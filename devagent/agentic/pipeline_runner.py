"""PipelineRunner — Plan-Execute-Gate: code determines flow, LLM generates content.

The heart of DevAgent's stability architecture. Replaces LLM-driven "what next?"
decisions with a code-enforced phase sequence. DeepSeek only generates content.

v3.3 — Adaptive Pipeline with Repair Cycle + Interactive Revision:
  Phase 1: Requirements   → analyze_requirements → review gate
  Phase 2: Design         → design_architecture    → review gate
  Phase 3: Implementation → generate_code          → review gate
  Phase 4: Testing        → generate_tests + test_run → review gate
  Phase 5: Repair         → debug_issue + repair_code → review gate (conditional)
  Phase 6: Delivery       → generate_report        → review gate
  Phase 7: Interactive    → human feedback → agentic changes → loop (conditional)

Interactive Revision:
  - After delivery, if interactive channel is connected → enter revision loop
  - Human reviews deliverables, sends feedback via WebSocket
  - DevAgent runs agentic ReAct loop to implement the requested changes
  - Human reviews changes → more feedback or "done"
  - Unlimited iterations — human controls when to finish

Conditional Repair:
  - After testing, if tests FAILED → auto-enter Phase 5 (repair)
  - After repair, re-enter Phase 4 (testing) for regression verification
  - Max 2 repair cycles, then auto-progress to delivery

Revise mechanism:
  - When human responds "revise" + feedback:
    → feedback is injected into the tool's input params
    → tool re-executes with human guidance
    → re-submitted for review
  - Max retries per phase before auto-progressing

Each phase:
  1. Execute tool (LLM generates content)
  2. Run deterministic checks
  3. Submit for human review (thread-safe, non-blocking)
  4. Process review decision: approve → next | revise → redo | reject → abort
  5. Timeout or retries exhausted → auto-progress
"""

import os
import re
import time
import json
import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class PhaseConfig:
    """Configuration for a single pipeline phase."""
    name: str
    display_name: str
    tool: str                     # Primary tool, or list[str] for multi-step
    output_dir: str
    wait_files: list[str]
    max_retries: int = 2
    timeout_seconds: int = 600
    auto_approve: bool = False
    conditional: bool = False     # Only run if condition met (e.g., tests failed)
    on_complete: str = ""         # Phase to go back to after completion (e.g., "testing")


@dataclass
class PhaseResult:
    """Result of executing one pipeline phase."""
    phase: str
    success: bool
    tool_output: str = ""
    files_generated: list[str] = field(default_factory=list)
    review_decision: str = ""
    review_feedback: str = ""
    retries: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class PipelineState:
    """State tracked during pipeline execution."""
    task_id: str = ""
    current_phase: str = ""
    phase_index: int = 0
    results: list[PhaseResult] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "RUNNING"

    requirements: dict = field(default_factory=dict)
    design_artifacts: dict = field(default_factory=dict)
    code_files: list = field(default_factory=list)
    test_results: dict = field(default_factory=dict)
    debug_analysis: dict = field(default_factory=dict)
    repair_patch: dict = field(default_factory=dict)

    # Dynamic phase management
    repair_cycles: int = 0
    max_repair_cycles: int = 2

    agent_state: Optional[object] = None


# ============================================================================
# Default Pipeline Configuration
# ============================================================================

DEFAULT_PHASES = [
    PhaseConfig(
        name="requirements",
        display_name="需求分析",
        tool="analyze_requirements",
        output_dir="01_requirements",
        wait_files=["01_requirements/requirement_specification.md"],
        max_retries=2,
        timeout_seconds=600,
    ),
    PhaseConfig(
        name="design",
        display_name="架构设计",
        tool="design_architecture",
        output_dir="02_design",
        wait_files=["02_design/architecture_design_spec.md"],
        max_retries=2,
        timeout_seconds=480,
    ),
    PhaseConfig(
        name="implementation",
        display_name="代码生成",
        tool="generate_code",
        output_dir="03_implementation",
        wait_files=[],  # Discovered from output dir
        max_retries=3,
        timeout_seconds=480,
    ),
    PhaseConfig(
        name="testing",
        display_name="测试执行",
        tool="generate_tests",
        output_dir="04_tests",
        wait_files=["04_tests/pytest_result.json"],
        max_retries=2,
        timeout_seconds=480,
    ),
    PhaseConfig(
        name="repair",
        display_name="Bug 修复",
        tool="repair_code",
        output_dir="05_repair",
        wait_files=["05_repair/patch.diff"],
        max_retries=2,
        timeout_seconds=600,
        conditional=True,       # Only run if tests failed
        on_complete="testing",  # After repair, go back to testing
    ),
    PhaseConfig(
        name="delivery",
        display_name="最终交付",
        tool="generate_report",
        output_dir="06_reports",
        wait_files=["06_reports/executive_report.md"],
        max_retries=1,
        timeout_seconds=300,
    ),
    PhaseConfig(
        name="interactive_revision",
        display_name="交互修改",
        tool="interactive_revision",  # Special tool — handled by _run_interactive_revision
        output_dir="07_revision",
        wait_files=[],
        max_retries=100,       # Unlimited — human controls when to exit
        timeout_seconds=1800,   # 30 min per feedback round
        conditional=True,       # Only runs when interactive channel is connected
    ),
]


# ============================================================================
# Pipeline Runner
# ============================================================================

class PipelineRunner:
    """Executes the full development pipeline with adaptive phase flow.

    Supports:
    - Dynamic phase insertion (repair phase when tests fail)
    - Revise → redo with feedback injected into tool params
    - Phase loopback (repair → testing)
    - Proper state accumulation across revise cycles
    """

    def __init__(self, llm_client, tools, thread_channel,
                 workspace: str = ".", phases: list[PhaseConfig] = None):
        self.llm = llm_client
        self.tools = tools
        self.channel = thread_channel
        self.workspace = workspace
        self.phases = phases or DEFAULT_PHASES
        self.validator = None

        self._on_phase_start: Optional[Callable] = None
        self._on_phase_end: Optional[Callable] = None
        self._on_review_requested: Optional[Callable] = None
        self._on_pipeline_complete: Optional[Callable] = None

    def set_callbacks(self, on_phase_start=None, on_phase_end=None,
                      on_review_requested=None, on_pipeline_complete=None):
        self._on_phase_start = on_phase_start
        self._on_phase_end = on_phase_end
        self._on_review_requested = on_review_requested
        self._on_pipeline_complete = on_pipeline_complete

    def run(self, task_description: str, workspace: str = ".",
            output_root: str = None, language: str = "python") -> PipelineState:
        """Execute the full pipeline synchronously."""
        ws = workspace or self.workspace
        out = output_root or os.path.join(ws, "outputs")
        os.makedirs(out, exist_ok=True)
        state = PipelineState(task_id=f"pipeline_{int(time.time())}")
        print(f"[Pipeline] Source: {ws}, Output: {out}", flush=True)

        if not self._phase_init(state, task_description, ws):
            return state

        # Build phase execution list — skips conditional phases unless triggered
        phase_list = list(self.phases)
        idx = 0
        while idx < len(phase_list):
            phase = phase_list[idx]
            state.phase_index = idx
            state.current_phase = phase.name

            # Check if this is a conditional phase that should be skipped
            if phase.conditional:
                if not self._should_run_phase(phase, state):
                    print(f"[Pipeline] Skipping conditional phase: {phase.display_name}",
                          flush=True)
                    idx += 1
                    continue

            if self._on_phase_start:
                self._on_phase_start(phase.name, phase.display_name)

            result = self._execute_phase_with_review(
                phase, state, task_description, out
            )
            state.results.append(result)

            if self._on_phase_end:
                self._on_phase_end(phase.name, result)

            if not result.success and result.review_decision == "reject":
                state.status = "FAILED"
                state.current_phase = phase.name
                break

            # Handle phase loopback (e.g., repair → testing)
            if result.success and phase.on_complete:
                # Find the target phase index and loop back
                for back_idx, bp in enumerate(phase_list):
                    if bp.name == phase.on_complete:
                        print(f"[Pipeline] ↻ {phase.display_name} → {bp.display_name}",
                              flush=True)
                        state.repair_cycles += 1
                        if state.repair_cycles >= state.max_repair_cycles:
                            print("[Pipeline] Max repair cycles reached — advancing to delivery",
                                  flush=True)
                            idx += 1
                        else:
                            idx = back_idx
                        break
                else:
                    idx += 1
            else:
                idx += 1

        if state.status == "RUNNING":
            state.status = "COMPLETED"

        if self._on_pipeline_complete:
            self._on_pipeline_complete(state)

        return state

    def _should_run_phase(self, phase: PhaseConfig, state: PipelineState) -> bool:
        """Determine if a conditional phase should execute."""
        if phase.name == "repair":
            tr = state.test_results or {}
            failed = tr.get("failed", 0)
            collected = tr.get("collected", 0)
            if collected > 0 and failed > 0:
                return True
            if failed < 0:
                return True
            last_result = state.results[-1] if state.results else None
            if last_result and last_result.review_decision == "revise":
                if "fix" in (last_result.review_feedback or "").lower():
                    return True
            return False
        if phase.name == "interactive_revision":
            # Only run if we have an interactive channel AND it's connected
            if self.channel is None:
                return False
            # Check if connected within a short window
            if not self.channel._has_clients():
                print("[Pipeline] No interactive client — skipping revision phase",
                      flush=True)
                return False
            return True
        return True

    # ==================================================================
    # Phase Init
    # ==================================================================

    def _phase_init(self, state: PipelineState, task_desc: str,
                    workspace: str) -> bool:
        req_path = os.path.join(workspace, "requirements.md")
        if not os.path.exists(req_path):
            return True
        try:
            content = open(req_path, "r").read()
            if content not in task_desc:
                state.task_description = (
                    f"{task_desc}\n\n## Requirements Document\n\n{content}"
                )
        except Exception:
            pass
        return True

    # ==================================================================
    # Phase execution
    # ==================================================================

    def _execute_phase_with_review(self, phase: PhaseConfig,
                                    state: PipelineState,
                                    task_desc: str,
                                    output_dir: str) -> PhaseResult:
        """Execute one phase: run tool → check → review → handle response."""
        result = PhaseResult(phase=phase.name, success=False)
        t0 = time.time()

        # Accumulate revise feedback across attempts
        revise_feedback = ""

        for attempt in range(phase.max_retries + 1):
            # 1. Execute the tool with any accumulated revise feedback
            tool_result = self._run_tool(
                phase.tool, phase.name, state,
                task_desc, output_dir, attempt,
                revise_feedback=revise_feedback,
            )

            if not tool_result.get("success", True):
                result.errors.append(
                    f"Tool {phase.tool} failed: {tool_result.get('error','')}"
                )
                result.retries = attempt
                has_partial = bool(
                    tool_result.get("files") or
                    (tool_result.get("structured") and
                     list(tool_result["structured"].keys()) != ["error"])
                )
                if has_partial:
                    pass  # Fall through to review with partial output
                elif attempt < phase.max_retries:
                    continue  # Retry
                else:
                    break

            # 2. Verify output files
            missing_files = []
            for f in phase.wait_files:
                fp = os.path.join(output_dir, f) if not os.path.isabs(f) else f
                if not os.path.exists(fp):
                    missing_files.append(f)

            generated = tool_result.get("files", [])
            result.files_generated = generated

            # 3. Deterministic validation
            validation = None
            if generated:
                if self.validator is None:
                    from .pipeline_validator import DeterministicValidator
                    self.validator = DeterministicValidator(output_dir)
                validation = self.validator.validate(phase.name, generated)

                # If tests FAILED during validation, record it
                if phase.name == "testing":
                    for c in (validation.checks if validation else []):
                        if c.name == "tests":
                            p = sum(int(x) for x in re.findall(r'(\d+) passed', c.detail))
                            f = sum(int(x) for x in re.findall(r'(\d+) failed', c.detail))
                            state.test_results = {"collected": p+f, "passed": p, "failed": f}

            # 4. Human review
            review_result = self._submit_review(
                phase=phase,
                state=state,
                summary=self._build_phase_summary(
                    phase, tool_result, generated, missing_files, validation
                ),
                files=list(set(generated + phase.wait_files)),
                attempt=attempt,
            )

            review_decision = review_result.get("decision", "auto_approve")
            result.review_decision = review_decision
            result.review_feedback = review_result.get("feedback", "")

            if review_decision == "approve":
                self._update_state_from_phase(state, phase.name, tool_result)
                result.success = True
                break

            elif review_decision == "revise":
                feedback = review_result.get("feedback", "")
                suggestions = review_result.get("suggestions", [])
                # Accumulate feedback for tool re-execution
                fb_parts = [f for f in [feedback] + suggestions if f]
                revise_feedback = "\n".join(fb_parts)
                print(f"[Pipeline] ✎ REVISE {phase.name}: {revise_feedback[:200]}",
                      flush=True)
                # Also append to task_desc for LLM context
                task_desc = (
                    f"{task_desc}\n\n## ⛔ REVISION REQUIRED (attempt {attempt+1}/{phase.max_retries})\n"
                    f"{revise_feedback}\n"
                    f"## FIX the issues above and regenerate. Do NOT skip this directive."
                )

            elif review_decision == "reject":
                result.success = False
                break

            else:  # auto / timeout
                self._update_state_from_phase(state, phase.name, tool_result)
                result.success = True
                break

        # Ensure 'attempt' is always bound for the result
        if 'attempt' not in dir():
            attempt = 0
        result.retries = attempt
        result.elapsed_seconds = time.time() - t0
        return result

    # ==================================================================
    # Tool execution
    # ==================================================================

    def _run_tool(self, tool_name: str, phase_name: str,
                  state: PipelineState, task_desc: str,
                  output_dir: str, attempt: int,
                  revise_feedback: str = "") -> dict:
        """Execute a pipeline tool with quality-enforced params."""
        params = {}
        quality_prefix = (
            "## LANGUAGE: ALL documents and descriptions MUST be written in "
            "SIMPLIFIED CHINESE (简体中文). NOT English.\n"
            "## QUALITY: Complete professional output. "
            "Code with type hints + docstrings + error handling.\n"
            "## TESTING: Every public function needs 1 happy-path "
            "+ 1 error-path test.\n\n"
        )

        if tool_name == "analyze_requirements":
            params["input_content"] = quality_prefix + task_desc
            if state.requirements and attempt > 0:
                params["requirements"] = state.requirements
            if revise_feedback:
                params["input_content"] += (
                    f"\n\n## ⛔ REVISION REQUIRED\n{revise_feedback}"
                )

        elif tool_name == "design_architecture":
            params["requirements"] = state.requirements
            if revise_feedback:
                params["requirements"] = dict(state.requirements)
                params["requirements"]["_revise_feedback"] = revise_feedback
                params["input_content"] = revise_feedback

        elif tool_name == "generate_code":
            params["design_artifacts"] = state.design_artifacts
            if state.requirements:
                params["requirements"] = state.requirements
            if revise_feedback:
                params["design_artifacts"] = dict(state.design_artifacts)
                params["design_artifacts"]["_revise_feedback"] = revise_feedback

        elif tool_name == "generate_tests":
            params["code_files"] = state.code_files
            if revise_feedback:
                params["code_files"] = list(state.code_files) if state.code_files else []
                params["test_feedback"] = revise_feedback

        elif tool_name == "debug_issue":
            params["test_results"] = state.test_results
            params["code_files"] = state.code_files
            if revise_feedback:
                params["error_description"] = revise_feedback

        elif tool_name == "repair_code":
            # First run debug_issue to get analysis, then repair
            debug_result = self._run_tool(
                "debug_issue", "repair", state, task_desc, output_dir, 0,
                revise_feedback=revise_feedback,
            )
            if debug_result.get("success") and debug_result.get("structured"):
                state.debug_analysis = debug_result.get("structured", {})
                params["debug_analysis"] = state.debug_analysis
            else:
                # Fallback: use test results directly
                params["debug_analysis"] = {
                    "needed": True,
                    "bug_location": {"file": "", "function": "", "line_range": [0, 0]},
                    "root_cause": str(state.test_results or "Tests failed"),
                    "fix_hypotheses": [],
                }
            params["code_files"] = state.code_files
            if revise_feedback:
                params["debug_analysis"]["_revise_feedback"] = revise_feedback

        elif tool_name == "generate_report":
            params["requirements"] = state.requirements
            params["design_artifacts"] = state.design_artifacts
            params["code_files"] = state.code_files
            params["test_results"] = state.test_results

        elif tool_name == "interactive_revision":
            # Special: handled by _run_interactive_revision directly
            return self._run_interactive_revision(
                state, task_desc, output_dir, attempt, revise_feedback
            )

        # === Execute ===
        result = None
        try:
            result = asyncio.run(
                self.tools.execute(tool_name, params, output_dir)
            )
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": f"Tool {tool_name} execution error: {e}\n{traceback.format_exc()}"
            }

        if result is None:
            return {"success": False, "error": "Tool execution returned None"}

        return {
            "success": result.success,
            "error": result.error or "",
            "output": result.output or "",
            "structured": result.structured or {},
            "files": self._extract_generated_files(phase_name, output_dir, result),
        }

    # ==================================================================
    # Interactive Revision
    # ==================================================================

    def _run_interactive_revision(self, state: PipelineState,
                                   task_desc: str, output_dir: str,
                                   attempt: int, revise_feedback: str = "") -> dict:
        """Interactive revision loop: human feedback → agentic fix → review → loop."""
        if not self.channel:
            return {"success": True, "error": "", "output": "", "structured": {},
                    "files": []}

        from .core import DevAgentCore

        revision_count = 0
        max_revisions = 50

        while revision_count < max_revisions:
            revision_count += 1

            summary = self._build_revision_summary(state, output_dir, revision_count)

            req = self.channel.create_feedback(
                phase="interactive_revision",
                title=f"交互修改 (第{revision_count}轮)",
                summary=summary,
                timeout=1800,
            )

            result = req.wait(timeout_seconds=1800 + 10)
            decision = result.get("decision", "timeout")
            feedback = result.get("feedback", "").strip()
            suggestions = result.get("suggestions", [])

            print(f"[Pipeline] 💬 Revision {revision_count}: "
                  f"decision={decision}, feedback='{feedback[:100]}'", flush=True)

            # Check if human wants to finish
            done_signals = ("done", "完成", "ok", "approve", "好的", "可以", "满意",
                           "通过", "结束", "没有问题", "没问题")
            if (decision == "approve" or decision == "done"
                    or feedback.lower() in done_signals
                    or feedback == ""):
                print("[Pipeline] ✅ Revision complete — human approved",
                      flush=True)
                return {
                    "success": True, "error": "",
                    "output": f"交互修改完成 — 共 {revision_count} 轮",
                    "structured": {"revision_rounds": revision_count,
                                  "final_status": "approved"},
                    "files": self._extract_generated_files(
                        "interactive_revision", output_dir,
                        type("R", (), {"success": True, "structured": {}})()
                    ),
                }

            if decision == "timeout":
                print("[Pipeline] ⏱ Revision timed out — completing", flush=True)
                return {
                    "success": True, "error": "",
                    "output": f"交互修改超时 — 共 {revision_count} 轮后自动完成",
                    "structured": {"revision_rounds": revision_count,
                                  "final_status": "timeout"},
                    "files": [],
                }

            # Execute the requested changes via agentic ReAct loop
            change_task = (
                f"# 项目修改请求 (第{revision_count}轮)\n\n"
                f"## 用户反馈\n{feedback}\n"
            )
            if suggestions:
                change_task += "\n## 具体要求\n" + "\n".join(
                    f"- {s}" for s in suggestions
                )
            change_task += (
                f"\n\n## 当前项目\n"
                f"- 输出目录: {output_dir}\n"
                f"- 已有 {len(state.code_files)} 个代码文件\n"
                f"- 请直接修改现有文件以满足用户需求\n"
                f"- 修改完成后调用 submit 工具提交\n"
            )

            print(f"[Pipeline] 🤖 Running agentic revision #{revision_count}...",
                  flush=True)

            try:
                core = DevAgentCore()
                core.max_iterations = 30

                if self.channel:
                    from .interaction import InteractionController, _set_active_controller
                    ic = core.interaction
                    if ic is None:
                        ic = InteractionController(
                            enable_approval=False, enable_dialogue=False,
                            enable_streaming=False, enable_review_gate=False,
                        )
                        core.interaction = ic
                    ic._thread_channel = self.channel
                    core.session_mgr = getattr(self.channel, 'session_mgr', None)
                    _set_active_controller(ic)

                agent_state = core.execute(
                    change_task,
                    workspace=output_dir,
                    language="python",
                    max_iterations=30,
                )

                print(f"[Pipeline] Agentic result: "
                      f"status={agent_state.status}, "
                      f"iters={agent_state.current_iteration}, "
                      f"modified={len(agent_state.modified_files)} files",
                      flush=True)

                for f in agent_state.modified_files:
                    if f.endswith(".py") and f not in state.code_files:
                        if "/tests/" not in f and "\\tests\\" not in f:
                            state.code_files.append(f)

                if agent_state.test_results:
                    state.test_results = agent_state.test_results

            except Exception as e:
                import traceback
                print(f"[Pipeline] ❌ Agentic revision failed: {e}", flush=True)
                traceback.print_exc()
                continue

        print(f"[Pipeline] ⚠ Max revisions ({max_revisions}) reached", flush=True)
        return {
            "success": True, "error": "",
            "output": f"交互修改达到上限 ({max_revisions} 轮) — 自动完成",
            "structured": {"revision_rounds": revision_count,
                          "final_status": "max_reached"},
            "files": [],
        }

    def _build_revision_summary(self, state: PipelineState,
                                 output_dir: str,
                                 revision_count: int) -> str:
        """Build project state summary for the revision prompt."""
        parts = [f"# 📦 项目交付物 — 第 {revision_count} 轮修改\n"]

        dirs_to_scan = [
            ("01_requirements", "需求分析"),
            ("02_design", "架构设计"),
            ("03_implementation", "代码实现"),
            ("04_tests", "测试"),
            ("05_repair", "Bug修复"),
            ("06_reports", "报告"),
        ]

        for dirname, label in dirs_to_scan:
            scan_dir = os.path.join(output_dir, dirname)
            if not os.path.isdir(scan_dir):
                continue
            parts.append(f"\n## {label}")
            for root, dirs, files in os.walk(scan_dir):
                dirs[:] = [d for d in dirs if d != '__pycache__']
                for fn in sorted(files):
                    fp = os.path.join(root, fn)
                    try:
                        size = os.path.getsize(fp)
                        size_str = (f"{size}B" if size < 1024
                                    else f"{size//1024}KB")
                    except OSError:
                        size_str = "?"
                    rel = os.path.relpath(fp, output_dir)
                    parts.append(f"  - `{rel}` ({size_str})")

        tr = state.test_results or {}
        if tr.get("collected", 0) > 0:
            parts.append(f"\n## 测试结果")
            parts.append(f"  通过 {tr.get('passed', 0)} / {tr.get('collected', 0)}"
                        f" (失败 {tr.get('failed', 0)})")

        parts.append(f"\n## 操作")
        parts.append("  输入修改意见（中文/English）→ DevAgent 自动修改 → 审核")
        parts.append("  输入 **done** / **完成** / **approve** 结束修改")

        return "\n".join(parts)

    def _extract_generated_files(self, phase_name: str,
                                  output_dir: str, result) -> list[str]:
        """Extract list of generated file paths from tool result."""
        files = []

        if result.success and result.structured:
            for key in ("code_files", "test_files", "modified_files"):
                val = result.structured.get(key, [])
                if isinstance(val, list):
                    files.extend(val)

        dir_map = {
            "requirements": "01_requirements",
            "design": "02_design",
            "implementation": "03_implementation",
            "testing": "04_tests",
            "repair": "05_repair",
            "delivery": "06_reports",
        }
        scan_dir = os.path.join(output_dir, dir_map.get(phase_name, ""))
        if os.path.isdir(scan_dir):
            for root, dirs, filenames in os.walk(scan_dir):
                for fn in filenames:
                    if fn.endswith((".py", ".md", ".json", ".mmd", ".diff")):
                        fp = os.path.join(root, fn)
                        files.append(fp)

        return list(set(files))

    # ==================================================================
    # Review submission
    # ==================================================================

    def _submit_review(self, phase: PhaseConfig, state: PipelineState,
                       summary: str, files: list[str],
                       attempt: int) -> dict:
        """Submit a phase for human review."""
        if self._on_review_requested:
            self._on_review_requested(phase.name, phase.display_name, summary, files)

        if not self.channel:
            return {"decision": "auto_approve", "feedback": "No interaction channel"}

        # Pass the actual phase timeout (not the default 10s)
        req = self.channel.create_review(
            phase=phase.name,
            title=phase.display_name,
            summary=summary,
            quality_score="unknown",
            timeout=max(phase.timeout_seconds, 30),
        )

        # Wait for human response
        result = req.wait(timeout_seconds=phase.timeout_seconds + 10)
        return result

    def _build_phase_summary(self, phase: PhaseConfig,
                             tool_result: dict,
                             generated_files: list[str],
                             missing_files: list[str],
                             validation) -> str:
        """Build a human-readable summary of the phase execution."""
        parts = []
        parts.append(f"Phase: {phase.display_name} ({phase.name})")
        parts.append(f"Tool: {phase.tool}")

        if generated_files:
            parts.append(f"\n📁 Files generated: {len(generated_files)}")
            for f in generated_files[:8]:
                short = f.replace("\\", "/").split("/")[-1]
                dir_part = "/".join(f.replace("\\", "/").split("/")[-2:-1])
                parts.append(f"  - {dir_part}/{short}" if dir_part else f"  - {short}")

        if missing_files:
            parts.append(f"\n⚠️  Missing expected files: {', '.join(missing_files)}")

        if validation and hasattr(validation, 'checks') and validation.checks:
            parts.append("\n🔍 Deterministic Verification:")
            for c in validation.checks:
                icon = "✅" if c.passed else "❌"
                parts.append(f"  {icon} {c.name}: {c.detail}")

        if tool_result.get("error"):
            parts.append(f"\n❌ Error: {tool_result['error'][:300]}")

        return "\n".join(parts)

    # ==================================================================
    # State management
    # ==================================================================

    def _update_state_from_phase(self, state: PipelineState,
                                  phase_name: str, tool_result: dict):
        """Update PipelineState with structured output from a completed phase."""
        structured = tool_result.get("structured", {}) or {}

        if phase_name == "requirements":
            if "requirements" in structured:
                state.requirements = structured["requirements"]
            elif structured:
                state.requirements = structured

        elif phase_name == "design":
            if "design_artifacts" in structured:
                state.design_artifacts = structured["design_artifacts"]
            elif structured:
                state.design_artifacts = structured

        elif phase_name == "implementation":
            files = tool_result.get("files", [])
            state.code_files = [
                f for f in files
                if f.endswith(".py") and "/tests/" not in f and "\\tests\\" not in f
            ]

        elif phase_name == "testing":
            if "test_results" in structured:
                state.test_results = structured["test_results"]
            elif structured:
                state.test_results = structured

        elif phase_name == "repair":
            if "repair_patch" in structured:
                state.repair_patch = structured["repair_patch"]
            elif structured:
                state.repair_patch = structured
            if "debug_analysis" in structured:
                state.debug_analysis = structured["debug_analysis"]


# ============================================================================
# Convenience function
# ============================================================================

def run_full_pipeline(task_description: str,
                      workspace: str = ".",
                      output_root: str = None,
                      thread_channel=None,
                      llm_client=None,
                      tools=None) -> PipelineState:
    """Run the complete Plan-Execute-Gate pipeline."""
    if tools is None:
        from .tools import ToolRegistry
        tools = ToolRegistry.create_default(llm_client=llm_client,
                                            include_pipeline=True)

    runner = PipelineRunner(
        llm_client=llm_client,
        tools=tools,
        thread_channel=thread_channel,
        workspace=workspace,
    )

    return runner.run(task_description, workspace, output_root=output_root)
