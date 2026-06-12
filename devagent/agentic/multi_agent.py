"""Multi-Agent Collaboration — Coordinator + Worker pattern for DevAgent V2.

Implements design doc 11: parallel Worker execution with SharedState,
file-level locking, topological group scheduling, and conflict resolution.

Architecture:
  Coordinator  → schedules Workers by topological groups
  WorkerAgent  → scoped Agentic Loop within file allowlist
  SharedState  → file locks + merge requests + global test results
  ConflictResolver → auto-merge | LLM-mediate | first-wins
"""

import os
import json
import asyncio
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class WorkerConfig:
    worker_id: str
    sub_task_id: str
    sub_task_description: str
    success_condition: str
    tool_allowlist: list[str] = field(default_factory=list)
    file_allowlist: list[str] = field(default_factory=list)
    max_iterations: int = 10
    timeout_seconds: int = 300


@dataclass
class WorkerResult:
    worker_id: str
    sub_task_id: str
    success: bool
    iterations: int = 0
    summary: str = ""
    modified_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0


@dataclass
class FileConflict:
    file_path: str
    worker_a: str
    worker_b: str
    a_content: str
    b_content: str

    def is_different_functions(self) -> bool:
        """Check if two workers edited different functions in the same file."""
        import ast
        try:
            a_tree = ast.parse(self.a_content)
            b_tree = ast.parse(self.b_content)
        except SyntaxError:
            return False

        a_funcs = {n.name for n in ast.walk(a_tree) if isinstance(n, ast.FunctionDef)}
        b_funcs = {n.name for n in ast.walk(b_tree) if isinstance(n, ast.FunctionDef)}
        return bool(a_funcs ^ b_funcs)  # Different sets of functions modified


@dataclass
class MultiAgentResult:
    success: bool
    worker_results: dict[str, WorkerResult] = field(default_factory=dict)
    conflicts: list[FileConflict] = field(default_factory=list)
    total_duration_sec: float = 0.0
    total_iterations: int = 0


# ============================================================================
# Shared State
# ============================================================================

class SharedState:
    """Thread-safe shared workspace for multi-agent coordination."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.file_locks: dict[str, str] = {}        # file_path → worker_id
        self.merge_requests: list[dict] = []
        self.global_test_results: dict = {}
        self._completed: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire_lock(self, file_path: str, worker_id: str) -> bool:
        async with self._lock:
            if file_path in self.file_locks:
                return self.file_locks[file_path] == worker_id
            self.file_locks[file_path] = worker_id
            return True

    async def release_lock(self, file_path: str, worker_id: str):
        async with self._lock:
            if self.file_locks.get(file_path) == worker_id:
                del self.file_locks[file_path]

    async def release_all(self, worker_id: str):
        async with self._lock:
            to_remove = [f for f, w in self.file_locks.items() if w == worker_id]
            for f in to_remove:
                del self.file_locks[f]

    async def mark_completed(self, sub_task_id: str):
        async with self._lock:
            self._completed.add(sub_task_id)

    def is_completed(self, sub_task_id: str) -> bool:
        return sub_task_id in self._completed

    def all_completed(self, sub_task_ids: list[str]) -> bool:
        return all(sid in self._completed for sid in sub_task_ids)


# ============================================================================
# Worker Agent
# ============================================================================

class WorkerAgent:
    """Scoped Agentic Loop instance for a single sub-task."""

    def __init__(self, config: WorkerConfig, shared: SharedState,
                 core=None, llm_client=None):
        self.config = config
        self.shared = shared
        self.core = core        # DevAgentCore instance
        self.llm = llm_client

    async def execute(self) -> WorkerResult:
        start = time.time()

        # Acquire locks for target files
        for f in self.config.file_allowlist:
            locked = await self.shared.acquire_lock(f, self.config.worker_id)
            if not locked:
                return WorkerResult(
                    worker_id=self.config.worker_id,
                    sub_task_id=self.config.sub_task_id,
                    success=False,
                    summary=f"Could not acquire lock on {f}"
                )

        try:
            if self.core:
                from .state import AgentLoopState
                state = AgentLoopState(
                    workspace=self.shared.workspace,
                    task_description=self.config.sub_task_description,
                    max_iterations=self.config.max_iterations
                )
                # Run scoped loop
                loop_state = await self.core._run_standard_loop(
                    self.config.sub_task_description, state
                )
                return WorkerResult(
                    worker_id=self.config.worker_id,
                    sub_task_id=self.config.sub_task_id,
                    success=loop_state.status == "COMPLETED",
                    iterations=loop_state.current_iteration,
                    summary=f"Status: {loop_state.status}, "
                           f"Iterations: {loop_state.current_iteration}",
                    modified_files=list(loop_state.modified_files),
                    duration_sec=time.time() - start
                )
            else:
                # No core available — simulate with direct LLM calls
                return await self._fallback_execute()
        except Exception as e:
            return WorkerResult(
                worker_id=self.config.worker_id,
                sub_task_id=self.config.sub_task_id,
                success=False,
                errors=[str(e)],
                duration_sec=time.time() - start
            )
        finally:
            await self.shared.release_all(self.config.worker_id)
            await self.shared.mark_completed(self.config.sub_task_id)

    async def _fallback_execute(self) -> WorkerResult:
        """Fallback when no DevAgentCore is available (e.g., in tests)."""
        await asyncio.sleep(0.1)
        return WorkerResult(
            worker_id=self.config.worker_id,
            sub_task_id=self.config.sub_task_id,
            success=True,
            iterations=1,
            summary="Fallback execution completed",
            duration_sec=0.1
        )


# ============================================================================
# Coordinator
# ============================================================================

class Coordinator:
    """Schedules and coordinates parallel Worker execution by topological groups."""

    def __init__(self, shared: SharedState,
                 core=None, llm_client=None,
                 max_parallel: int = 4):
        self.shared = shared
        self.core = core
        self.llm = llm_client
        self.max_parallel = max_parallel
        self.conflict_resolver = ConflictResolver()

    async def execute_plan(self, plan: "ExecutionPlan") -> MultiAgentResult:
        """Execute an ExecutionPlan using parallel workers by group."""
        start_time = time.time()
        results: dict[str, WorkerResult] = {}
        conflicts: list[FileConflict] = []
        total_iterations = 0

        groups = plan.parallel_groups if plan.parallel_groups else [
            [st.id for st in plan.sub_tasks]
        ]

        for group in groups:
            # Build workers for this group
            workers = []
            for st_id in group:
                st = plan.get_sub_task(st_id)
                if not st:
                    continue

                config = WorkerConfig(
                    worker_id=f"worker-{st_id}",
                    sub_task_id=st_id,
                    sub_task_description=f"{st.type.value}: {st.description}",
                    success_condition=st.success_condition,
                    max_iterations=st.max_iterations,
                    tool_allowlist=st.estimated_tools,
                    file_allowlist=[],
                )
                worker = WorkerAgent(config, self.shared, self.core, self.llm)
                workers.append(worker)

            # Run workers in parallel (limited by max_parallel)
            semaphore = asyncio.Semaphore(self.max_parallel)

            async def run_with_limit(w):
                async with semaphore:
                    return await w.execute()

            group_tasks = [asyncio.create_task(run_with_limit(w)) for w in workers]
            group_results = await asyncio.gather(*group_tasks, return_exceptions=True)

            for i, result in enumerate(group_results):
                if isinstance(result, Exception):
                    result = WorkerResult(
                        worker_id=workers[i].config.worker_id,
                        sub_task_id=workers[i].config.sub_task_id,
                        success=False,
                        errors=[str(result)]
                    )
                results[result.sub_task_id] = result
                total_iterations += result.iterations

                if not result.success:
                    st = plan.get_sub_task(result.sub_task_id)
                    if st and plan.is_on_critical_path(result.sub_task_id):
                        return MultiAgentResult(
                            success=False,
                            worker_results=results,
                            conflicts=conflicts,
                            total_duration_sec=time.time() - start_time,
                            total_iterations=total_iterations
                        )

        # Resolve any file conflicts between workers
        conflicts = self.conflict_resolver.detect_conflicts(results)
        if conflicts:
            for c in conflicts:
                resolved = await self.conflict_resolver.resolve(c)
                # Apply resolution (write resolved content to file)
                Path(self.shared.workspace, c.file_path).write_text(resolved)

        return MultiAgentResult(
            success=True,
            worker_results=results,
            conflicts=conflicts,
            total_duration_sec=time.time() - start_time,
            total_iterations=total_iterations
        )


# ============================================================================
# Conflict Resolver
# ============================================================================

class ConflictResolver:
    """Detects and resolves file conflicts between parallel workers."""

    def detect_conflicts(self,
                         results: dict[str, WorkerResult]) -> list[FileConflict]:
        """Find files modified by multiple workers."""
        file_workers: dict[str, list[str]] = {}
        for wr in results.values():
            for f in wr.modified_files:
                if f not in file_workers:
                    file_workers[f] = []
                file_workers[f].append(wr.worker_id)

        conflicts = []
        for file_path, workers in file_workers.items():
            if len(workers) > 1:
                conflicts.append(FileConflict(
                    file_path=file_path,
                    worker_a=workers[0],
                    worker_b=workers[1],
                    a_content="",
                    b_content=""
                ))
        return conflicts

    async def resolve(self, conflict: FileConflict) -> str:
        """Resolve a conflict using the best available strategy."""
        # Strategy 1: Different functions → auto-merge
        if conflict.is_different_functions():
            return self._auto_merge(conflict)

        # Strategy 2: Same function → LLM-mediate
        if self.llm:
            return await self._llm_mediate(conflict)

        # Strategy 3: First-wins (keep content from first worker)
        return conflict.a_content

    @staticmethod
    def _auto_merge(conflict: FileConflict) -> str:
        """Simple merge: concatenate distinct function bodies."""
        import ast
        try:
            a_tree = ast.parse(conflict.a_content)
            b_tree = ast.parse(conflict.b_content)
        except SyntaxError:
            return conflict.a_content

        # Extract function definitions from both
        a_funcs = {n for n in ast.walk(a_tree) if isinstance(n, ast.FunctionDef)}
        b_funcs = {n for n in ast.walk(b_tree) if isinstance(n, ast.FunctionDef)}
        a_names = {f.name for f in a_funcs}
        b_names = {f.name for f in b_funcs}

        # If no overlap, combine both
        if not (a_names & b_names):
            a_lines = conflict.a_content.split("\n")
            b_lines = conflict.b_content.split("\n")
            return "\n".join(a_lines + [""] + b_lines)

        return conflict.a_content  # Fallback: keep A

    async def _llm_mediate(self, conflict: FileConflict) -> str:
        """Use LLM to merge conflicting edits."""
        if not self.llm:
            return conflict.a_content

        prompt = f"""Two developers edited the same file. Merge their changes.

=== Version A ({conflict.worker_a}) ===
```python
{conflict.a_content[:2000]}
```

=== Version B ({conflict.worker_b}) ===
```python
{conflict.b_content[:2000]}
```

Output ONLY the merged file content."""

        try:
            return self.llm.chat(messages=[{"role": "user", "content": prompt}])
        except Exception:
            return conflict.a_content  # Fallback
