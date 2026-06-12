"""Thread-Safe Interaction Channel — bridges background agent threads with WebSocket loop.

When the agent runs in a background thread (separate asyncio loop), normal
asyncio.Future-based interaction cannot work because the WebSocket handler
is in uvicorn's event loop. This module provides thread-safe primitives.

Architecture:
  Background Thread                         Uvicorn Event Loop
  ┌──────────────────┐                     ┌──────────────────┐
  │ DevAgentCore     │ ──wait_for_human──► │ StreamingServer   │
  │                  │                     │       │            │
  │ threading.Event  │ ◄──set_result────── │ WebSocket handler │
  │   .wait()        │                     │  resolve_channel()│
  └──────────────────┘                     └──────────────────┘
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ThreadRequest:
    """A request from the agent that needs human response across threads."""
    id: str
    type: str  # "approval" | "review" | "question"
    description: str
    options: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Thread-safe signaling
    _event: threading.Event = field(default_factory=threading.Event)
    _response: dict = field(default_factory=dict)

    def wait(self, timeout_seconds: float = 600) -> dict:
        """Block until human responds or timeout."""
        signalled = self._event.wait(timeout=timeout_seconds)
        if not signalled:
            return {"decision": "timeout", "feedback": "No response within timeout", "suggestions": []}
        return self._response

    def resolve(self, decision: str, feedback: str = "", suggestions: list = None):
        """Signal the waiting thread with the human's response."""
        self._response = {
            "decision": decision,
            "feedback": feedback or "",
            "suggestions": suggestions or [],
        }
        self._event.set()


class ThreadChannel:
    """Thread-safe channel for human interaction from background threads.

    Auto-detects when no clients are connected and resolves immediately
    to avoid blocking indefinitely.

    Usage in DevAgentCore background thread:
        channel = ThreadChannel(session_manager=session_mgr)
        request = channel.create_review(...)
        result = request.wait(timeout=120)  # blocks until resolved or auto-approve

    Usage in WebSocket handler (uvicorn event loop):
        channel.resolve(request_id, decision, feedback, suggestions)
    """

    def __init__(self, streaming_server=None, session_manager=None):
        self._pending: dict[str, ThreadRequest] = {}
        self._lock = threading.Lock()
        self.streaming = streaming_server
        self.session_mgr = session_manager

    def _has_clients(self) -> bool:
        """Check if any WebSocket clients are connected.

        Waits up to 3 seconds (polling every 0.5s) for a client to connect.
        This gives the WebSocket handler time to register before auto-approving.
        """
        if not self.session_mgr:
            return False

        # Check immediately first (fast path)
        if self._check_sessions():
            return True

        # Wait up to 5 seconds for a WS client to connect
        # (bg agent reaches review quickly now — 5s is plenty for handshake)
        for _ in range(20):  # 20 × 0.25s = 5s
            time.sleep(0.25)
            if self._check_sessions():
                return True

        return False

    def _check_sessions(self) -> bool:
        """Raw check of SessionManager sessions (thread-safe under GIL)."""
        try:
            sessions = getattr(self.session_mgr, '_sessions', {})
            for task_sessions in sessions.values():
                for s in task_sessions.values():
                    if getattr(s, 'is_connected', False):
                        return True
        except Exception:
            pass
        return False

    def create_approval(self, description: str, details: dict = None,
                        options: list = None, timeout: int = 120) -> ThreadRequest:
        """Create an approval request, auto-resolve if no clients."""
        req = ThreadRequest(
            id=f"apr_{uuid.uuid4().hex[:8]}",
            type="approval",
            description=description,
            options=options or ["approve", "deny"],
            details=details or {},
        )
        with self._lock:
            self._pending[req.id] = req

        if not self._has_clients():
            req.resolve("approve", "Auto-approved (no clients connected)")
            return req

        self._push_event("approval.requested", {
            "id": req.id, "description": description,
            "details": details or {}, "options": req.options, "timeout": timeout,
        })
        return req

    def create_review(self, phase: str, title: str, summary: str,
                      quality_score: str = "unknown",
                      timeout: int = 600) -> ThreadRequest:
        """Create a phase review request.

        In non-interactive mode (no WS clients): auto-approve immediately.
        In interactive mode (WS clients connected): wait for human response.

        Default timeout is 600s — enough for human to review.
        """
        req = ThreadRequest(
            id=f"rev_{uuid.uuid4().hex[:8]}",
            type="review",
            description=f"[{phase}] {title}",
            options=["approve", "revise", "reject"],
            details={"phase": phase, "title": title, "summary": summary,
                     "quality_score": quality_score},
        )
        with self._lock:
            self._pending[req.id] = req

        has_client = self._has_clients()

        if not has_client:
            req.resolve("approve", "Auto-approved (no reviewers connected)")
            return req

        self._push_event("review.requested", {
            "id": req.id, "phase": phase, "title": title,
            "summary": summary, "quality_score": quality_score,
        })
        return req

    def create_question(self, question: str, options: list = None,
                        context: dict = None, timeout: int = 300) -> ThreadRequest:
        """Create a question for the human, auto-answer if no clients."""
        req = ThreadRequest(
            id=f"q_{uuid.uuid4().hex[:8]}",
            type="question",
            description=question,
            options=options or [],
            details=context or {},
        )
        with self._lock:
            self._pending[req.id] = req

        if not self._has_clients():
            req.resolve("answered", "", [])
            return req

        self._push_event("agent.question", {
            "id": req.id, "question": question,
            "options": req.options, "context": context or {},
        })
        return req

    def create_feedback(self, phase: str, title: str, summary: str,
                        timeout: int = 1800) -> ThreadRequest:
        """Create an interactive feedback request for post-delivery revision.

        Unlike create_review which expects approve/revise/reject, this expects
        the human to provide free-form feedback (text) to guide DevAgent's
        changes. The decision field can be:
          - "continue" (default) — DevAgent will execute the feedback
          - "done" / "approve" — finish the revision loop
          - "timeout" — auto-complete after timeout

        The feedback text is what DevAgent executes as a change request.
        """
        req = ThreadRequest(
            id=f"fb_{uuid.uuid4().hex[:8]}",
            type="feedback",
            description=summary,
            options=["continue", "done", "approve"],
            details={
                "phase": phase, "title": title, "summary": summary,
                "instruction": (
                    "输入你的修改意见（中文/English），DevAgent 将自动实现修改。\n"
                    "输入 **done** / **完成** 结束修改。"
                ),
            },
        )
        with self._lock:
            self._pending[req.id] = req

        has_client = self._has_clients()

        if not has_client:
            req.resolve("done", "Auto-completed (no interactive clients)")
            return req

        self._push_event("review.requested", {
            "id": req.id, "phase": phase, "title": title,
            "summary": summary, "quality_score": "interactive_revision",
            "feedback_mode": True,  # Signal the client to show a text input
        })
        return req

    def resolve(self, request_id: str, decision: str,
                feedback: str = "", suggestions: list = None) -> bool:
        """Resolve a pending request. Thread-safe, callable from any thread."""
        with self._lock:
            req = self._pending.pop(request_id, None)
        if req is None:
            return False
        req.resolve(decision, feedback, suggestions)
        return True

    def get_pending(self) -> list[dict]:
        """Get list of pending requests."""
        with self._lock:
            return [
                {"id": r.id, "type": r.type, "description": r.description[:200],
                 "created_at": r.created_at}
                for r in self._pending.values()
            ]

    def _push_event(self, event_type: str, data: dict):
        """Push event to connected WebSocket clients via SessionManager.

        Uses direct queue insertion (thread-safe, works from any thread).
        """
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        if not self.session_mgr:
            return
        try:
            for task_sessions in getattr(self.session_mgr, '_sessions', {}).values():
                for s in task_sessions.values():
                    if getattr(s, 'is_connected', False):
                        try:
                            s.message_queue.put_nowait(event)
                        except Exception:
                            pass
        except Exception:
            pass
