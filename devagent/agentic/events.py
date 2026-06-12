"""Event system for DevAgent V2 — streaming observability.

Provides typed events for every agent action, an async event bus,
and standard subscribers (SSE push, file logger, state snaphot).
"""

import os
import json
import uuid
import asyncio
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
from typing import Callable, Optional


class EventType(str, Enum):
    # Workflow
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # Agent
    AGENT_THINKING = "agent.thinking"
    AGENT_DECIDED = "agent.decided"

    # Tool
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_ERROR = "tool.error"

    # State
    FILE_MODIFIED = "file.modified"
    TEST_RESULT = "test.result"

    # Quality
    GATE_CHECK = "gate.check"
    GATE_PASSED = "gate.passed"
    GATE_FAILED = "gate.failed"

    # Human
    HUMAN_APPROVAL_REQUESTED = "human.approval.requested"
    HUMAN_APPROVAL_RECEIVED = "human.approval.received"

    # Errors
    ERROR_OCCURRED = "error.occurred"
    WARNING_RAISED = "warning.raised"


@dataclass
class DevAgentEvent:
    """A single observable event in the agent lifecycle."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: EventType = EventType.AGENT_THINKING
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    task_id: str = ""
    iteration: int = 0
    data: dict = field(default_factory=dict)
    parent_event_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "iteration": self.iteration,
            "data": self.data,
            "parent_event_id": self.parent_event_id,
        }

    def to_sse(self) -> str:
        return f"event: {self.type.value}\ndata: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


class EventBus:
    """Async publish-subscribe event bus."""

    def __init__(self):
        self._sync: dict[EventType, list[Callable]] = defaultdict(list)
        self._async: dict[EventType, list[Callable]] = defaultdict(list)
        self._all: dict[EventType, list[Callable]] = defaultdict(list)
        self._events: list[DevAgentEvent] = []

    def subscribe(self, event_type: EventType, handler: Callable, mode: str = "sync"):
        if mode == "async":
            self._async[event_type].append(handler)
        else:
            self._sync[event_type].append(handler)

    def subscribe_all(self, handler: Callable, mode: str = "sync"):
        self._all[EventType] = self._all.get(EventType, []) + [handler]
        for et in EventType:
            self.subscribe(et, handler, mode)

    async def publish(self, event: DevAgentEvent):
        self._events.append(event)

        for handler in self._sync.get(event.type, []) + self._all.get(EventType, []):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

        for handler in self._async.get(event.type, []):
            try:
                asyncio.create_task(self._safe_async(handler, event))
            except Exception:
                pass

    @staticmethod
    async def _safe_async(handler, event):
        try:
            await handler(event)
        except Exception:
            pass

    def replay(self) -> list[DevAgentEvent]:
        return list(self._events)

    def clear(self):
        self._events.clear()


# ============================================================================
# Standard Subscribers
# ============================================================================

class SSEPushSubscriber:
    """Pushes events via a callback suitable for SSE/WebSocket."""

    def __init__(self, send_fn: Callable):
        self.send_fn = send_fn

    async def on_event(self, event: DevAgentEvent):
        await self.send_fn(event.to_sse())


class FileLogger:
    """Persists events to a JSONL file."""

    def __init__(self, log_dir: str = ".devagent/tasks"):
        self.log_dir = log_dir

    def on_event(self, event: DevAgentEvent):
        task_dir = os.path.join(self.log_dir, event.task_id)
        os.makedirs(task_dir, exist_ok=True)
        log_path = os.path.join(task_dir, "events.jsonl")
        with open(log_path, "a") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


class ConsoleEmitter:
    """Prints events to console for CLI mode."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._icons = {
            EventType.TASK_STARTED: "🚀",
            EventType.TASK_COMPLETED: "✅",
            EventType.TASK_FAILED: "❌",
            EventType.AGENT_THINKING: "🤔",
            EventType.AGENT_DECIDED: "💡",
            EventType.TOOL_STARTED: "🔧",
            EventType.TOOL_COMPLETED: "✓",
            EventType.TOOL_ERROR: "⚠",
            EventType.TEST_RESULT: "🧪",
            EventType.FILE_MODIFIED: "📝",
        }

    def on_event(self, event: DevAgentEvent):
        icon = self._icons.get(event.type, "•")
        if event.type == EventType.TOOL_COMPLETED:
            tool = event.data.get("tool", "")
            duration = event.data.get("duration_ms", 0)
            print(f"  {icon} {tool} ({duration:.0f}ms)")
        elif event.type == EventType.AGENT_DECIDED:
            tool = event.data.get("tool", "")
            thought = (event.data.get("thought", "") or "")[:80]
            print(f"  {icon} → {tool}: {thought}")
        elif event.type == EventType.TASK_COMPLETED:
            print(f"\n{icon} Task {event.task_id} completed")
        elif event.type == EventType.TASK_FAILED:
            print(f"\n{icon} Task {event.task_id} failed: {event.data.get('reason', '')}")
        elif event.type == EventType.ERROR_OCCURRED:
            print(f"  {icon} ERROR: {event.data.get('message', '')}")
        elif self.verbose:
            print(f"  {icon} [{event.type.value}] {event.data}")
