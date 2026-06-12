"""Planning Phase — task decomposition and scoped execution for DevAgent V2.

Implements the design from docs/improvement/07_Planning_Phase_任务分解设计.md:
  PlannerAgent  — LLM-driven task decomposition into SubTask DAG
  PlanExecutor  — Topological execution of SubTasks with scoped Agentic Loops
  SubTask/Task  — Data models for structured task decomposition
"""

import os
import json
import asyncio
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Models
# ============================================================================

class SubTaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubTaskType(str, Enum):
    LOCATE = "locate"
    UNDERSTAND = "understand"
    EDIT = "edit"
    TEST = "test"
    INVESTIGATE = "investigate"
    VERIFY = "verify"


@dataclass
class SubTask:
    id: str
    type: SubTaskType
    description: str
    success_condition: str
    max_iterations: int = 10
    dependencies: list[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    result_summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    estimated_tools: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    task_id: str
    original_task: str
    sub_tasks: list[SubTask]
    critical_path: list[str] = field(default_factory=list)
    estimated_total_iterations: int = 20
    parallel_groups: list[list[str]] = field(default_factory=list)

    def get_sub_task(self, st_id: str) -> Optional[SubTask]:
        for st in self.sub_tasks:
            if st.id == st_id:
                return st
        return None

    def is_on_critical_path(self, st_id: str) -> bool:
        return st_id in self.critical_path

    def completion_ratio(self) -> float:
        if not self.sub_tasks:
            return 1.0
        done = sum(1 for st in self.sub_tasks
                   if st.status == SubTaskStatus.COMPLETED)
        return done / len(self.sub_tasks)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "original_task": self.original_task[:200],
            "sub_tasks": [
                {"id": st.id, "type": st.type.value, "description": st.description,
                 "status": st.status.value, "dependencies": st.dependencies}
                for st in self.sub_tasks
            ],
            "critical_path": self.critical_path,
            "completion": self.completion_ratio()
        }


@dataclass
class PlanResult:
    plan: ExecutionPlan
    sub_task_results: dict[str, "ScopedLoopResult"] = field(default_factory=dict)
    overall_success: bool = False
    total_iterations_used: int = 0
    duration_sec: float = 0.0


@dataclass
class ScopedLoopResult:
    sub_task_id: str
    success: bool
    iterations: int
    summary: str
    modified_files: list[str] = field(default_factory=list)


# ============================================================================
# Planner Agent
# ============================================================================

PLANNER_SYSTEM_PROMPT = """You are a senior software engineering planner. Decompose a task into atomic, ordered sub-tasks.

## Decomposition Rules
1. Each sub-task must be ATOMIC with one clear, verifiable objective.
2. Each sub-task must have a VERIFIABLE success condition.
3. Sub-tasks must be ORDERED by dependency (no circular deps).
4. Target: 3-8 sub-tasks. Prefer more, smaller sub-tasks.
5. The FIRST sub-task should always be LOCATE — find the relevant code.
6. The LAST sub-task should always be VERIFY — confirm the fix/feature works.
7. Mark which sub-tasks form the CRITICAL PATH (must succeed for task success).

## Sub-Task Types
- locate:    Search for relevant files/code
- understand: Read and comprehend code logic
- edit:      Make focused code changes
- test:      Run tests to verify
- investigate: Research without modifying
- verify:    Final verification and cleanup

## Output Format
Return ONLY valid JSON with this structure:
{
  "sub_tasks": [
    {
      "id": "ST-01",
      "type": "locate",
      "description": "Search for the login validation logic in src/auth.py",
      "success_condition": "Found the validate_login function and understood its flow",
      "dependencies": [],
      "max_iterations": 5,
      "estimated_tools": ["grep_text", "file_read"]
    }
  ],
  "critical_path": ["ST-01", "ST-03", "ST-05"],
  "estimated_total_iterations": 20
}"""


class PlannerAgent:
    """LLM-driven task decomposition into SubTask DAG."""

    def __init__(self, llm_client, workspace: str = "."):
        self.llm = llm_client
        self.workspace = workspace

    def plan(self, task_description: str, repo_map: str = "") -> Optional[ExecutionPlan]:
        """Decompose a task into an execution plan."""
        context = f"## Task\n{task_description}"
        if repo_map:
            context += f"\n\n## Repository Overview\n{repo_map[:2000]}"

        try:
            result = self.llm.chat_structured(
                messages=[{"role": "user", "content": context}],
                system_prompt=PLANNER_SYSTEM_PROMPT
            )
        except Exception as e:
            return None  # Fallback: run without planning

        sub_tasks = []
        for st_data in result.get("sub_tasks", []):
            try:
                st_type = SubTaskType(st_data.get("type", "investigate"))
            except ValueError:
                st_type = SubTaskType.INVESTIGATE

            sub_tasks.append(SubTask(
                id=st_data.get("id", f"ST-{len(sub_tasks) + 1:02d}"),
                type=st_type,
                description=st_data.get("description", ""),
                success_condition=st_data.get("success_condition", ""),
                max_iterations=st_data.get("max_iterations", 10),
                dependencies=st_data.get("dependencies", []),
                estimated_tools=st_data.get("estimated_tools", []),
            ))

        if len(sub_tasks) <= 1:
            return None  # Single sub-task = no planning benefit

        critical_path = result.get("critical_path", [st.id for st in sub_tasks])

        plan = ExecutionPlan(
            task_id=f"plan_{int(time.time())}",
            original_task=task_description,
            sub_tasks=sub_tasks,
            critical_path=critical_path,
            estimated_total_iterations=result.get("estimated_total_iterations", 20),
            parallel_groups=self._compute_parallel_groups(sub_tasks),
        )
        return plan

    @staticmethod
    def _compute_parallel_groups(sub_tasks: list[SubTask]) -> list[list[str]]:
        """Group sub-tasks into parallel-executable batches by topological order."""
        groups = []
        remaining = {st.id for st in sub_tasks}
        completed: set[str] = set()

        while remaining:
            group = []
            for st_id in sorted(remaining):
                st = next((s for s in sub_tasks if s.id == st_id), None)
                if st and all(d in completed for d in st.dependencies):
                    group.append(st_id)
            if not group:
                break
            groups.append(group)
            completed.update(group)
            remaining -= set(group)

        return groups


# ============================================================================
# Plan Executor
# ============================================================================

class PlanExecutor:
    """Executes an ExecutionPlan by running scoped Agentic Loops per SubTask."""

    def __init__(self, core, context_manager, tools, event_bus, streaming):
        self.core = core
        self.context_mgr = context_manager
        self.tools = tools
        self.event_bus = event_bus
        self.streaming = streaming

    async def execute(self, plan: ExecutionPlan,
                      state: "AgentLoopState") -> PlanResult:
        """Execute the plan, one topological layer at a time."""
        start_time = time.time()
        results: dict[str, ScopedLoopResult] = {}
        completed: set[str] = set()
        total_iterations = 0

        # Process groups in topological order
        for group in plan.parallel_groups:
            for st_id in group:
                st = plan.get_sub_task(st_id)
                if not st:
                    continue

                st.status = SubTaskStatus.IN_PROGRESS

                # Build scoped task description
                scoped_task = self._build_scoped_description(st, state)

                # Run a limited Agentic Loop for this sub-task
                loop_result = await self._run_scoped_loop(
                    st, scoped_task, state
                )

                st.status = (SubTaskStatus.COMPLETED if loop_result.success
                            else SubTaskStatus.FAILED)
                st.result_summary = loop_result.summary
                st.artifacts = loop_result.modified_files
                results[st_id] = loop_result
                total_iterations += loop_result.iterations

                if not loop_result.success:
                    if plan.is_on_critical_path(st_id):
                        # Critical path failure — abort plan
                        for remaining in plan.sub_tasks:
                            if remaining.status == SubTaskStatus.PENDING:
                                remaining.status = SubTaskStatus.SKIPPED
                        return PlanResult(
                            plan=plan, sub_task_results=results,
                            overall_success=False,
                            total_iterations_used=total_iterations,
                            duration_sec=time.time() - start_time
                        )

                completed.add(st_id)

        return PlanResult(
            plan=plan, sub_task_results=results,
            overall_success=True,
            total_iterations_used=total_iterations,
            duration_sec=time.time() - start_time
        )

    def _build_scoped_description(self, st: SubTask,
                                   state: "AgentLoopState") -> str:
        """Build a focused task description scoped to this sub-task."""
        lines = [
            f"## Sub-Task: {st.id} — {st.type.value.upper()}",
            f"Objective: {st.description}",
            f"Success Condition: {st.success_condition}",
            f"Max Iterations: {st.max_iterations}",
        ]
        if st.estimated_tools:
            lines.append(f"Suggested Tools: {', '.join(st.estimated_tools)}")
        if st.dependencies:
            lines.append(f"Dependencies: {', '.join(st.dependencies)}")

        # Add workspace context
        modified = state.modified_files if hasattr(state, 'modified_files') else []
        if modified:
            lines.append(f"Previously Modified Files: {', '.join(modified)}")

        return "\n".join(lines)

    async def _run_scoped_loop(self, st: SubTask, scoped_task: str,
                                state: "AgentLoopState") -> ScopedLoopResult:
        """Run a scoped agentic loop for a single sub-task."""
        # Create a temporary state copy for this sub-task
        import copy
        sub_state = copy.deepcopy(state)
        sub_state.max_iterations = st.max_iterations
        sub_state.current_iteration = 0

        # Execute
        try:
            # Use the core's standard loop with reduced iterations
            loop_state = await self.core._run_standard_loop(
                scoped_task, sub_state
            )
            return ScopedLoopResult(
                sub_task_id=st.id,
                success=loop_state.status == "COMPLETED",
                iterations=loop_state.current_iteration,
                summary=f"Status: {loop_state.status}, "
                       f"Iterations: {loop_state.current_iteration}",
                modified_files=list(loop_state.modified_files)
            )
        except Exception as e:
            return ScopedLoopResult(
                sub_task_id=st.id,
                success=False,
                iterations=0,
                summary=f"Exception: {str(e)[:200]}"
            )
