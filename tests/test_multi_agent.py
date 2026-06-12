"""Tests for Multi-Agent Collaboration — Coordinator + Worker."""

import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.multi_agent import (
    WorkerConfig, WorkerResult, FileConflict,
    SharedState, WorkerAgent, Coordinator,
    ConflictResolver,
)
from devagent.agentic.planning import SubTask, SubTaskType, ExecutionPlan


class TestWorkerConfig:
    def test_defaults(self):
        cfg = WorkerConfig(
            worker_id="w1", sub_task_id="ST-01",
            sub_task_description="Find bug",
            success_condition="Bug found"
        )
        assert cfg.max_iterations == 10
        assert cfg.file_allowlist == []


class TestFileConflict:
    def test_different_functions(self):
        c = FileConflict(
            file_path="test.py", worker_a="w1", worker_b="w2",
            a_content="def foo():\n    return 1\n",
            b_content="def bar():\n    return 2\n"
        )
        assert c.is_different_functions()

    def test_same_function(self):
        c = FileConflict(
            file_path="test.py", worker_a="w1", worker_b="w2",
            a_content="def foo():\n    return 1\n",
            b_content="def foo():\n    return 2\n"
        )
        assert not c.is_different_functions()


class TestSharedState:
    async def _test_lock(self):
        ss = SharedState("/tmp")
        ok = await ss.acquire_lock("a.py", "w1")
        assert ok
        # Same worker re-acquiring: OK
        ok = await ss.acquire_lock("a.py", "w1")
        assert ok
        # Different worker: denied
        ok = await ss.acquire_lock("a.py", "w2")
        assert not ok

    def test_lock_acquire_release(self):
        asyncio.run(self._test_lock())

    async def _test_mark_completed(self):
        ss = SharedState("/tmp")
        assert not ss.is_completed("ST-01")
        await ss.mark_completed("ST-01")
        assert ss.is_completed("ST-01")

    def test_mark_completed(self):
        asyncio.run(self._test_mark_completed())


class TestWorkerAgent:
    def test_fallback_execute(self):
        cfg = WorkerConfig(
            worker_id="w1", sub_task_id="ST-01",
            sub_task_description="Test task",
            success_condition="Done"
        )
        ss = SharedState("/tmp")
        worker = WorkerAgent(cfg, ss)
        result = asyncio.run(worker.execute())
        assert result.success
        assert result.worker_id == "w1"


class TestConflictResolver:
    def test_detect_no_conflicts(self):
        resolver = ConflictResolver()
        results = {
            "ST-01": WorkerResult("w1", "ST-01", True, modified_files=["a.py"]),
            "ST-02": WorkerResult("w2", "ST-02", True, modified_files=["b.py"]),
        }
        conflicts = resolver.detect_conflicts(results)
        assert len(conflicts) == 0

    def test_detect_conflicts(self):
        resolver = ConflictResolver()
        results = {
            "ST-01": WorkerResult("w1", "ST-01", True, modified_files=["shared.py"]),
            "ST-02": WorkerResult("w2", "ST-02", True, modified_files=["shared.py"]),
        }
        conflicts = resolver.detect_conflicts(results)
        assert len(conflicts) == 1

    async def _test_resolve_different_funcs(self):
        resolver = ConflictResolver()
        c = FileConflict("test.py", "w1", "w2",
                        "def foo():\n    return 1\n",
                        "def bar():\n    return 2\n")
        result = await resolver.resolve(c)
        assert "foo" in result

    def test_resolve_different_funcs(self):
        asyncio.run(self._test_resolve_different_funcs())


class TestCoordinator:
    def test_execute_plan_sequential(self):
        plan = ExecutionPlan(
            task_id="p1", original_task="test",
            sub_tasks=[
                SubTask("ST-01", SubTaskType.LOCATE, "Search", "Found"),
                SubTask("ST-02", SubTaskType.EDIT, "Fix", "Fixed", dependencies=["ST-01"]),
            ],
            parallel_groups=[["ST-01"], ["ST-02"]]
        )
        ss = SharedState("/tmp")
        coord = Coordinator(ss, max_parallel=2)
        result = asyncio.run(coord.execute_plan(plan))
        assert result.success
        assert "ST-01" in result.worker_results
        assert "ST-02" in result.worker_results
