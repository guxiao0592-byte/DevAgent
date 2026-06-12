"""Tests for Planning Phase — task decomposition and scoped execution."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.planning import (
    SubTask, SubTaskType, SubTaskStatus,
    ExecutionPlan, PlanResult, PlannerAgent
)


class TestSubTask:
    def test_defaults(self):
        st = SubTask(id="ST-01", type=SubTaskType.LOCATE,
                     description="Find the code",
                     success_condition="Found it")
        assert st.status == SubTaskStatus.PENDING
        assert st.max_iterations == 10
        assert st.dependencies == []

    def test_dependencies(self):
        st = SubTask(id="ST-02", type=SubTaskType.EDIT,
                     description="Fix bug",
                     success_condition="Bug fixed",
                     dependencies=["ST-01"])
        assert "ST-01" in st.dependencies
        assert st.type == SubTaskType.EDIT


class TestExecutionPlan:
    def test_get_sub_task(self):
        plan = ExecutionPlan(
            task_id="plan_1", original_task="Fix bug",
            sub_tasks=[
                SubTask("ST-01", SubTaskType.LOCATE, "Search", "Found"),
            ]
        )
        st = plan.get_sub_task("ST-01")
        assert st is not None
        assert plan.get_sub_task("ST-99") is None

    def test_completion_ratio(self):
        st1 = SubTask("ST-01", SubTaskType.LOCATE, "a", "a")
        st2 = SubTask("ST-02", SubTaskType.EDIT, "b", "b")
        st1.status = SubTaskStatus.COMPLETED
        plan = ExecutionPlan("p1", "task", [st1, st2])
        assert plan.completion_ratio() == 0.5

    def test_to_dict(self):
        plan = ExecutionPlan("p1", "Test task",
                            [SubTask("ST-01", SubTaskType.LOCATE, "a", "a")],
                            critical_path=["ST-01"])
        d = plan.to_dict()
        assert d["task_id"] == "p1"
        assert len(d["sub_tasks"]) == 1

    def test_parallel_groups(self):
        st1 = SubTask("ST-01", SubTaskType.LOCATE, "a", "a")
        st2 = SubTask("ST-02", SubTaskType.EDIT, "b", "b", dependencies=["ST-01"])
        st3 = SubTask("ST-03", SubTaskType.TEST, "c", "c", dependencies=["ST-01"])
        plan = ExecutionPlan("p1", "task", [st1, st2, st3])

        groups = PlannerAgent._compute_parallel_groups([st1, st2, st3])
        assert len(groups) == 2  # ST-01 alone, then ST-02 + ST-03 in parallel
        assert groups[0] == ["ST-01"]
        assert set(groups[1]) == {"ST-02", "ST-03"}


class TestPlannerAgent:
    def test_parallel_groups_linear(self):
        st1 = SubTask("ST-01", SubTaskType.LOCATE, "a", "a")
        st2 = SubTask("ST-02", SubTaskType.EDIT, "b", "b", dependencies=["ST-01"])
        st3 = SubTask("ST-03", SubTaskType.TEST, "c", "c", dependencies=["ST-02"])
        groups = PlannerAgent._compute_parallel_groups([st1, st2, st3])
        assert len(groups) == 3  # Sequential

    def test_parallel_groups_independent(self):
        st1 = SubTask("ST-01", SubTaskType.LOCATE, "a", "a")
        st2 = SubTask("ST-02", SubTaskType.LOCATE, "b", "b")
        st3 = SubTask("ST-03", SubTaskType.TEST, "c", "c")
        groups = PlannerAgent._compute_parallel_groups([st1, st2, st3])
        assert len(groups) == 1  # All independent, single group
        assert set(groups[0]) == {"ST-01", "ST-02", "ST-03"}
