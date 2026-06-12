"""Tests for DevAgent V2 observability & UX enhancements."""

import os
import sys
import json
import asyncio
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.observability import (
    TaskRecord, StreamingServer, ExecutionReplay,
    HumanInTheLoop, TaskHistoryManager,
)


# ============================================================================
# Helpers
# ============================================================================

def make_event_log(tmpdir: str, task_id: str = "task_test_001"):
    """Create a sample event log file."""
    log_dir = os.path.join(tmpdir, task_id)
    os.makedirs(log_dir, exist_ok=True)
    events = [
        {"type": "task.started", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:00", "iteration": 0,
         "data": {"task": "Fix the bug"}},
        {"type": "tool.completed", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:01", "iteration": 1,
         "data": {"tool": "grep_text", "success": True}},
        {"type": "tool.completed", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:03", "iteration": 2,
         "data": {"tool": "file_read", "success": True}},
        {"type": "tool.completed", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:05", "iteration": 3,
         "data": {"tool": "file_edit", "success": True}},
        {"type": "tool.completed", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:07", "iteration": 4,
         "data": {"tool": "test_run", "success": True}},
        {"type": "task.completed", "task_id": task_id,
         "timestamp": "2026-01-01T00:00:08", "iteration": 5,
         "data": {"reason": "all_tests_pass"}},
    ]
    with open(os.path.join(log_dir, "events.jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return log_dir


# ============================================================================
# Test TaskRecord
# ============================================================================

class TestTaskRecord:
    def test_record_creation(self):
        record = TaskRecord(
            task_id="task_001",
            task_type="agentic",
            task_description="Fix login bug",
            status="COMPLETED",
            iterations=12,
            files_modified=["src/auth.py"],
            test_passed=5,
            test_failed=0,
            duration_sec=23.5,
        )
        d = record.to_dict()
        assert d["task_id"] == "task_001"
        assert d["status"] == "COMPLETED"
        assert d["iterations"] == 12
        assert "src/auth.py" in d["files_modified"]
        assert d["duration_sec"] == 23.5

    def test_defaults(self):
        record = TaskRecord(task_id="t1", task_type="agentic")
        assert record.status == "RUNNING"
        assert record.iterations == 0
        assert record.duration_sec == 0.0


# ============================================================================
# Test StreamingServer
# ============================================================================

class TestStreamingServer:
    def test_register_client(self):
        ss = StreamingServer()
        q = asyncio.run(ss.register_client("task_001"))
        assert q is not None
        assert q.empty()

    def test_push_event(self):
        ss = StreamingServer()
        q = asyncio.run(ss.register_client("task_001"))
        asyncio.run(ss.push_event({
            "type": "tool.completed", "task_id": "task_001",
            "data": {"tool": "grep_text"}, "iteration": 1
        }))
        event = q.get_nowait()
        assert event["type"] == "tool.completed"

    def test_unregister_client(self):
        ss = StreamingServer()
        q = asyncio.run(ss.register_client("task_001"))
        asyncio.run(ss.unregister_client("task_001", q))
        assert len(ss._queues.get("task_001", [])) == 0

    def test_event_counting(self):
        ss = StreamingServer()
        asyncio.run(ss.push_event({"type": "task.started", "task_id": "t1", "data": {}, "iteration": 0}))
        asyncio.run(ss.push_event({"type": "tool.completed", "task_id": "t1", "data": {}, "iteration": 1}))
        assert ss._event_counts["t1"] == 2


# ============================================================================
# Test ExecutionReplay
# ============================================================================

class TestExecutionReplay:
    def test_load_events(self):
        tmp = tempfile.mkdtemp()
        make_event_log(tmp, "task_001")
        replay = ExecutionReplay(os.path.join(tmp))
        events = replay.load_events("task_001")
        assert len(events) == 6
        assert events[0]["type"] == "task.started"
        assert events[-1]["type"] == "task.completed"

    def test_get_task_ids(self):
        tmp = tempfile.mkdtemp()
        make_event_log(tmp, "task_a")
        make_event_log(tmp, "task_b")
        replay = ExecutionReplay(os.path.join(tmp))
        ids = replay.get_task_ids()
        assert "task_a" in ids
        assert "task_b" in ids

    def test_load_empty(self):
        replay = ExecutionReplay("/nonexistent/path")
        events = replay.load_events("nonexistent")
        assert events == []

    def test_get_task_summary(self):
        tmp = tempfile.mkdtemp()
        make_event_log(tmp, "task_001")
        replay = ExecutionReplay(os.path.join(tmp))
        summary = replay.get_task_summary("task_001")
        assert summary is not None
        assert summary.task_id == "task_001"
        assert summary.status == "COMPLETED"
        assert summary.iterations == 5

    def test_replay_generator(self):
        tmp = tempfile.mkdtemp()
        make_event_log(tmp, "task_001")
        replay = ExecutionReplay(os.path.join(tmp))

        async def collect():
            events = []
            async for e in replay.replay("task_001", speed=100):
                events.append(e)
            return events

        events = asyncio.run(collect())
        assert len(events) == 6


# ============================================================================
# Test HumanInTheLoop
# ============================================================================

class TestHumanInTheLoop:
    def test_destructive_command_detected(self):
        hitl = HumanInTheLoop()
        action = {"tool": "shell_run", "params": {"command": "rm -rf /tmp/test"}}
        point = hitl.needs_approval(action, None)
        assert point == "destructive_shell"

    def test_normal_command_no_approval(self):
        hitl = HumanInTheLoop()
        action = {"tool": "shell_run", "params": {"command": "echo hello"}}
        point = hitl.needs_approval(action, None)
        assert point is None

    def test_large_edit_detected(self):
        hitl = HumanInTheLoop()
        big_string = "\n".join(["line"] * 60)
        action = {
            "tool": "file_edit",
            "params": {"old_string": big_string, "new_string": "replaced"}
        }
        point = hitl.needs_approval(action, None)
        assert point == "large_edit"

    def test_normal_edit_no_approval(self):
        hitl = HumanInTheLoop()
        action = {
            "tool": "file_edit",
            "params": {"old_string": "x = 1", "new_string": "x = 2"}
        }
        point = hitl.needs_approval(action, None)
        assert point is None

    def test_auto_approve(self):
        hitl = HumanInTheLoop()
        hitl.auto_approve("destructive_shell")
        action = {"tool": "shell_run", "params": {"command": "rm -rf /tmp/test"}}
        point = hitl.needs_approval(action, None)
        assert point == "destructive_shell"  # Still detected, but will auto-approve

    def test_list_pending_empty(self):
        hitl = HumanInTheLoop()
        pending = hitl.list_pending()
        assert len(pending) == 0


# ============================================================================
# Test TaskHistoryManager
# ============================================================================

class TestTaskHistoryManager:
    def test_record_and_retrieve(self):
        tmp = tempfile.mkdtemp()
        mgr = TaskHistoryManager(os.path.join(tmp, "history"))

        record = TaskRecord(
            task_id="task_001",
            task_type="agentic",
            task_description="Fix login bug",
            status="COMPLETED",
            iterations=8,
            duration_sec=15.0,
            test_passed=3,
        )
        mgr.record_task(record)

        retrieved = mgr.get_task("task_001")
        assert retrieved is not None
        assert retrieved.status == "COMPLETED"
        assert retrieved.iterations == 8

    def test_list_tasks(self):
        tmp = tempfile.mkdtemp()
        mgr = TaskHistoryManager(os.path.join(tmp, "history"))

        for i in range(5):
            mgr.record_task(TaskRecord(
                task_id=f"task_{i:03d}",
                task_type="agentic",
                status="COMPLETED" if i % 2 == 0 else "FAILED",
                duration_sec=10.0 + i,
            ))

        tasks = mgr.list_tasks(limit=10)
        assert len(tasks) == 5

        failed = mgr.list_tasks(limit=10, status="FAILED")
        assert len(failed) == 2

    def test_search(self):
        tmp = tempfile.mkdtemp()
        mgr = TaskHistoryManager(os.path.join(tmp, "history"))
        mgr.record_task(TaskRecord(
            task_id="task_001", task_type="agentic",
            task_description="Fix the login validation bug",
            status="COMPLETED",
        ))
        mgr.record_task(TaskRecord(
            task_id="task_002", task_type="agentic",
            task_description="Add user registration feature",
            status="COMPLETED",
        ))

        results = mgr.search("login")
        assert len(results) == 1
        assert results[0].task_id == "task_001"

    def test_get_metrics(self):
        tmp = tempfile.mkdtemp()
        mgr = TaskHistoryManager(os.path.join(tmp, "history"))

        for i in range(10):
            mgr.record_task(TaskRecord(
                task_id=f"task_{i:03d}",
                task_type="agentic",
                status="COMPLETED" if i < 7 else "FAILED",
                iterations=10 + i,
                duration_sec=20.0 + i * 2,
            ))

        metrics = mgr.get_metrics()
        assert metrics["total_tasks"] == 10
        assert metrics["completed"] == 7
        assert metrics["failed"] == 3
        assert metrics["success_rate"] == 70.0
        assert metrics["avg_iterations"] > 0

    def test_get_trend(self):
        tmp = tempfile.mkdtemp()
        mgr = TaskHistoryManager(os.path.join(tmp, "history"))

        for i in range(8):
            mgr.record_task(TaskRecord(
                task_id=f"task_{i:03d}",
                task_type="agentic",
                status="COMPLETED" if i >= 2 else "FAILED",
                iterations=5,
                duration_sec=10.0,
            ))

        trend = mgr.get_trend("success_rate")
        assert len(trend) == 8
        # Last value should be 100% (last 5 tasks: 3 FAILED + 5 COMPLETED → wait)
        # Actually: tasks 0,1=FAILED, 2-7=COMPLETED
        # Win 5 at end: 2,3,4,5,6,7 = all COMPLETED → 100%
        assert trend[-1]["value"] == 100.0

    def test_record_from_event_log(self):
        tmp = tempfile.mkdtemp()
        log_dir = make_event_log(tmp, "task_evt_001")
        # Use parent of log_dir so ExecutionReplay can find task_evt_001/ dir
        replay = ExecutionReplay(os.path.join(tmp))
        summary = replay.get_task_summary("task_evt_001")
        assert summary is not None
        assert summary.task_id == "task_evt_001"
        assert summary.status == "COMPLETED"


# ============================================================================
# Test from event log summary
# ============================================================================

class TestSummaryFromEvents:
    def test_summary_from_replay(self):
        tmp = tempfile.mkdtemp()
        make_event_log(tmp, "task_001")
        replay = ExecutionReplay(os.path.join(tmp))
        summary = replay.get_task_summary("task_001")
        assert summary is not None
        assert summary.status == "COMPLETED"
        assert summary.iterations >= 4
