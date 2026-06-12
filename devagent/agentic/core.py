"""ReAct Agentic Loop — the core of DevAgent V2.

Implements the Think → Act → Observe cycle with LLM-driven decision making,
tool execution, context management, and structured termination.

Refactored architecture:
  _execute_single_step()  → shared step execution (eliminates duplication)
  _handle_step_result()   → observation processing + side effects
  _run_agentic_loop()     → main loop, used by both execute_async and _run_standard_loop
"""

import os
import json
import time
import asyncio
import re
from datetime import datetime
from typing import Optional

from .tools import ToolRegistry, ToolResult
from .events import EventBus, EventType, DevAgentEvent, ConsoleEmitter, FileLogger
from .state import AgentLoopState
from .context import (
    ContextManager, PhaseDetector,
    HallucinationGuard, ContextualToolFilter
)
from .fault_locator import FaultLocalizationPipeline, FaultReport
from .validation import InstantValidator
from .observability import (
    StreamingServer, HumanInTheLoop, TaskHistoryManager, TaskRecord
)
from .planning import PlannerAgent, ExecutionPlan
from .experience import ExperienceStore, ExperienceInjector, ExperienceRecorder
from .sandbox import SandboxManager, ContainerSpec
from .verification import VerificationGate
from .interaction import (
    InteractionController, ProgressStreamer,
    UserCommand, CommandType,
    _set_active_controller,
)
from .session import SessionManager
from ..agent_core.llm_client import LLMClient
from ..agent_core.config_loader import load_config, get_llm_config


# ============================================================================
# Termination Checker
# ============================================================================

class TerminationChecker:
    """Checks if the agentic loop should stop."""

    def __init__(self, max_iterations: int = 50, stuck_window: int = 25):
        self.max_iterations = max_iterations
        self.stuck_window = stuck_window

    def check(self, state: AgentLoopState,
              last_observation: Optional[dict] = None) -> tuple[bool, str]:
        if last_observation:
            if last_observation.get("structured", {}).get("submitted"):
                return True, "agent_submitted"
            output = (last_observation.get("output", "") or "").lower()
            if "cannot complete" in output or "unable to fix" in output:
                return True, "agent_declared_failure"

        tr = state.test_results
        if tr and tr.get("collected", 0) > 0:
            if tr.get("failed", 0) == 0 and tr.get("errors", 0) == 0:
                return True, "all_tests_pass"

        if state.current_iteration >= state.max_iterations:
            return True, "max_iterations_reached"

        if self._is_stuck(state):
            return True, "stuck_no_progress"

        if self._consecutive_errors(state, window=3):
            return True, "consecutive_errors"

        return False, ""

    def _is_stuck(self, state: AgentLoopState) -> bool:
        if state.current_iteration < self.stuck_window:
            return False
        recent = state.action_history[-self.stuck_window:]
        productive = {
            # Code modification
            "file_edit", "file_write", "submit", "test_run", "shell_run",
            # Pipeline tools (requirements/design/code generation are productive)
            "plan_task", "analyze_requirements", "design_architecture",
            "generate_code", "generate_tests", "debug_issue", "repair_code",
            "generate_report",
            # Interactive tools (review submission = progress)
            "request_review", "ask_user",
            # Discovery that returns results (grep/find are productive)
            "grep_text", "grep_ast", "find_symbol",
        }
        return all(a.get("tool", "") not in productive for a in recent)

    def _consecutive_errors(self, state: AgentLoopState, window: int = 3) -> bool:
        if len(state.observation_history) < window:
            return False
        recent = state.observation_history[-window:]
        return all(not o.get("success", True) for o in recent)


# ============================================================================
# Action Parser
# ============================================================================

class ActionParser:
    """Parses LLM output into structured actions."""

    PATTERN = re.compile(
        r'THOUGHT:\s*(.+?)\s*\n\s*ACTION:\s*(\w+)\s*\n\s*PARAMS:\s*(\{.+?\})\s*$',
        re.DOTALL | re.IGNORECASE
    )

    @classmethod
    def parse(cls, text: str) -> Optional[dict]:
        m = cls.PATTERN.search(text.strip())
        if m:
            try:
                params = json.loads(m.group(3))
            except json.JSONDecodeError:
                candidates = re.findall(r'\{[^{}]*\}', m.group(3))
                params = json.loads(candidates[0]) if candidates else {}
            return {
                "thought": m.group(1).strip(),
                "tool": m.group(2).strip(),
                "params": params
            }

        action_m = re.search(r'ACTION:\s*(\w+)', text, re.IGNORECASE)
        if action_m:
            return {
                "thought": text[:200].strip(),
                "tool": action_m.group(1),
                "params": cls._extract_params(text)
            }
        return None

    @staticmethod
    def _extract_params(text: str) -> dict:
        json_match = re.search(r'\{[^{}]*"path"[^{}]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        params = {}
        for m in re.finditer(r'(\w+)\s*[:=]\s*"([^"]*)"', text):
            params[m.group(1)] = m.group(2)
        for m in re.finditer(r'(\w+)\s*[:=]\s*(\d+)', text):
            if m.group(1) not in params:
                params[m.group(1)] = int(m.group(2))
        return params if params else {}


# ============================================================================
# DevAgentCore — Refactored with shared step execution
# ============================================================================

class DevAgentCore:
    """The main ReAct agentic loop — refactored for clarity and accuracy."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        llm_config = get_llm_config(self.config)
        self.llm = LLMClient(llm_config)
        self.workflow_config = self.config.get("workflow", {})
        self.agentic_config = self.workflow_config.get("agentic", {})

        self.max_iterations = self.agentic_config.get("max_iterations", 100)
        self.stuck_window = self.agentic_config.get("stuck_window", 30)

        self.event_bus = EventBus()
        self._setup_subscribers()

        # Enhancement systems (lazy init where appropriate)
        self.streaming = StreamingServer()
        self.hitl = HumanInTheLoop()
        self.history = TaskHistoryManager()
        self.experience_store = ExperienceStore()
        self.experience_injector = ExperienceInjector()
        self.experience_recorder = ExperienceRecorder(self.experience_store)
        self.sandbox_mgr = SandboxManager(self.config)
        self._sandbox_active = False
        self.verification_gate: Optional[VerificationGate] = None

        # === Interactive mode — real-time user interaction ===
        self.interaction: Optional[InteractionController] = None
        self.progress_streamer: Optional[ProgressStreamer] = None
        self.session_mgr: Optional[SessionManager] = None
        self._interactive_mode: str = "off"  # off / full / approval_only / observe_only

    def enable_interaction(self, mode: str = "full"):
        """Enable real-time interactive mode with user feedback channels.

        Must be called BEFORE execute() or execute_async().

        Args:
            mode: Interaction mode
                - "full":       Full interaction (approval + dialogue + streaming)
                - "approval":   Approval gates only (block dangerous ops)
                - "observe":    Streaming only (read-only observation)
                - "off":        Disable interaction (default)
        """
        self._interactive_mode = mode
        self.session_mgr = SessionManager()

        enable_approval = mode in ("full", "approval")
        enable_dialogue = mode == "full"
        enable_streaming = mode in ("full", "observe", "approval")

        self.interaction = InteractionController(
            event_bus=self.event_bus,
            streaming_server=self.streaming,
            hitl_manager=self.hitl,
            session_manager=self.session_mgr,
            llm_client=self.llm,
            enable_approval=enable_approval,
            enable_dialogue=enable_dialogue,
            enable_streaming=enable_streaming,
            enable_review_gate=enable_approval,  # review gate requires approval mode
        )

        # Apply config overrides from agentic config
        ac = self.agentic_config
        if ac.get("auto_approve_destructive", False):
            from .interaction import ApprovalType
            self.interaction.auto_approve_policy[ApprovalType.DESTRUCTIVE_SHELL] = True
        if ac.get("auto_approve_large_edit", False):
            from .interaction import ApprovalType
            self.interaction.auto_approve_policy[ApprovalType.LARGE_EDIT] = True
        if ac.get("max_questions"):
            self.interaction.max_questions_per_task = ac["max_questions"]

        self.progress_streamer = ProgressStreamer(
            streaming_server=self.streaming,
            session_manager=self.session_mgr,
            snapshot_interval_ms=ac.get("snapshot_interval_ms", 500),
        )

        # Register for ask_user tool access (globally)
        _set_active_controller(self.interaction)

    def _setup_subscribers(self):
        self.event_bus.subscribe_all(ConsoleEmitter(verbose=False), mode="sync")
        self.event_bus.subscribe_all(FileLogger(), mode="sync")

    # ==================================================================
    # Public API
    # ==================================================================

    def execute(self, task_description: str, workspace: str = ".",
                language: str = "python", max_iterations: int = None) -> AgentLoopState:
        return asyncio.run(self.execute_async(
            task_description, workspace, language, max_iterations
        ))

    def run_pipeline(self, task_description: str, workspace: str = ".",
                     output_root: str = None, language: str = "python") -> "PipelineState":
        """Execute using Plan-Execute-Gate pipeline (stable, code-driven flow).

        Unlike the free-form ReAct loop, this follows a fixed phase sequence:
        requirements → design → code → test → delivery.

        Each phase: execute tool → deterministic check → human review → next phase.
        DeepSeek only generates content — code controls the flow.

        Returns PipelineState with results from all phases.
        """
        from .pipeline_runner import PipelineRunner, PipelineState

        # Build tools with pipeline support
        tools = ToolRegistry.create_default(
            llm_client=self.llm, include_pipeline=True
        )

        # Get thread channel if interaction is enabled
        channel = self.interaction._thread_channel if self.interaction else None

        runner = PipelineRunner(
            llm_client=self.llm,
            tools=tools,
            thread_channel=channel,
            workspace=workspace,
        )

        # Wire simple print-based callbacks (no asyncio needed)
        def on_phase(name, display):
            print(f"[Pipeline] Phase: {display} ({name})", flush=True)

        def on_review(phase, display, summary, files):
            print(f"[Pipeline] Review requested: {display} ({len(files)} files)", flush=True)

        runner.set_callbacks(
            on_phase_start=on_phase,
            on_review_requested=on_review,
        )

        return runner.run(task_description, workspace, output_root=output_root, language=language)

    async def execute_async(self, task_description: str, workspace: str = ".",
                             language: str = "python",
                             max_iterations: int = None) -> AgentLoopState:
        """Full featured execution with planning, experience, sandbox, and verification."""
        max_iter = max_iterations or self.max_iterations

        state = AgentLoopState(
            task_type="agentic",
            workspace=os.path.abspath(workspace),
            task_description=task_description,
            language=language,
            max_iterations=max_iter,
            status="RUNNING"
        )

        # === Bind controller to task_id for interactive mode ===
        if self.session_mgr and self.interaction:
            self.session_mgr.bind_controller(state.task_id, self.interaction)

        # Sandbox setup
        if self.agentic_config.get("enable_sandbox", False):
            try:
                spec = ContainerSpec(workspace_mount=state.workspace)
                await self.sandbox_mgr.create_for_task(state.task_id, state.workspace, spec)
                self._sandbox_active = True
            except Exception:
                self._sandbox_active = False

        self._task_start_time = time.time()
        await self.event_bus.publish(DevAgentEvent(
            type=EventType.TASK_STARTED, task_id=state.task_id,
            data={"task": task_description[:200], "workspace": state.workspace}
        ))
        await self.streaming.push_event({
            "type": "task.started", "task_id": state.task_id,
            "data": {"task": task_description[:200]},
            "timestamp": datetime.now().isoformat(), "iteration": 0
        })

        tools = ToolRegistry.create_default(
            llm_client=self.llm, include_pipeline=True
        )
        termination = TerminationChecker(max_iter, self.stuck_window)
        context_mgr = ContextManager(state.workspace)

        # Planning Phase
        if self.agentic_config.get("enable_planning", True):
            planner = PlannerAgent(self.llm, state.workspace)
            repo_map = context_mgr.get_repo_map()
            plan = planner.plan(task_description, repo_map)
            if plan:
                state._execution_plan = plan

        # Experience retrieval
        if self.agentic_config.get("enable_experience_library", True):
            similar = self.experience_store.retrieve({
                "error_message": task_description, "task": task_description,
                "language": language, "project": os.path.basename(state.workspace),
            }, top_k=3)
            if similar:
                state._injected_experiences = [
                    {"id": e.id, "bug_signature": e.bug_signature[:150],
                     "fix_description": e.fix_description[:200],
                     "fix_patch": e.fix_patch[:500]} for e in similar
                ]

        try:
            await self._run_agentic_loop(state, tools, termination, context_mgr,
                                         task_description)
        except Exception as e:
            state.status = "FAILED"
            await self.event_bus.publish(DevAgentEvent(
                type=EventType.TASK_FAILED, task_id=state.task_id,
                iteration=state.current_iteration,
                data={"reason": f"Exception: {e}", "iterations": state.current_iteration}
            ))

        # Post-execution
        state.save()
        self._record_completion(state, task_description)
        return state

    # ==================================================================
    # Core Loop — shared by both execute_async and _run_standard_loop
    # ==================================================================

    async def _run_agentic_loop(self, state: AgentLoopState,
                                 tools: ToolRegistry,
                                 termination: TerminationChecker,
                                 context_mgr: ContextManager,
                                 task_description: str):
        """Main agentic loop — drives the Think→Act→Observe cycle.

        Includes interactive checkpoints for user command processing,
        pause/resume, and progressive state publishing.
        """
        tool_descriptions = tools.get_descriptions()
        actions = ActionParser()

        # Track injected contexts for the prompt
        if not hasattr(state, '_injected_contexts'):
            state._injected_contexts = []
        if not hasattr(state, '_forced_next_tool'):
            state._forced_next_tool = ""
            state._forced_reason = ""
            state._forced_feedback = ""

        while not state.is_terminal():
            # ================================================================
            # INTERACTIVE CHECKPOINT 1: Process user commands
            # ================================================================
            if self.interaction:
                await self.interaction.check_commands(state)

            # ================================================================
            # INTERACTIVE CHECKPOINT 2: Handle pause state
            # ================================================================
            if self.interaction and self.interaction.is_paused:
                await self._push_interaction_event(state, "control.paused", {
                    "iteration": state.current_iteration,
                })
                while self.interaction.is_paused:
                    await asyncio.sleep(0.5)
                    await self.interaction.check_commands(state)
                    if self.interaction.is_aborted:
                        state.status = "ABORTED"
                        await self._push_interaction_event(state, "control.aborted", {
                            "iteration": state.current_iteration,
                        })
                        break
                if state.is_terminal():
                    break
                # Publish resume
                await self._push_interaction_event(state, "control.resumed", {})

            # ================================================================
            # INTERACTIVE CHECKPOINT 3: Check for abort
            # ================================================================
            if self.interaction and self.interaction.is_aborted:
                state.status = "ABORTED"
                await self._push_interaction_event(state, "control.aborted", {
                    "iteration": state.current_iteration,
                })
                break

            state.current_iteration += 1

            await self.event_bus.publish(DevAgentEvent(
                type=EventType.AGENT_THINKING, task_id=state.task_id,
                iteration=state.current_iteration, data={"phase": "thinking"}
            ))

            # === FORCED ACTION: Skip LLM, execute forced tool directly ===
            # This is the ONLY reliable way to enforce post-review actions.
            # LLM prompt-based overrides are unreliable with weaker models.
            forced = getattr(state, '_forced_next_tool', '')
            if forced:
                force_tool = forced
                force_reason = getattr(state, '_forced_reason', '')
                force_params = getattr(state, '_force_params', {}) or {}
                force_phase = getattr(state, '_forced_phase', '')

                # Clear immediately — only execute once
                state._forced_next_tool = ""
                state._forced_reason = ""
                state._force_params = {}
                state._forced_phase = ""

                # Build action programmatically — no LLM involved
                action = {
                    "thought": f"[SYSTEM] Review response: {force_reason}. Forced tool: {force_tool}",
                    "tool": force_tool,
                    "params": force_params,
                }

                print(f"[CORE] FORCED ACTION: {force_tool} (reason={force_reason})", flush=True)

                state.add_action(action)

                # Execute the forced tool
                result = await tools.execute(force_tool, force_params, state.workspace)

                state.add_observation({
                    "success": result.success,
                    "output": (result.output or "")[:500],
                    "error": result.error or "",
                    "structured": result.structured or {},
                })

                # Side effects
                self._handle_step_side_effects(state, action, result, observation={}, context_mgr=context_mgr)
                if self.interaction:
                    await self.interaction.post_action(action, result, state)
                if self.progress_streamer:
                    await self.progress_streamer.publish(state)

                # After forced tool runs, re-enter next iteration
                # (LLM takes over from here — may call request_review)
                continue

            # Build context and get LLM decision (normal flow)
            messages = context_mgr.build_messages(task_description, state, tool_descriptions)

            # Inject user-provided contexts into the prompt
            if hasattr(state, '_injected_contexts') and state._injected_contexts:
                recent = state._injected_contexts[-3:]  # last 3
                has_directive = any(
                    "REVISE:" in c.get("content", "") or
                    "REJECTED" in c.get("content", "") or
                    "NEXT STEP:" in c.get("content", "") or
                    c.get("source", "") in ("human_reviewer", "review_response")
                    for c in recent
                )
                if has_directive:
                    # Put as the FIRST user message — highest priority
                    directive_text = "## ⛔ STOP. Read this directive BEFORE doing anything else.\n\n" + "\n\n".join(
                        c.get("content", "") for c in recent if c.get("content", "")
                    ) + "\n\n## OBEY the directive above. Call the specified tool NOW."
                    messages.insert(1, {"role": "user", "content": directive_text})
                else:
                    injected_text = "\n\n## User-Supplied Context\n" + "\n".join(
                        f"- [{c.get('source', 'user')} @ {c.get('timestamp', '')}]: {c.get('content', '')}"
                        for c in recent
                    )
                    messages.append({"role": "user", "content": injected_text})

            llm_response = self._call_llm(messages, tools, state)
            action = actions.parse(llm_response)

            if not action:
                state.add_action({"tool": "parse_error", "params": {},
                                 "raw_response": llm_response[:500]})
                state.add_observation({"success": False, "error": "Could not parse action"})
                continue

            # ================================================================
            # INTERACTIVE CHECKPOINT 4: Pre-action hooks (approval gate)
            # ================================================================
            if self.interaction:
                approved = await self.interaction.pre_action(action, state)
                if not approved:
                    state.add_action(action)
                    state.add_observation({
                        "success": False,
                        "error": "Action blocked by user approval gate"
                    })
                    continue

            # Execute the step (shared logic)
            observation = await self._execute_single_step(state, action, tools, context_mgr)

            # ================================================================
            # INTERACTIVE CHECKPOINT 5: Post-action hooks + progress
            # ================================================================
            if self.interaction:
                await self.interaction.post_action(action, observation, state)
            if self.progress_streamer:
                await self.progress_streamer.publish(state)

            # Check termination
            should_stop, reason = termination.check(state, observation)
            if should_stop:
                state.status = ("COMPLETED"
                               if reason in ("agent_submitted", "all_tests_pass")
                               else "FAILED")
                await self.event_bus.publish(DevAgentEvent(
                    type=EventType.TASK_COMPLETED if state.status == "COMPLETED"
                         else EventType.TASK_FAILED,
                    task_id=state.task_id, iteration=state.current_iteration,
                    data={"reason": reason, "iterations": state.current_iteration}
                ))
                break

        # ================================================================
        # END OF LOOP: Cleanup interaction resources
        # ================================================================
        if self.session_mgr and self.interaction:
            self.session_mgr.unbind_controller(state.task_id)

    # ==================================================================
    # Single Step Execution — eliminates 100+ lines of duplication
    # ==================================================================

    async def _execute_single_step(self, state: AgentLoopState,
                                    action: dict, tools: ToolRegistry,
                                    context_mgr: ContextManager) -> dict:
        """Execute one complete Think→Act→Observe step.

        Returns the observation dict. Side-effects: updates state, publishes events,
        runs validation, triggers fault localization.
        """
        # Validate edits before executing
        if action["tool"] == "file_edit":
            target = action["params"].get("path", "")
            if target:
                valid, msg = HallucinationGuard.validate_edit_target(
                    target, state.workspace)
                if not valid:
                    state.add_action(action)
                    obs = {"success": False, "error": msg}
                    state.add_observation(obs)
                    return obs

        # Publish decision
        await self.event_bus.publish(DevAgentEvent(
            type=EventType.AGENT_DECIDED, task_id=state.task_id,
            iteration=state.current_iteration,
            data={"thought": action["thought"][:200],
                  "tool": action["tool"],
                  "params_summary": str(action["params"])[:200]}
        ))
        state.add_action(action)

        # Execute tool
        await self.event_bus.publish(DevAgentEvent(
            type=EventType.TOOL_STARTED, task_id=state.task_id,
            iteration=state.current_iteration,
            data={"tool": action["tool"], "params": action["params"]}
        ))

        t_start = time.time()
        result = await tools.execute(action["tool"], action["params"], state.workspace)
        duration_ms = (time.time() - t_start) * 1000

        observation = {
            "success": result.success, "output": result.output,
            "error": result.error, "structured": result.structured,
            "duration_ms": duration_ms,
        }

        # Post-execution side effects
        self._handle_step_side_effects(state, action, result, observation, context_mgr)

        # Publish result
        event_type = EventType.TOOL_COMPLETED if result.success else EventType.TOOL_ERROR
        await self.event_bus.publish(DevAgentEvent(
            type=event_type, task_id=state.task_id,
            iteration=state.current_iteration,
            data={"tool": action["tool"], "success": result.success,
                  "output": result.output[:300], "error": result.error[:200],
                  "duration_ms": duration_ms}
        ))
        await self.streaming.push_event({
            "type": event_type.value, "task_id": state.task_id,
            "iteration": state.current_iteration,
            "data": {"tool": action["tool"], "success": result.success,
                     "duration_ms": duration_ms},
            "timestamp": datetime.now().isoformat(),
        })

        state.add_observation(observation)
        return observation

    def _build_force_params(self, next_tool: str, phase: str, state) -> dict:
        """Build parameters for a forced tool call based on the previous tool's output."""
        params = {}
        # Pass previous structured output as input to next pipeline tool
        if next_tool == "design_architecture":
            params["requirements"] = getattr(state, 'requirements', {})
        elif next_tool == "generate_code":
            params["design_artifacts"] = getattr(state, 'design_artifacts', {})
        elif next_tool == "test_run":
            params["paths"] = ["03_implementation/tests/", "tests/"]
        elif next_tool == "generate_report":
            pass  # auto-builds from state
        elif next_tool == "analyze_requirements":
            params["input_content"] = getattr(state, '_forced_feedback', '') or "Redo with fixes"
        elif next_tool == "design_architecture":
            params["requirements"] = getattr(state, 'requirements', {})
        elif next_tool == "repair_code":
            params["debug_analysis"] = getattr(state, 'debug_analysis', {})
        return params

    def _handle_step_side_effects(self, state: AgentLoopState,
                                   action: dict, result: ToolResult,
                                   observation: dict,
                                   context_mgr: ContextManager):
        """Process side effects after a tool execution."""
        tool = action["tool"]

        # File modifications
        if result.success and tool in ("file_edit", "file_write"):
            path = action["params"].get("path", "")
            if path and path not in state.modified_files:
                state.modified_files.append(path)
                context_mgr.on_file_modified(path)

        # === REVIEW RESPONSE INJECTION ===
        if tool == "request_review" and result.success:
            structured = result.structured or {}
            if structured.get("approved") or structured.get("auto_approved"):
                nt = structured.get("next_tool", "")
                if nt:
                    state._forced_next_tool = nt
                    state._forced_reason = "review_approved"
                    state._force_params = self._build_force_params(nt, phase, state)

            elif structured.get("revise"):
                nt = structured.get("next_tool", "")
                fb = structured.get("feedback", "")
                if nt:
                    state._forced_next_tool = nt
                    state._forced_reason = "review_revise"
                    state._forced_feedback = fb
                    state._force_params = self._build_force_params(nt, phase, state)

        # Instant validation after edits
        if (result.success and tool in ("file_edit", "file_write")
                and self.agentic_config.get("enable_instant_validation", True)):
            path = action["params"].get("path", "")
            if path:
                try:
                    val_result = asyncio.get_event_loop().run_until_complete(
                        InstantValidator(state.workspace).validate(path))
                except RuntimeError:
                    val_result = None  # Already in event loop
                    # Create a task instead
                    asyncio.ensure_future(self._validate_async(state, path, observation))
                    return
                if val_result and val_result.summary():
                    observation["output"] = result.output + "\n\n" + val_result.summary()

        # Test results
        if tool == "test_run":
            state.test_results = result.structured
            if result.structured.get("failed", 0) > 0:
                asyncio.ensure_future(
                    self._run_fault_localization_and_enhance(state, observation, result)
                )

    async def _validate_async(self, state: AgentLoopState, path: str, observation: dict):
        try:
            val_result = await InstantValidator(state.workspace).validate(path)
            if val_result.summary():
                observation["output"] = observation.get("output", "") + \
                                        "\n\n" + val_result.summary()
        except Exception:
            pass

    async def _run_fault_localization_and_enhance(self, state: AgentLoopState,
                                                    observation: dict, result: ToolResult):
        fl_report = await self._run_fault_localization(state)
        if fl_report and fl_report.bug_file:
            loc_text = (
                f"\n\n=== FAULT LOCALIZATION ===\n"
                f"Bug: {fl_report.bug_file}:{fl_report.bug_line} in {fl_report.bug_function}()\n"
                f"Confidence: {fl_report.confidence}\n"
                f"Root cause: {fl_report.root_cause[:500]}\n"
                f"Suggested fix: {fl_report.fix_suggestion[:300]}\n"
                f"SBFL top-3: " + ", ".join(
                    f"{s.file}:{s.line}" for s in fl_report.top_suspects[:3])
            )
            observation["output"] = result.output + loc_text

    # ==================================================================
    # Fault Localization
    # ==================================================================

    async def _run_fault_localization(self, state: AgentLoopState,
                                       source_path: str = "src/",
                                       test_path: str = "tests/") -> Optional[FaultReport]:
        if not self.agentic_config.get("enable_fault_localization", True):
            return None
        tr = state.test_results
        if not tr or tr.get("failed", 0) == 0:
            return None
        try:
            pipeline = FaultLocalizationPipeline(state.workspace, self.llm)
            return await pipeline.localize(test_path=test_path, source_path=source_path,
                error_info={"message": f"{tr.get('failed', '?')} tests failed"})
        except Exception:
            return None

    # ==================================================================
    # Scoped Loop (used by PlanExecutor / multi_agent)
    # ==================================================================

    async def _run_standard_loop(self, task_description: str,
                                  state: AgentLoopState) -> AgentLoopState:
        """Simplified loop without planning/experience/sandbox overhead."""
        tools = ToolRegistry.create_default(
            llm_client=self.llm, include_pipeline=True
        )
        termination = TerminationChecker(state.max_iterations, self.stuck_window)
        context_mgr = ContextManager(state.workspace)

        await self._run_agentic_loop(state, tools, termination, context_mgr, task_description)
        return state

    # ==================================================================
    # Interaction Helpers
    # ==================================================================

    async def _push_interaction_event(self, state: AgentLoopState,
                                       event_type: str, data: dict):
        """Push an event to streaming and event bus for interactive mode."""
        if self.streaming:
            await self.streaming.push_event({
                "type": event_type,
                "task_id": state.task_id,
                "iteration": state.current_iteration,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            })
        if self.event_bus:
            await self.event_bus.publish(DevAgentEvent(
                type=EventType.AGENT_THINKING,  # reuse for now
                task_id=state.task_id,
                iteration=state.current_iteration,
                data={"interaction_event": event_type, **data}
            ))

    # ==================================================================
    # LLM Calling
    # ==================================================================

    def _call_llm(self, messages: list[dict], tools: ToolRegistry,
                   state: AgentLoopState) -> str:
        schemas = tools.get_openai_schemas()
        phase = PhaseDetector.detect(state)
        if self.agentic_config.get("enable_tool_filtering", True):
            allowed = ContextualToolFilter.filter(phase)
            schemas = [s for s in schemas if s["function"]["name"] in allowed]

        if self.llm.provider == "openai":
            return self._call_openai_fc(messages, schemas)
        else:
            return self._call_text_based(messages)

    def _call_openai_fc(self, messages: list[dict], schemas: list[dict]) -> str:
        import requests as req
        headers = {"Authorization": f"Bearer {self.llm.api_key}",
                    "Content-Type": "application/json"}
        payload = {
            "model": self.llm.model, "messages": messages,
            "temperature": self.llm.temperature, "max_tokens": self.llm.max_tokens,
            "tools": schemas, "tool_choice": "auto"
        }
        try:
            resp = req.post(f"{self.llm.api_base}/chat/completions",
                           headers=headers, json=payload, timeout=180)
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                tool_name = tc["function"]["name"]
                params = json.loads(tc["function"]["arguments"])
                thought = msg.get("content", "") or "Executing tool to advance the task"
                return f"THOUGHT: {thought}\nACTION: {tool_name}\nPARAMS: {json.dumps(params)}"
            return msg.get("content", "")
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

    def _call_text_based(self, messages: list[dict]) -> str:
        combined = ""
        for m in messages:
            role_prefix = f"[{m['role'].upper()}]" if m['role'] == 'system' else ""
            combined += f"{role_prefix}\n{m['content']}\n\n"
        try:
            return self.llm.chat(messages=[{"role": "user", "content": combined}])
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

    # ==================================================================
    # Post-Execution
    # ==================================================================

    def _record_completion(self, state: AgentLoopState, task_description: str):
        if self.agentic_config.get("enable_experience_library", True):
            self.experience_recorder.record_from_state(state)

        duration = time.time() - self._task_start_time if self._task_start_time > 0 else 0
        tr = state.test_results or {}
        record = TaskRecord(
            task_id=state.task_id, task_type=state.task_type,
            task_description=task_description, status=state.status,
            iterations=state.current_iteration, files_modified=state.modified_files,
            test_passed=tr.get("passed", 0), test_failed=tr.get("failed", 0),
            errors=[{"phase": "execution", "message": f"Status: {state.status}"}]
                   if state.status == "FAILED" else [],
            duration_sec=duration,
        )
        self.history.record_task(record)

        # Cleanup sandbox
        if self._sandbox_active:
            try:
                asyncio.get_event_loop().run_until_complete(
                    self.sandbox_mgr.destroy_for_task(state.task_id))
            except (RuntimeError, Exception):
                pass


# ============================================================================
# Convenience function
# ============================================================================

def run_agentic(task_description: str, workspace: str = ".",
                language: str = "python", config_path: str = None) -> AgentLoopState:
    core = DevAgentCore(config_path)
    return core.execute(task_description, workspace, language)
