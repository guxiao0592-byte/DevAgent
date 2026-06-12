"""Interaction Controller — bridges the Agentic Loop with user interaction channels.

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │                  InteractionController                  │
  │                                                         │
  │  pre_action_hooks  →  [ApprovalGate, ProgressNotifier] │
  │  post_action_hooks →  [StatePublisher, ArtifactNotifier]│
  │  command_handler   →  [Pause/Resume/Abort/Skip/Redirect]│
  │  dialogue_manager  →  [AskUser, Clarify, Decide]        │
  │  terminal_channel  →  [CLI-based feedback when no WS]   │
  └─────────────────────────────────────────────────────────┘

Provides:
  - Approval gates for destructive/large operations
  - Agent-initiated user questions (ask_user tool integration)
  - Progress streaming with snapshot publishing
  - Command handling (pause/resume/abort/redirect/inject)
  - Terminal-based CLI feedback (when stdin is a TTY, no WS clients)
  - Hook-based integration with minimal core loop changes
"""

import re
import sys
import time
import enum
import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Awaitable


# ============================================================================
# Module-level registry for ask_user tool access
# ============================================================================

_active_controller: Optional["InteractionController"] = None


def _set_active_controller(controller: Optional["InteractionController"]):
    """Set the active controller for ask_user tool access."""
    global _active_controller
    _active_controller = controller


def _get_active_controller() -> Optional["InteractionController"]:
    """Get the active controller for ask_user tool access."""
    return _active_controller


# ============================================================================
# Data Types
# ============================================================================

class CommandType(str, enum.Enum):
    """Commands that the user can send during execution."""
    PAUSE = "pause"
    RESUME = "resume"
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    REDIRECT = "redirect"
    INJECT_CONTEXT = "inject"
    FORCE_TERMINATE = "kill"


class ApprovalType(str, enum.Enum):
    """Types of actions requiring user approval."""
    DESTRUCTIVE_SHELL = "destructive_shell"
    LARGE_EDIT = "large_edit"
    UNCERTAIN_FIX = "uncertain_fix"
    UNVERIFIED_EDIT = "unverified_edit"
    EXTERNAL_API_CALL = "external_api"
    FILE_DELETE = "file_delete"
    GIT_OPERATION = "git_operation"
    CONFIG_CHANGE = "config_change"


@dataclass
class ApprovalRequest:
    """A single approval request sent to the user."""
    id: str
    type: ApprovalType
    description: str
    details: dict = field(default_factory=dict)
    options: list[str] = field(default_factory=lambda: ["approve", "deny", "approve_all"])
    timeout_seconds: int = 120
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved: bool = False
    resolution: Optional[str] = None
    user_note: str = ""


@dataclass
class UserCommand:
    """A command from the user injected into the execution loop."""
    type: CommandType
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    client_id: str = ""


# ============================================================================
# InteractionController
# ============================================================================

class InteractionController:
    """Central controller for all user-agent interactions during execution.

    Integrates with DevAgentCore via hook-based architecture:
      - DevAgentCore calls pre_action() before each tool execution
      - DevAgentCore calls post_action() after each tool execution
      - DevAgentCore calls check_commands() each loop iteration

    Usage in DevAgentCore:
        controller = InteractionController(event_bus, streaming, hitl, session_mgr)
        await controller.pre_action(action, state)   # may block (approval)
        ... execute tool ...
        await controller.post_action(action, result, state)  # publish state
        await controller.check_commands(state)  # process user commands
    """

    # Shell patterns that trigger destructive_shell approval
    DESTRUCTIVE_PATTERNS = [
        r"rm\s+-rf", r"git\s+push\s+--force", r"git\s+reset\s+--hard",
        r"sudo\s+", r">\s*/dev/", r"mkfs\.", r"dd\s+if=",
        r"chmod\s+777", r"chown\s+-R",
    ]

    def __init__(self, event_bus=None, streaming_server=None,
                 hitl_manager=None, session_manager=None,
                 llm_client=None,
                 enable_approval: bool = True,
                 enable_dialogue: bool = True,
                 enable_streaming: bool = True,
                 enable_review_gate: bool = True):
        self.event_bus = event_bus
        self.streaming = streaming_server
        self.hitl = hitl_manager
        self.session_mgr = session_manager

        # Thread-safe channel for background agent threads
        self._thread_channel = None  # Set by API endpoint when running in bg thread

        # Terminal channel for CLI-based interaction (fallback when no WebSocket)
        self.terminal = TerminalChannel()
        if self.terminal.available:
            self.terminal.start()

        # Queues
        self._command_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._pending_approval: Optional[ApprovalRequest] = None

        # Config
        self.enable_approval = enable_approval
        self.enable_dialogue = enable_dialogue
        self.enable_streaming = enable_streaming
        self.enable_review_gate = enable_review_gate
        self.auto_approve_policy: dict[ApprovalType, bool] = {}
        self.max_questions_per_task: int = 10
        self._question_count: int = 0

        # Phase review gate — LLM quality evaluation + human review
        self.review_gate = None
        if enable_review_gate:
            from .review_gate import PhaseReviewGate
            self.review_gate = PhaseReviewGate(
                llm_client=llm_client,
                streaming_server=streaming_server,
                session_manager=session_manager,
                terminal_channel=self.terminal,
            )

        # State
        self._paused: bool = False
        self._aborted: bool = False
        self._step_counter: int = 0
        self._task_id: str = ""

        # Pre/post action hooks — extensible pipeline
        self.pre_action_hooks: list[Callable[..., Awaitable]] = []
        self.post_action_hooks: list[Callable[..., Awaitable]] = []

        # Register built-in hooks
        if enable_approval:
            self.pre_action_hooks.append(self._check_approval_gate)
        if enable_streaming:
            self.pre_action_hooks.append(self._publish_pre_action_state)
            self.post_action_hooks.append(self._publish_post_action_state)
            self.post_action_hooks.append(self._check_artifact_notification)
            self.post_action_hooks.append(self._check_test_notification)

    # ==================================================================
    # Public API — called from DevAgentCore
    # ==================================================================

    async def pre_action(self, action: dict, state) -> bool:
        """Run all pre-action hooks. Returns False to BLOCK the action."""
        for hook in self.pre_action_hooks:
            try:
                should_proceed = await hook(action, state)
                if not should_proceed:
                    return False
            except Exception:
                pass  # Never let a hook crash the loop
        self._step_counter += 1
        return True

    async def post_action(self, action: dict, result, state):
        """Run all post-action hooks."""
        for hook in self.post_action_hooks:
            try:
                await hook(action, result, state)
            except Exception:
                pass  # Never let a hook crash the loop

    async def check_commands(self, state) -> Optional[UserCommand]:
        """Check for pending user commands (non-blocking).

        Returns the last command processed, or None if queue is empty.
        """
        try:
            cmd: UserCommand = self._command_queue.get_nowait()
            await self._handle_command(cmd, state)
            self._command_queue.task_done()
            return cmd
        except asyncio.QueueEmpty:
            pass
        return None

    async def wait_for_approval(self, request: ApprovalRequest) -> str:
        """Block until user approves/denies, or timeout.

        Returns one of: "approve", "deny", "approve_all", "auto_deny"
        """
        self._pending_approval = request

        # PRIORITY 1: Thread channel (background agent thread)
        if self._thread_channel:
            req = self._thread_channel.create_approval(
                description=request.description,
                details=request.details,
                options=request.options,
                timeout=request.timeout_seconds,
            )
            result = req.wait(timeout_seconds=request.timeout_seconds + 5)
            resolution = result.get("decision", "auto_deny")
            self._thread_channel._pending.pop(req.id, None)
            return resolution

        # Check if any WS clients are connected — if not, use terminal
        has_clients = (self.session_mgr and
                       self.session_mgr.get_connected_count(getattr(self, '_task_id', '')) > 0)

        if has_clients:
            # Push to streaming channel so client can render approval UI
            if self.streaming:
                await self._push_event("approval.requested", {
                    "id": request.id,
                    "type": request.type.value,
                    "description": request.description,
                    "details": request.details,
                    "options": request.options,
                    "timeout": request.timeout_seconds,
                    "created_at": request.created_at,
                })

            # Wait with timeout
            try:
                resolution = await asyncio.wait_for(
                    self._await_resolution(request.id, request.options),
                    timeout=request.timeout_seconds
                )
            except asyncio.TimeoutError:
                resolution = "auto_deny"

        elif self.terminal.available:
            # Terminal-based approval
            resolution = await self.terminal.prompt_approval(
                request.id, request.description,
                request.options, request.timeout_seconds
            )
        else:
            # No interaction channel available — auto-approve
            resolution = "auto_approve"

        request.resolved = True
        request.resolution = resolution
        self._pending_approval = None

        if self.streaming:
            await self._push_event("approval.resolved", {
                "id": request.id, "resolution": resolution,
            })

        return resolution

    async def ask_user(self, question: str, options: list[str] = None,
                       context: dict = None) -> str:
        """Agent-initiated question to the user (used by ask_user tool).

        Returns the user's answer, or empty string if timeout/no response.
        """
        if not self.enable_dialogue:
            return ""

        self._question_count += 1
        if self._question_count > self.max_questions_per_task:
            return ""  # Rate limit

        question_id = f"q_{int(time.time())}_{self._question_count}"

        has_clients = (self.session_mgr and
                       self.session_mgr.get_connected_count(getattr(self, '_task_id', '')) > 0)

        # PRIORITY 1: Thread channel (background agent thread)
        if self._thread_channel:
            req = self._thread_channel.create_question(
                question=question, options=options or [],
                context=context or {}, timeout=300,
            )
            result = req.wait(timeout_seconds=300)
            self._thread_channel._pending.pop(req.id, None)
            return result.get("feedback", "") or ""

        if has_clients and self.streaming:
            await self._push_event("agent.question", {
                "id": question_id,
                "question": question,
                "options": options or [],
                "context": context or {},
            })
            try:
                response = await asyncio.wait_for(
                    self._await_resolution(question_id, options or []),
                    timeout=300
                )
            except asyncio.TimeoutError:
                response = ""
        elif self.terminal.available:
            response = await self.terminal.prompt_question(
                question_id, question, options or [], 300
            )
        else:
            response = ""

        return response if response else ""

    # ==================================================================
    # Control methods
    # ==================================================================

    def pause(self):
        """Pause execution after current step."""
        self._paused = True

    def resume(self):
        """Resume from pause."""
        self._paused = False

    def abort(self):
        """Gracefully abort execution (save checkpoint)."""
        self._aborted = True

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def has_pending_approval(self) -> bool:
        return self._pending_approval is not None

    def get_pending_approval(self) -> Optional[dict]:
        """Get pending approval info for API consumption."""
        if not self._pending_approval:
            return None
        req = self._pending_approval
        return {
            "id": req.id,
            "type": req.type.value,
            "description": req.description,
            "options": req.options,
            "timeout": req.timeout_seconds,
            "created_at": req.created_at,
        }

    # ==================================================================
    # Command queue — called from SessionManager
    # ==================================================================

    async def enqueue_command(self, cmd: UserCommand):
        """Enqueue a user command from any channel."""
        try:
            self._command_queue.put_nowait(cmd)
        except asyncio.QueueFull:
            pass  # Drop if queue is full (client should retry)

    async def resolve_approval(self, approval_id: str, resolution: str,
                               user_note: str = ""):
        """Resolve a pending approval from user input."""
        future_key = f'_future_{approval_id}'
        if hasattr(self, future_key):
            future: asyncio.Future = getattr(self, future_key)
            if not future.done():
                future.set_result({
                    "resolution": resolution,
                    "note": user_note
                })

    # ==================================================================
    # Hook implementations
    # ==================================================================

    async def _check_approval_gate(self, action: dict, state) -> bool:
        """Check if action needs approval before execution."""
        approval_type = self._classify_action(action, state)
        if approval_type is None:
            return True

        # Check auto-approve policy
        if self.auto_approve_policy.get(approval_type, False):
            return True

        # Build approval request
        request = ApprovalRequest(
            id=f"apr_{int(time.time())}_{self._step_counter}",
            type=approval_type,
            description=self._build_approval_description(action, approval_type),
            details={
                "tool": action.get("tool", ""),
                "params_summary": str(action.get("params", {}))[:300],
                "iteration": getattr(state, 'current_iteration', 0),
                "modified_files": list(getattr(state, 'modified_files', [])),
            },
            timeout_seconds=self._get_timeout(approval_type),
        )

        resolution = await self.wait_for_approval(request)

        if resolution == "approve_all":
            self.auto_approve_policy[approval_type] = True
            return True
        return resolution == "approve"

    async def _publish_pre_action_state(self, action: dict, state):
        """Publish state before action execution."""
        if not self.streaming:
            return
        await self._push_event("agent.decided", {
            "thought": action.get("thought", "")[:300],
            "tool": action.get("tool", ""),
            "params_summary": str(action.get("params", {}))[:200],
        }, state=state)

    async def _publish_post_action_state(self, action: dict, result, state):
        """Publish state after action execution."""
        if not self.streaming:
            return
        tool = action.get("tool", "")
        success = getattr(result, 'success', False)

        event_data = {
            "tool": tool,
            "success": success,
            "duration_ms": getattr(result, 'duration_ms', 0) if hasattr(result, 'duration_ms') else 0,
            "output_preview": (getattr(result, 'output', '') or '')[:500],
            "error": (getattr(result, 'error', '') or '')[:300],
        }

        # Enrich with structured data for specific tools
        if tool == "file_edit" and success:
            event_data["diff_preview"] = (getattr(result, 'output', '') or '')[:2000]
            event_data["file"] = action.get("params", {}).get("path", "")
        elif tool == "test_run":
            event_data["test_summary"] = getattr(result, 'structured', {})

        await self._push_event(
            "tool.completed" if success else "tool.error",
            event_data, state=state
        )

    async def _check_artifact_notification(self, action: dict, result, state):
        """Notify when significant artifacts are created."""
        if not self.streaming:
            return
        tool = action.get("tool", "")
        if tool in ("file_write", "file_edit") and getattr(result, 'success', False):
            file_path = action.get("params", {}).get("path", "")
            if file_path:
                await self._push_event("artifact.created", {
                    "file": file_path,
                    "action": tool,
                    "iteration": getattr(state, 'current_iteration', 0),
                }, state=state)

    async def _check_test_notification(self, action: dict, result, state):
        """Notify when test results change significantly."""
        if not self.streaming:
            return
        if action.get("tool") == "test_run":
            structured = getattr(result, 'structured', {})
            failed = structured.get("failed", 0)
            await self._push_event("test.result", {
                "passed": structured.get("passed", 0),
                "failed": failed,
                "collected": structured.get("collected", 0),
                "status": "passing" if failed == 0 else "failing",
            }, state=state)

    # ==================================================================
    # Command handling
    # ==================================================================

    async def _handle_command(self, cmd: UserCommand, state):
        """Process a user command."""
        if cmd.type == CommandType.PAUSE:
            self.pause()
            if self.streaming:
                await self._push_event("control.paused", {
                    "reason": cmd.data.get("reason", "User requested pause"),
                }, state=state)

        elif cmd.type == CommandType.RESUME:
            self.resume()
            if self.streaming:
                await self._push_event("control.resumed", {}, state=state)

        elif cmd.type == CommandType.ABORT:
            self.abort()
            if hasattr(state, 'status'):
                state.status = "ABORTED"
            if self.streaming:
                await self._push_event("control.aborted", {
                    "reason": cmd.data.get("reason", "User aborted execution"),
                }, state=state)

        elif cmd.type == CommandType.REDIRECT:
            new_focus = cmd.data.get("focus", "")
            if new_focus and hasattr(state, 'task_description'):
                state.task_description = (
                    f"{state.task_description}\n[USER REDIRECT at iter "
                    f"{getattr(state, 'current_iteration', 0)}]: {new_focus}"
                )

        elif cmd.type == CommandType.INJECT_CONTEXT:
            context_text = cmd.data.get("context", "")
            if context_text:
                if not hasattr(state, '_injected_contexts'):
                    state._injected_contexts = []
                state._injected_contexts.append({
                    "content": context_text,
                    "timestamp": datetime.now().isoformat(),
                    "source": "user",
                })

        elif cmd.type == CommandType.RETRY:
            hint = cmd.data.get("hint", "")
            if hint and hasattr(state, '_injected_contexts'):
                state._injected_contexts.append({
                    "content": f"RETRY HINT: {hint}",
                    "timestamp": datetime.now().isoformat(),
                    "source": "user_retry",
                })

    # ==================================================================
    # Private helpers
    # ==================================================================

    def _classify_action(self, action: dict, state) -> Optional[ApprovalType]:
        """Classify an action to determine if it needs approval."""
        tool = action.get("tool", "")

        if tool == "shell_run":
            cmd = action.get("params", {}).get("command", "")
            for pattern in self.DESTRUCTIVE_PATTERNS:
                if re.search(pattern, cmd):
                    return ApprovalType.DESTRUCTIVE_SHELL

        if tool == "file_edit":
            old = action.get("params", {}).get("old_string", "")
            if len(old.split("\n")) > 50:
                return ApprovalType.LARGE_EDIT

        return None

    def _build_approval_description(self, action: dict,
                                     atype: ApprovalType) -> str:
        """Build human-readable approval description."""
        descriptions = {
            ApprovalType.DESTRUCTIVE_SHELL:
                f"Potentially dangerous shell command:\n"
                f"  `{action.get('params', {}).get('command', '')[:200]}`",
            ApprovalType.LARGE_EDIT:
                f"Large file edit ({len(action.get('params', {}).get('old_string', '').split(chr(10)))} lines) "
                f"in `{action.get('params', {}).get('path', '')}`",
            ApprovalType.UNCERTAIN_FIX:
                f"Low-confidence code fix in `{action.get('params', {}).get('path', '')}`",
            ApprovalType.UNVERIFIED_EDIT:
                f"Editing file that hasn't been read: `{action.get('params', {}).get('path', '')}`",
        }
        return descriptions.get(atype, f"Action requires approval: {action.get('tool', '')}")

    def _get_timeout(self, atype: ApprovalType) -> int:
        """Get timeout for an approval type."""
        timeouts = {
            ApprovalType.DESTRUCTIVE_SHELL: 120,
            ApprovalType.LARGE_EDIT: 60,
            ApprovalType.UNCERTAIN_FIX: 300,
            ApprovalType.UNVERIFIED_EDIT: 60,
            ApprovalType.EXTERNAL_API_CALL: 60,
            ApprovalType.FILE_DELETE: 60,
            ApprovalType.GIT_OPERATION: 60,
            ApprovalType.CONFIG_CHANGE: 60,
        }
        return timeouts.get(atype, 120)

    async def _await_resolution(self, request_id: str,
                                 options: list[str]) -> str:
        """Create a future and wait for external resolution."""
        future: asyncio.Future = asyncio.Future()
        future_key = f'_future_{request_id}'
        setattr(self, future_key, future)
        try:
            result = await future
            if isinstance(result, dict):
                return result.get("resolution", options[1] if len(options) > 1 else "deny")
            return str(result)
        finally:
            try:
                delattr(self, future_key)
            except AttributeError:
                pass

    async def _push_event(self, event_type: str, data: dict, state=None):
        """Push an event to the streaming server with standard metadata."""
        if not self.streaming:
            return
        task_id = ""
        iteration = 0
        if state:
            task_id = getattr(state, 'task_id', '')
            iteration = getattr(state, 'current_iteration', 0)

        await self.streaming.push_event({
            "type": event_type,
            "task_id": task_id,
            "iteration": iteration,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })


# ============================================================================
# Progress Streamer
# ============================================================================

class ProgressStreamer:
    """Publishes rich progress snapshots at configurable intervals.

    Throttled: only publishes when interval has elapsed since last snapshot,
    unless force=True is passed.
    """

    def __init__(self, streaming_server=None, session_manager=None,
                 snapshot_interval_ms: int = 500):
        self.streaming = streaming_server
        self.session_mgr = session_manager
        self.interval_ms = snapshot_interval_ms
        self._last_snapshot_time: float = 0.0

    async def publish(self, state, force: bool = False):
        """Publish a progress snapshot if interval elapsed (or forced).

        Args:
            state: AgentLoopState instance
            force: If True, publish immediately regardless of interval
        """
        now = time.time() * 1000
        if not force and (now - self._last_snapshot_time) < self.interval_ms:
            return
        self._last_snapshot_time = now

        # Build snapshot
        tr = getattr(state, 'test_results', {}) or {}
        actions, obs = state.get_recent_history(5) if hasattr(state, 'get_recent_history') else ([], [])

        # Detect phase
        phase = "unknown"
        try:
            from .context import PhaseDetector
            phase = PhaseDetector.detect(state)
        except Exception:
            pass

        snapshot = {
            "task_id": getattr(state, 'task_id', ''),
            "status": getattr(state, 'status', ''),
            "iteration": getattr(state, 'current_iteration', 0),
            "max_iterations": getattr(state, 'max_iterations', 50),
            "phase": phase,
            "progress_pct": state.progress_ratio() if hasattr(state, 'progress_ratio') else 0.0,
            "modified_files": list(getattr(state, 'modified_files', [])),
            "test_summary": {
                "passed": tr.get("passed", 0),
                "failed": tr.get("failed", 0),
                "collected": tr.get("collected", 0),
            } if tr else None,
            "recent_steps": [
                {
                    "iteration": a.get("iteration", 0) if isinstance(a, dict) else 0,
                    "tool": a.get("tool", "") if isinstance(a, dict) else "",
                    "thought": (a.get("thought", "") or "")[:200] if isinstance(a, dict) else "",
                    "success": o.get("success", False) if isinstance(o, dict) else None,
                    "output_preview": (o.get("output", "") or "")[:300] if isinstance(o, dict) else "",
                }
                for a, o in zip(actions[-5:], obs[-5:])
            ] if actions else [],
            "timestamp": datetime.now().isoformat(),
        }

        # Push to streaming and broadcast to session clients
        if self.streaming:
            await self.streaming.push_event({
                "type": "progress.snapshot",
                "task_id": snapshot["task_id"],
                "data": snapshot,
                "timestamp": snapshot["timestamp"],
            })

        if self.session_mgr:
            await self.session_mgr.broadcast(snapshot["task_id"], {
                "type": "progress.snapshot",
                "data": snapshot,
            })


# ============================================================================
# Terminal Channel — CLI-based feedback when no WebSocket is connected
# ============================================================================

class TerminalChannel:
    """Provides CLI-based approval/review/question interaction via stdin/stdout.

    When the agent is running in interactive CLI mode without WebSocket clients,
    this channel prints prompts to stdout and reads responses from stdin
    (using a background thread to avoid blocking the asyncio event loop).
    """

    def __init__(self):
        self._input_queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._is_tty = sys.stdin.isatty()

    @property
    def available(self) -> bool:
        return self._is_tty

    def start(self):
        """Start the background stdin reader thread."""
        if not self._is_tty:
            return
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_stdin, daemon=True)
        self._reader_thread.start()

    def stop(self):
        self._running = False

    def _read_stdin(self):
        """Background thread: reads lines from stdin and puts them in the queue."""
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped:
                    try:
                        self._input_queue.put_nowait(stripped)
                    except asyncio.QueueFull:
                        pass
            except (EOFError, KeyboardInterrupt):
                break

    async def read_line(self, timeout_seconds: int = 300) -> str:
        """Read a single line from the terminal input queue."""
        try:
            return await asyncio.wait_for(self._input_queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return ""

    async def prompt_approval(self, approval_id: str, description: str,
                               options: list[str], timeout: int = 120) -> str:
        """Prompt the user in the terminal for an approval decision."""
        self._print_header("APPROVAL REQUIRED")
        print(f"\n{description}\n")
        if options:
            print("Options:", " | ".join(options))
        print(f"(auto-deny in {timeout}s)\n")
        print("Your response: ", end="", flush=True)

        response = await self.read_line(timeout)
        response_lower = response.lower()

        if any(word in response_lower for word in ("approve_all", "approve all", "approveall")):
            return "approve_all"
        if "deny" in response_lower:
            return "deny"
        if "approve" in response_lower:
            return "approve"
        return "deny" if response else "auto_deny"

    async def prompt_review(self, review_id: str, phase: str, title: str,
                             quality_score: str, timeout: int = 600) -> dict:
        """Prompt the user in the terminal for a phase review decision."""
        self._print_header(f"PHASE REVIEW — {phase.upper()}")
        print(f"\n  Title: {title}")
        print(f"  Quality: {quality_score}")
        print(f"\n  (auto-approve in {timeout}s)\n")
        print("  [A]pprove  [R]evise (with feedback)  Re[J]ect")
        print("\nYour choice: ", end="", flush=True)

        response = await self.read_line(timeout)
        r = response.lower()

        if r.startswith("j"):
            print("\nRejection reason: ", end="", flush=True)
            reason = await self.read_line(120)
            return {"decision": "reject", "feedback": reason, "suggestions": []}
        elif r.startswith("r"):
            print("\nWhat needs to be fixed: ", end="", flush=True)
            feedback = await self.read_line(300)
            print("Specific suggestions (one per line, empty line to finish):")
            suggestions = []
            while True:
                s = await self.read_line(120)
                if not s:
                    break
                suggestions.append(s)
            return {"decision": "revise", "feedback": feedback, "suggestions": suggestions}
        else:
            print("\nOptional comment (or press Enter): ", end="", flush=True)
            note = await self.read_line(30)
            return {"decision": "approve", "feedback": note, "suggestions": []}

    async def prompt_question(self, question_id: str, question: str,
                               options: list[str], timeout: int = 300) -> str:
        """Prompt the user in the terminal with an agent question."""
        self._print_header("AGENT QUESTION")
        print(f"\n  {question}\n")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  [{i}] {opt}")
            print(f"\nChoose 1-{len(options)} or type answer: ", end="", flush=True)
        else:
            print("Your answer: ", end="", flush=True)

        response = await self.read_line(timeout)
        if response.isdigit() and options and 1 <= int(response) <= len(options):
            return options[int(response) - 1]
        return response

    @staticmethod
    def _print_header(text: str):
        print(f"\n{'═' * 60}")
        print(f"  {text}")
        print(f"{'═' * 60}")
