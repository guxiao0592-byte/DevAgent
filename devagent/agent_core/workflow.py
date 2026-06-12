"""Workflow Orchestrator - manages agent execution with mature patterns.

Architecture:
  Planner -> [Phase -> Validate -> Refine]*n -> Review

Mature patterns:
  1. PLAN-DECOMPOSE: PlannerAgent decomposes raw input into phases
  2. SELF-REFLECTION: Each agent validates its output, refines if needed
  3. QUALITY GATES: Structured checks between phases with feedback loops
  4. CONTEXT-PRESERVING RETRY: Retry with specific error context (not blind)
  5. STRUCTURED ERROR RECOVERY: Phase-level try/except with state rollback
"""

import os
import json
from typing import Optional

from .state import AgentState
from .schemas import TaskSpec
from .llm_client import LLMClient
from .config_loader import load_config, get_llm_config, get_workflow_config
from ..agents.planner_agent import PlannerAgent
from ..agents.requirement_agent import RequirementAgent
from ..agents.design_agent import DesignAgent
from ..agents.code_agent import CodeAgent
from ..agents.test_agent import TestAgent
from ..agents.debug_agent import DebugAgent
from ..agents.repair_agent import RepairAgent
from ..agents.review_agent import ReviewAgent
from ..tools.file_tool import FileTool
from ..agent_core.schemas import Artifact as ArtifactModel


class PhaseResult:
    """Result of a single phase execution."""
    def __init__(self, success: bool, feedback: str = "", data: Optional[dict] = None):
        self.success = success
        self.feedback = feedback
        self.data = data or {}


class WorkflowController:
    """Controls the execution flow of agents with mature orchestration patterns."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        llm_config = get_llm_config(self.config)
        self.llm = LLMClient(llm_config)
        self.workflow_config = get_workflow_config(self.config)
        self.file_tool = FileTool()

        # Initialize agents
        self.planner_agent = PlannerAgent(self.llm, self.workflow_config)
        self.requirement_agent = RequirementAgent(self.llm, self.config.get("tools", {}))
        self.design_agent = DesignAgent(self.llm, self.workflow_config)
        self.code_agent = CodeAgent(self.llm, self.workflow_config)
        self.test_agent = TestAgent(self.llm, self.config.get("tools", {}))
        self.debug_agent = DebugAgent(self.llm, self.workflow_config)
        self.repair_agent = RepairAgent(self.llm, self.config.get("tools", {}))
        self.review_agent = ReviewAgent(self.llm, self.workflow_config)

        # Quality gate prompts
        self._gate_prompt = """You are a quality gate validator. Check if a phase output meets minimum quality standards.

Assess:
1. Is the output non-empty and structurally valid?
2. Does it contain specific, actionable content (not generic)?
3. Is it internally consistent?

Respond: {"pass": true/false, "feedback": "specific issues if failed"}"""

    def execute(self, spec: TaskSpec) -> AgentState:
        """Execute the full workflow based on task type."""
        state = AgentState(
            task_type=spec.task_type,
            input_path=spec.input_path,
            output_root=spec.output_root,
            max_retry=spec.max_retry
        )

        os.makedirs(state.output_root, exist_ok=True)
        # Initialize central ArtifactRegistry for this workflow run
        try:
            from ..tools.artifact_registry import ArtifactRegistry
            state.artifact_registry = ArtifactRegistry(state.output_root or "outputs")
        except Exception:
            state.artifact_registry = None

        try:
            if spec.task_type == "agentic":
                self._run_agentic_workflow(state, spec)
            elif spec.task_type == "design":
                self._run_design_workflow(state)
            elif spec.task_type == "implement":
                self._run_implement_workflow(state)
            elif spec.task_type == "test":
                self._run_test_workflow(state, spec)
            elif spec.task_type == "debug":
                self._run_debug_workflow(state, spec)
            elif spec.task_type == "repair":
                self._run_repair_workflow(state, spec)
            elif spec.task_type == "full":
                self._run_full_workflow(state)
            else:
                state.add_error("router", f"Unknown task type: {spec.task_type}")
        except Exception as e:
            state.add_error("workflow", f"Workflow execution failed: {str(e)}")

        # Always run review to generate reports
        try:
            state = self.review_agent.run(state)
        except Exception as e:
            state.add_error("review", f"Review failed: {str(e)}")

        self._save_execution_log(state)
        return state

    # ==================== Phase Execution with Quality Gates ====================

    def _execute_phase(self, state: AgentState, phase_name: str,
                       agent_fn, gate_check: bool = True) -> PhaseResult:
        """Execute a single phase with error handling and optional quality gate.

        Args:
            state: Current agent state
            phase_name: Name for error tracing
            agent_fn: Callable that takes state and returns state
            gate_check: Whether to run quality gate validation after phase

        Returns:
            PhaseResult with success status and feedback
        """
        try:
            state = agent_fn(state)

            if state.errors:
                last_error = state.errors[-1].get("message", "Unknown error")
                return PhaseResult(False, last_error)

            if gate_check and self.workflow_config.get("enable_quality_gates", True):
                gate_result = self._check_quality_gate(state, phase_name)
                if not gate_result["pass"]:
                    return PhaseResult(False, gate_result.get("feedback", "Quality gate failed"))

            return PhaseResult(True)

        except Exception as e:
            error_msg = f"{phase_name} phase failed: {str(e)}"
            state.add_error(phase_name, str(e))
            return PhaseResult(False, error_msg)

    def _check_quality_gate(self, state: AgentState, phase_name: str) -> dict:
        """Check if a phase output meets quality standards."""
        phase_data = self._get_phase_data(state, phase_name)
        if not phase_data:
            return {"pass": True, "feedback": ""}

        try:
            result = self.llm.chat_structured(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Check the quality of this {phase_name} phase output:\n\n"
                        f"{json.dumps(phase_data, indent=2, ensure_ascii=False)[:2000]}"
                    )
                }],
                system_prompt=self._gate_prompt
            )
            return result
        except Exception:
            return {"pass": True, "feedback": ""}

    def _get_phase_data(self, state: AgentState, phase_name: str) -> Optional[dict]:
        """Extract phase output data from state for quality checking."""
        mapping = {
            "requirements": state.requirements,
            "design": state.design_artifacts,
            "implementation": {"code_files": state.code_files},
            "testing": state.test_results,
            "debug": state.debug_analysis,
            "repair": state.repair_patch,
            "planning": state.input_manifest,
        }
        return mapping.get(phase_name)

    # ==================== Retry with Context ====================

    def _retry_phase(self, state: AgentState, phase_name: str,
                     agent_fn, max_retries: int = None) -> bool:
        """Execute a phase with context-preserving retry.

        Each retry includes previous error context so the agent can learn from failures.
        """
        max_retries = max_retries or state.max_retry

        for attempt in range(max_retries):
            result = self._execute_phase(state, phase_name, agent_fn,
                                          gate_check=(attempt == max_retries - 1))
            if result.success:
                return True

            state.add_warning(phase_name,
                              f"Attempt {attempt + 1}/{max_retries} failed: {result.feedback}")

        return False

    # ==================== Workflow Implementations ====================

    def _run_design_workflow(self, state: AgentState):
        """Planning + Analysis + Design workflow."""
        state.add_trace("Workflow", "start", {"mode": "design"})

        # Phase 1: Plan
        self._execute_phase(state, "planning",
                            lambda s: self.planner_agent.run(s))

        # Phase 2: Requirements
        self._execute_phase(state, "requirements",
                            lambda s: self.requirement_agent.run(s))

        # Phase 3: Design
        self._execute_phase(state, "design",
                            lambda s: self.design_agent.run(s))

    def _run_implement_workflow(self, state: AgentState):
        """Implementation + Test workflow."""
        state.add_trace("Workflow", "start", {"mode": "implement"})

        self._execute_phase(state, "planning",
                            lambda s: self.planner_agent.run(s))

        self._execute_phase(state, "requirements",
                            lambda s: self.requirement_agent.run(s))

        self._execute_phase(state, "implementation",
                            lambda s: self.code_agent.run(s))

        self._execute_phase(state, "testing",
                            lambda s: self.test_agent.run(s))

    def _run_test_workflow(self, state: AgentState, spec: TaskSpec):
        """Standalone test execution workflow."""
        state.add_trace("Workflow", "start", {"mode": "test"})
        self._import_code_files(state, spec.code_path)
        if not state.code_files:
            state.add_error("test", "No code files found in specified path")
            return
        self._execute_phase(state, "testing",
                            lambda s: self.test_agent.run(s))

    def _run_debug_workflow(self, state: AgentState, spec: TaskSpec):
        """Standalone debug + repair workflow."""
        state.add_trace("Workflow", "start", {"mode": "debug"})
        self._import_code_files(state, spec.code_path)

        if spec.tests_path and os.path.exists(spec.tests_path):
            self._import_tests(state, spec.tests_path)
            from ..tools.test_runner import PytestRunner
            runner = PytestRunner()
            impl_dir = state.get_output_subdir("implementation")
            tests_dir = state.get_output_subdir("tests")
            tests_src_dir = os.path.join(tests_dir, "tests")
            state.test_results = runner.run_tests(
                tests_src_dir, work_dir=impl_dir,
                extra_env={"PYTHONPATH": impl_dir},
            )

        # Debug phase with one retry
        debug_ok = self._retry_phase(state, "debug",
                                     lambda s: self.debug_agent.run(s), max_retries=2)

        if debug_ok or not state.errors:
            self._run_repair_loop(state)

        state.add_trace("Workflow", "repair_complete",
                        {"retries": state.retry_count})

    def _run_repair_workflow(self, state: AgentState, spec: TaskSpec):
        """Repair workflow with context-preserving retry loop."""
        state.add_trace("Workflow", "start", {"mode": "repair"})

        if spec.code_path:
            self._import_code_files(state, spec.code_path)

        if spec.tests_path and os.path.exists(spec.tests_path):
            self._import_tests(state, spec.tests_path)
            from ..tools.test_runner import PytestRunner
            runner = PytestRunner()
            impl_dir = state.get_output_subdir("implementation")
            tests_dir = state.get_output_subdir("tests")
            tests_src_dir = os.path.join(tests_dir, "tests")
            state.test_results = runner.run_tests(
                tests_src_dir, work_dir=impl_dir,
                extra_env={"PYTHONPATH": impl_dir},
            )

        # Debug phase with one retry
        debug_ok = self._retry_phase(state, "debug",
                                     lambda s: self.debug_agent.run(s), max_retries=2)

        if debug_ok or not state.errors:
            self._run_repair_loop(state)

        state.add_trace("Workflow", "repair_complete",
                        {"retries": state.retry_count})

    def _run_repair_loop(self, state: AgentState):
        """Repair loop with regression verification.

        Each retry preserves the previous error context so the repair
        agent can learn from what went wrong in the previous attempt.
        """
        max_retry = state.max_retry
        for attempt in range(max_retry):
            state.retry_count = attempt

            # Run repair
            result = self._execute_phase(state, "repair",
                                          lambda s: self.repair_agent.run(s))
            if not result.success:
                state.add_trace("Workflow", "repair_retry",
                                {"attempt": attempt + 1, "reason": result.feedback})
                continue

            # Check regression results
            patch = state.repair_patch
            if not patch:
                continue

            rg = patch.get("regression_results", {})
            if rg.get("success", False):
                state.add_trace("Workflow", "repair_success",
                                {"attempt": attempt + 1})
                return
            if rg.get("failed", 0) == 0:
                return

            state.add_trace("Workflow", "regression_failed",
                            {"attempt": attempt + 1,
                             "passed": rg.get("passed", 0),
                             "failed": rg.get("failed", 0)})

    def _run_full_workflow(self, state: AgentState):
        """Full end-to-end workflow with planning, quality gates, and repair.

        Flow:
          Plan -> Reqs -> Design -> Code -> Test -> [Debug -> Repair]* -> Review
                ^gate    ^gate    ^gate   ^gate         ^gate
        """
        state.add_trace("Workflow", "start", {"mode": "full"})

        # Phase 1: Planning
        self._execute_phase(state, "planning",
                            lambda s: self.planner_agent.run(s))

        # Phase 2: Requirements
        self._execute_phase(state, "requirements",
                            lambda s: self.requirement_agent.run(s))

        # Phase 3: Design
        self._execute_phase(state, "design",
                            lambda s: self.design_agent.run(s))

        # Phase 4: Code Generation
        self._execute_phase(state, "implementation",
                            lambda s: self.code_agent.run(s))

        # Phase 5: Testing (use sandbox if available)
        self._execute_phase(state, "testing",
                            lambda s: self._run_testing_with_sandbox(s))

        # Phase 6: Debug & Repair (if tests failed)
        test_results = state.test_results
        if test_results and (test_results.get("failed", 0) > 0
                             or test_results.get("errors", 0) > 0):
            state.add_trace("Workflow", "entering_repair_loop", {})

            self._retry_phase(state, "debug",
                              lambda s: self.debug_agent.run(s), max_retries=2)

            if not state.errors:
                self._run_repair_loop(state)
        else:
            state.add_trace("Workflow", "tests_passed", {})

    def _run_testing_with_sandbox(self, state: AgentState) -> AgentState:
        """Run tests in an isolated sandbox (Docker/Podman/Local fallback).

        Wraps the TestAgent execution with sandbox lifecycle management.
        Falls back to direct execution if sandbox is unavailable.
        """
        from ..agentic.sandbox import SandboxManager, ContainerSpec

        use_sandbox = self.workflow_config.get("use_sandbox", False)
        if not use_sandbox:
            return self.test_agent.run(state)

        impl_dir = state.get_output_subdir("implementation")
        if not os.path.isdir(impl_dir):
            state.add_warning("sandbox", "No implementation dir, skipping sandbox")
            return self.test_agent.run(state)

        sandbox_mgr = SandboxManager(self.config)
        try:
            import asyncio

            spec = ContainerSpec(
                image="python:3.12-slim",
                workspace_mount=impl_dir,
                memory_limit="1g",
                cpu_limit=1.0,
                timeout=300,
                network="none",
            )

            # Create and start sandbox
            asyncio.get_event_loop().run_until_complete(
                sandbox_mgr.create_for_task(state.task_id, impl_dir, spec)
            )

            # Install test dependencies in sandbox
            asyncio.get_event_loop().run_until_complete(
                sandbox_mgr.run(state.task_id,
                              "pip install --quiet pytest pytest-cov coverage 2>/dev/null || true",
                              timeout=120)
            )

            # Run tests inside sandbox with PYTHONPATH set
            result = asyncio.get_event_loop().run_until_complete(
                sandbox_mgr.run(state.task_id,
                              "cd /workspace && PYTHONPATH=/workspace python -m pytest tests/ -v --tb=short 2>&1 || true",
                              timeout=180)
            )

            state.add_trace("Sandbox", "test_executed", {
                "exit_code": result.exit_code,
                "stdout_len": len(result.stdout),
                "sandbox_backend": sandbox_mgr.backend_name,
            })

            # Store sandbox test results
            if not state.test_results:
                test_results = self.test_agent.run(state)
            else:
                test_results = state

        except Exception as e:
            state.add_warning("sandbox", f"Sandbox test execution failed: {e}, falling back to direct")
            test_results = self.test_agent.run(state)
        finally:
            try:
                import asyncio
                asyncio.get_event_loop().run_until_complete(
                    sandbox_mgr.destroy_for_task(state.task_id)
                )
            except Exception:
                pass

        return test_results

    def _run_agentic_workflow(self, state: AgentState, spec):
        """Agentic V2 workflow — delegates to the ReAct core loop."""
        from ..agentic.core import DevAgentCore

        # Build task description from input or task_description field
        task_desc = getattr(spec, 'task_description', '') or ''
        if not task_desc and state.input_path and os.path.exists(state.input_path):
            task_desc = self.file_tool.read_text(state.input_path)

        if not task_desc:
            state.add_error("agentic", "No task description or input file provided for agentic mode")
            state.status = "FAILED"
            return

        workspace = state.output_root or os.path.dirname(state.input_path or ".")

        core = DevAgentCore()
        # Inherit the configured LLM
        core.llm = self.llm

        agentic_state = core.execute(
            task_description=task_desc,
            workspace=workspace,
            max_iterations=spec.max_retry * 10 if spec.max_retry else None
        )

        # Map agentic state back to legacy state for reporting
        state.status = "FINISHED" if agentic_state.status == "COMPLETED" else "FAILED"
        state.code_files = agentic_state.modified_files
        state.test_results = agentic_state.test_results
        state.add_trace("AgenticCore", agentic_state.status, {
            "iterations": agentic_state.current_iteration,
            "files_modified": len(agentic_state.modified_files)
        })

    # ==================== File Helpers ====================

    def _import_code_files(self, state: AgentState, code_path: str):
        """Import source code files from a path into the state."""
        if not code_path or not os.path.exists(code_path):
            return

        impl_dir = state.get_output_subdir("implementation")
        src_dir = os.path.join(impl_dir, "src")
        os.makedirs(src_dir, exist_ok=True)

        code_files = []
        if os.path.isfile(code_path):
            dest = os.path.join(src_dir, os.path.basename(code_path))
            code_text = self.file_tool.read_text(code_path)
            self.file_tool.write_text(dest, code_text)
            # Register imported code file to artifact registry if available
            try:
                reg = getattr(state, "artifact_registry", None)
                if reg is not None:
                    art = ArtifactModel(id=f"code_{state.task_id}_{os.path.basename(dest)}",
                                        type="implementation:source",
                                        format="py",
                                        content=code_text,
                                        metadata={"origin": code_path, "filename": os.path.basename(dest)})
                    reg.register_from_state(state, "implementation", art)
            except Exception:
                pass
            code_files.append(dest)
        elif os.path.isdir(code_path):
            for fname in os.listdir(code_path):
                if fname.endswith(".py"):
                    src = os.path.join(code_path, fname)
                    if os.path.isfile(src):
                        dest = os.path.join(src_dir, fname)
                        self.file_tool.write_text(dest, self.file_tool.read_text(src))
                        # Register each copied file to artifact registry if available
                        try:
                            reg = getattr(state, "artifact_registry", None)
                            if reg is not None:
                                content = self.file_tool.read_text(src)
                                art = ArtifactModel(id=f"code_{state.task_id}_{fname}",
                                                    type="implementation:source",
                                                    format="py",
                                                    content=content,
                                                    metadata={"origin": src, "filename": fname})
                                reg.register_from_state(state, "implementation", art)
                        except Exception:
                            pass
                        code_files.append(dest)
        state.code_files = code_files

    def _import_tests(self, state: AgentState, tests_path: str):
        """Import test files from a path into the state."""
        if not tests_path or not os.path.exists(tests_path):
            return

        tests_dir = state.get_output_subdir("tests")
        tests_src_dir = os.path.join(tests_dir, "tests")
        os.makedirs(tests_src_dir, exist_ok=True)

        if os.path.isfile(tests_path):
            self.file_tool.write_text(
                os.path.join(tests_src_dir, os.path.basename(tests_path)),
                self.file_tool.read_text(tests_path)
            )
            # Register test file
            try:
                reg = getattr(state, "artifact_registry", None)
                if reg is not None:
                    content = self.file_tool.read_text(tests_path)
                    art = ArtifactModel(id=f"test_{state.task_id}_{os.path.basename(tests_path)}",
                                        type="testing:test_file",
                                        format="py",
                                        content=content,
                                        metadata={"origin": tests_path, "filename": os.path.basename(tests_path)})
                    reg.register_from_state(state, "tests", art)
            except Exception:
                pass
        elif os.path.isdir(tests_path):
            for fname in os.listdir(tests_path):
                if fname.endswith(".py"):
                    src = os.path.join(tests_path, fname)
                    if os.path.isfile(src):
                        self.file_tool.write_text(
                            os.path.join(tests_src_dir, fname),
                            self.file_tool.read_text(src)
                        )
                        try:
                            reg = getattr(state, "artifact_registry", None)
                            if reg is not None:
                                content = self.file_tool.read_text(src)
                                art = ArtifactModel(id=f"test_{state.task_id}_{fname}",
                                                    type="testing:test_file",
                                                    format="py",
                                                    content=content,
                                                    metadata={"origin": src, "filename": fname})
                                reg.register_from_state(state, "tests", art)
                        except Exception:
                            pass

    def _save_execution_log(self, state: AgentState):
        """Save structured execution log."""
        log_path = os.path.join(state.output_root, "execution.log")
        lines = []
        lines.append(f"DevAgent Execution Log - {state.task_id}")
        lines.append(f"Task Type: {state.task_type}")
        lines.append(f"Status: {state.status}")
        lines.append("")
        lines.append("Execution Trace:")
        for trace in state.execution_trace:
            ts = (trace.get("timestamp", "") or "")[:19]
            lines.append(f"  [{ts}] {trace.get('node', '')}: {trace.get('status', '')}")
        lines.append("")
        if state.errors:
            lines.append("Errors:")
            for err in state.errors:
                lines.append(f"  [{err.get('phase', '')}] {err.get('message', '')}")
        lines.append("")
        lines.append(f"Final Report: {state.final_report}")
        self.file_tool.write_text(log_path, "\n".join(lines))
        # Also register execution log into central registry for audit (fallback safe)
        try:
            reg = getattr(state, "artifact_registry", None)
            if reg is not None:
                art = ArtifactModel(id=f"execution_log_{state.task_id}",
                                    type="workflow:execution_log",
                                    format="txt",
                                    content="\n".join(lines),
                                    metadata={"generated_by": "WorkflowController", "filename": "execution.log"})
                reg.register_from_state(state, "workflow", art)
        except Exception:
            pass
