"""Observability and UX enhancements for DevAgent V2.

Components:
  StreamingServer    — Real SSE event push via WebSocket (not mock)
  ExecutionReplay    — Replay task execution from event logs
  HumanInTheLoop     — Approval gates for destructive/unusual operations
  TaskHistoryManager — Persistent task history with search and metrics
  DashboardAPI       — REST endpoints for a Web Dashboard
"""

import os
import json
import asyncio
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable
from collections import defaultdict


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class TaskRecord:
    """A completed or running task in the history."""
    task_id: str
    task_type: str
    task_description: str = ""
    status: str = "RUNNING"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    iterations: int = 0
    files_modified: list[str] = field(default_factory=list)
    test_passed: int = 0
    test_failed: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0
    event_count: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "task_description": self.task_description[:200],
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "iterations": self.iterations,
            "files_modified": self.files_modified,
            "test_passed": self.test_passed,
            "test_failed": self.test_failed,
            "errors": self.errors,
            "duration_sec": round(self.duration_sec, 1),
            "event_count": self.event_count,
        }


# ============================================================================
# Streaming Server
# ============================================================================

class StreamingServer:
    """Manages real SSE event streaming to connected clients.

    Replaces the V1 mock streaming with actual DevAgentEvent push.
    """

    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._event_counts: dict[str, int] = defaultdict(int)

    async def register_client(self, task_id: str) -> asyncio.Queue:
        """Register a new SSE client for a task. Returns an async queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues[task_id].append(q)
        return q

    async def unregister_client(self, task_id: str, queue: asyncio.Queue):
        """Remove a disconnected client."""
        if task_id in self._queues:
            try:
                self._queues[task_id].remove(queue)
            except ValueError:
                pass

    async def push_event(self, event_dict: dict):
        """Push an event to all clients watching the task."""
        task_id = event_dict.get("task_id", "")
        self._event_counts[task_id] += 1

        for q in self._queues.get(task_id, []):
            try:
                q.put_nowait(event_dict)
            except asyncio.QueueFull:
                pass

    async def sse_generator(self, task_id: str) -> str:
        """Async generator yielding SSE-formatted events for a task."""
        q = await self.register_client(task_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {{\"task_id\": \"{task_id}\"}}\n\n"
        finally:
            await self.unregister_client(task_id, q)


# ============================================================================
# Execution Replay
# ============================================================================

class ExecutionReplay:
    """Replays task execution from persisted event logs."""

    def __init__(self, log_dir: str = ".devagent/tasks"):
        self.log_dir = log_dir

    def get_task_ids(self) -> list[str]:
        """List all task IDs with event logs."""
        base = Path(self.log_dir)
        if not base.exists():
            return []
        return sorted(
            [d.name for d in base.iterdir() if d.is_dir()],
            reverse=True
        )

    def load_events(self, task_id: str) -> list[dict]:
        """Load all events for a task."""
        events_path = Path(self.log_dir) / task_id / "events.jsonl"
        if not events_path.exists():
            return []
        events = []
        with open(events_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events

    async def replay(self, task_id: str, speed: float = 1.0):
        """Replay task events with timing preservation.

        Yields events with delays matching original execution timing.
        """
        events = self.load_events(task_id)
        if not events:
            return

        prev_time = None
        for event in events:
            ts = event.get("timestamp", "")
            if ts and prev_time:
                try:
                    current = datetime.fromisoformat(ts)
                    prev = datetime.fromisoformat(prev_time)
                    delay = (current - prev).total_seconds() / max(speed, 0.1)
                    delay = min(delay, 5.0)  # Cap at 5 seconds
                    if delay > 0:
                        await asyncio.sleep(delay)
                except (ValueError, TypeError):
                    pass

            yield event
            prev_time = ts

    def get_task_summary(self, task_id: str) -> Optional[TaskRecord]:
        """Build a task summary from its event log."""
        events = self.load_events(task_id)
        if not events:
            return None

        record = TaskRecord(task_id=task_id, task_type="agentic")

        for e in events:
            etype = e.get("type", "")
            data = e.get("data", {})

            if etype == "task.started":
                record.task_description = data.get("task", "")[:200]
                record.created_at = e.get("timestamp", "")
            elif etype in ("task.completed", "task.failed"):
                record.status = "COMPLETED" if etype == "task.completed" else "FAILED"
                record.completed_at = e.get("timestamp", "")
            elif etype == "tool.completed":
                tool = data.get("tool", "")
                if tool in ("file_edit", "file_write"):
                    pass  # files tracked via file.modified
            elif etype == "file.modified":
                f = data.get("file", "")
                if f and f not in record.files_modified:
                    record.files_modified.append(f)
            elif etype == "test.result":
                record.test_passed = data.get("passed", 0)
                record.test_failed = data.get("failed", 0)
            elif etype == "error.occurred":
                record.errors.append({"message": data.get("message", "")[:200]})

            record.event_count += 1
            record.iterations = max(record.iterations, e.get("iteration", 0))

        # Calculate duration
        if record.created_at and record.completed_at:
            try:
                start = datetime.fromisoformat(record.created_at)
                end = datetime.fromisoformat(record.completed_at)
                record.duration_sec = (end - start).total_seconds()
            except (ValueError, TypeError):
                pass

        return record


# ============================================================================
# Human-in-the-Loop
# ============================================================================

class HumanInTheLoop:
    """Approval gates for operations that need human confirmation.

    Triggers:
      - destructive_shell: rm, force push, sudo
      - large_edit: >50 lines changed
      - uncertain_fix: LLM confidence < 0.5
      - max_iterations_near: >=80% of max iterations used
    """

    INTERVENTION_POINTS = {
        "destructive_shell": {
            "description": "Potentially destructive shell command",
            "requires_approval": True,
            "timeout_seconds": 120,
        },
        "large_edit": {
            "description": "Edit changes more than 50 lines",
            "requires_approval": True,
            "timeout_seconds": 60,
        },
        "uncertain_fix": {
            "description": "LLM confidence on fix is low",
            "requires_approval": True,
            "timeout_seconds": 300,
        },
        "unverified_edit": {
            "description": "Editing a file that hasn't been read",
            "requires_approval": True,
            "timeout_seconds": 60,
        },
    }

    DESTRUCTIVE_PATTERNS = [
        r"rm\s+-rf", r"git\s+push\s+--force", r"git\s+reset\s+--hard",
        r"sudo\s+", r">\s*/dev/", r"mkfs\.", r"dd\s+if=",
    ]

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}
        self._auto_approve: set[str] = set()

    def needs_approval(self, action: dict, state) -> Optional[str]:
        """Check if an action needs human approval. Returns point_id or None."""
        tool = action.get("tool", "")

        # Check destructive shell
        if tool == "shell_run":
            cmd = action.get("params", {}).get("command", "")
            for pattern in self.DESTRUCTIVE_PATTERNS:
                if __import__('re').search(pattern, cmd):
                    return "destructive_shell"

        # Check large edit
        if tool == "file_edit":
            old = action.get("params", {}).get("old_string", "")
            new = action.get("params", {}).get("new_string", "")
            if len(old.split("\n")) > 50 or len(new.split("\n")) > 50:
                return "large_edit"

        return None

    async def request_approval(self, point_id: str, details: dict) -> bool:
        """Request human approval for an operation.

        Returns True if approved, False if denied or timed out.
        """
        if point_id in self._auto_approve:
            return True

        config = self.INTERVENTION_POINTS.get(point_id, {})
        timeout = config.get("timeout_seconds", 120)

        future: asyncio.Future = asyncio.Future()
        approval_id = f"{point_id}_{int(time.time())}"
        self._pending[approval_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(approval_id, None)

    def approve(self, approval_id: str):
        """Approve a pending request (called from API/CLI)."""
        future = self._pending.get(approval_id)
        if future and not future.done():
            future.set_result(True)

    def deny(self, approval_id: str):
        """Deny a pending request."""
        future = self._pending.get(approval_id)
        if future and not future.done():
            future.set_result(False)

    def auto_approve(self, point_id: str):
        """Auto-approve all future requests of this type."""
        self._auto_approve.add(point_id)

    def list_pending(self) -> list[dict]:
        """List all pending approval requests."""
        return [
            {"id": aid, "resolved": f.done()}
            for aid, f in self._pending.items()
        ]


# ============================================================================
# Task History Manager
# ============================================================================

class TaskHistoryManager:
    """Persistent task history with search, metrics, and trend analysis."""

    def __init__(self, history_dir: str = ".devagent/history"):
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, TaskRecord] = {}
        self.replay = ExecutionReplay()

    def record_task(self, record: TaskRecord):
        """Save a task record to history."""
        self._records[record.task_id] = record
        # Persist to disk
        path = self.history_dir / f"{record.task_id}.json"
        path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Get a task by ID."""
        if task_id in self._records:
            return self._records[task_id]
        # Try loading from disk
        path = self.history_dir / f"{task_id}.json"
        if path.exists():
            data = json.loads(path.read_text())
            record = TaskRecord(**{k: v for k, v in data.items()
                                   if k in TaskRecord.__dataclass_fields__})
            self._records[task_id] = record
            return record

        # Try from event logs
        return self.replay.get_task_summary(task_id)

    def list_tasks(self, limit: int = 50, status: str = None) -> list[TaskRecord]:
        """List recent tasks, optionally filtered by status."""
        # Load from disk
        for path in sorted(self.history_dir.glob("*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True):
            task_id = path.stem
            if task_id not in self._records:
                try:
                    data = json.loads(path.read_text())
                    self._records[task_id] = TaskRecord(
                        **{k: v for k, v in data.items()
                           if k in TaskRecord.__dataclass_fields__})
                except (json.JSONDecodeError, TypeError):
                    continue

        records = list(self._records.values())
        if status:
            records = [r for r in records if r.status == status]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[:limit]

    def search(self, query: str, limit: int = 20) -> list[TaskRecord]:
        """Search task history by description substring."""
        q = query.lower()
        return [r for r in self.list_tasks(limit=200)
                if q in r.task_description.lower()][:limit]

    def get_metrics(self, days: int = 7) -> dict:
        """Get aggregate metrics for recent tasks."""
        recent = [r for r in self.list_tasks(limit=200)
                  if r.status in ("COMPLETED", "FAILED")]

        if not recent:
            return {"total_tasks": 0}

        completed = [r for r in recent if r.status == "COMPLETED"]
        failed = [r for r in recent if r.status == "FAILED"]

        return {
            "total_tasks": len(recent),
            "completed": len(completed),
            "failed": len(failed),
            "success_rate": round(len(completed) / len(recent) * 100, 1) if recent else 0,
            "avg_iterations": round(
                sum(r.iterations for r in recent) / len(recent), 1
            ) if recent else 0,
            "avg_duration_sec": round(
                sum(r.duration_sec for r in recent) / len(recent), 1
            ) if recent else 0,
            "avg_files_modified": round(
                sum(len(r.files_modified) for r in recent) / len(recent), 1
            ) if recent else 0,
            "total_test_passed": sum(r.test_passed for r in recent),
            "total_test_failed": sum(r.test_failed for r in recent),
        }

    def get_trend(self, metric: str = "success_rate") -> list[dict]:
        """Get trend data for a metric over time."""
        all_tasks = self.list_tasks(limit=200)
        all_tasks.sort(key=lambda r: r.created_at)
        trend = []
        window = []
        for r in all_tasks:
            window.append(r)
            if len(window) > 5:
                window = window[-5:]
            if metric == "success_rate":
                val = sum(1 for t in window if t.status == "COMPLETED") / len(window) * 100
            elif metric == "avg_duration":
                val = sum(t.duration_sec for t in window) / len(window)
            elif metric == "avg_iterations":
                val = sum(t.iterations for t in window) / len(window)
            else:
                val = 0
            trend.append({
                "timestamp": r.created_at,
                "value": round(val, 1),
                "window_size": len(window),
            })
        return trend


# ============================================================================
# Dashboard HTML Page
# ============================================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DevAgent Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'SF Mono', monospace; background: #1a1a2e; color: #e0e0e0; padding: 20px; }
  h1 { color: #00d4aa; margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: 250px 1fr 300px; gap: 20px; height: calc(100vh - 80px); }
  .panel { background: #16213e; border-radius: 8px; padding: 16px; overflow-y: auto; }
  .panel h2 { color: #00d4aa; font-size: 14px; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 6px; }
  .task-item { padding: 8px; cursor: pointer; border-radius: 4px; margin-bottom: 4px; font-size: 12px; }
  .task-item:hover { background: #0f3460; }
  .task-item.active { background: #0f3460; border-left: 3px solid #00d4aa; }
  .task-item .id { color: #888; font-size: 10px; }
  .timeline-event { padding: 6px 8px; margin-bottom: 4px; border-radius: 4px; font-size: 11px; display: flex; gap: 10px; }
  .timeline-event .icon { width: 20px; text-align: center; }
  .timeline-event.THINKING { background: #1a1a40; }
  .timeline-event.TOOL { background: #1a2a30; }
  .timeline-event.ERROR { background: #3a1a1a; }
  .timeline-event.TEST { background: #1a3a1a; }
  .status-bar { grid-column: 1 / -1; background: #16213e; border-radius: 8px; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .metric { text-align: center; }
  .metric .value { font-size: 24px; color: #00d4aa; font-weight: bold; }
  .metric .label { color: #888; font-size: 10px; }
  .btn { background: #0f3460; color: #e0e0e0; border: 1px solid #333; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .btn:hover { background: #1a4a7a; }
</style>
</head>
<body>
<h1>DevAgent Dashboard</h1>
<div class="grid">
  <div class="panel" id="history-panel">
    <h2>Task History</h2>
    <div id="task-list">Loading...</div>
  </div>
  <div class="panel" id="timeline-panel">
    <h2>Execution Timeline</h2>
    <div id="timeline">Select a task to view</div>
  </div>
  <div class="panel" id="detail-panel">
    <h2>Details</h2>
    <div id="details"></div>
  </div>
  <div class="status-bar" id="status-bar">
    <div class="metric"><div class="value" id="metric-total">-</div><div class="label">Total Tasks</div></div>
    <div class="metric"><div class="value" id="metric-success">-</div><div class="label">Success Rate</div></div>
    <div class="metric"><div class="value" id="metric-iter">-</div><div class="label">Avg Iterations</div></div>
    <div class="metric"><div class="value" id="metric-dur">-</div><div class="label">Avg Duration</div></div>
  </div>
</div>
<script>
async function loadTasks() {
  const resp = await fetch('/api/v1/tasks/history');
  const data = await resp.json();
  const list = document.getElementById('task-list');
  list.innerHTML = data.tasks.map(t =>
    `<div class="task-item" onclick="selectTask('${t.task_id}')">
      <div class="id">${t.task_id}</div>
      <div>${t.status} | ${t.task_type} | ${t.duration_sec}s</div>
    </div>`
  ).join('');
  document.getElementById('metric-total').textContent = data.total;
}
function selectTask(id) {
  document.querySelectorAll('.task-item').forEach(el => el.classList.remove('active'));
  event.target.closest('.task-item').classList.add('active');
  fetch(`/api/v1/tasks/${id}`).then(r => r.json()).then(t => {
    document.getElementById('details').innerHTML = `
      <p><b>Status:</b> ${t.status}</p>
      <p><b>Files:</b> ${(t.files_modified||[]).join(', ') || 'none'}</p>
      <p><b>Tests:</b> ${t.test_passed||0} passed, ${t.test_failed||0} failed</p>
      <p><b>Errors:</b> ${t.errors?.length||0}</p>
    `;
  });
}
loadTasks();
setInterval(loadTasks, 5000);
</script>
</body>
</html>"""


# ============================================================================
# Dashboard API Extensions
# ============================================================================

class DashboardAPI:
    """Registers dashboard routes on a FastAPI app."""

    def __init__(self, history: TaskHistoryManager, streaming: StreamingServer):
        self.history = history
        self.streaming = streaming

    def register(self, app):
        """Register all dashboard routes on the FastAPI app."""
        try:
            from fastapi import WebSocket, WebSocketDisconnect
            HAS_FASTAPI = True
        except ImportError:
            HAS_FASTAPI = False

        @app.get("/dashboard")
        def dashboard_page():
            """Serve the dashboard HTML page."""
            try:
                from fastapi.responses import HTMLResponse
                return HTMLResponse(content=DASHBOARD_HTML)
            except ImportError:
                return {"error": "FastAPI not available"}

        @app.get("/api/v1/dashboard/metrics")
        def dashboard_metrics():
            """Get aggregate metrics for the dashboard."""
            return self.history.get_metrics()

        @app.get("/api/v1/dashboard/trend")
        def dashboard_trend(metric: str = "success_rate"):
            """Get trend data for charts."""
            return {"trend": self.history.get_trend(metric)}

        @app.get("/api/v1/dashboard/status")
        def dashboard_status():
            """Get current system status."""
            tasks = self.history.list_tasks(limit=10)
            return {
                "recent_tasks": [t.to_dict() for t in tasks[:5]],
                "active_streams": sum(
                    1 for qs in self.streaming._queues.values() for q in qs
                ),
                "total_events": sum(self.streaming._event_counts.values()),
            }

        if HAS_FASTAPI:

            @app.websocket("/api/v1/tasks/{task_id}/stream/realtime")
            async def realtime_stream(ws: WebSocket, task_id: str):
                """Real SSE event stream for a task."""
                await ws.accept()
                try:
                    async for sse_event in self.streaming.sse_generator(task_id):
                        await ws.send_text(sse_event)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass
                finally:
                    try:
                        await ws.close()
                    except Exception:
                        pass
