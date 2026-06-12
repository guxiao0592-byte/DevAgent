# DevAgent V2 — Planning Phase 任务分解设计方案

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | Planning Phase — 任务分解详细设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-21 |
| 优先级 | P0 |
| 前置依赖 | [01_架构升级_Agentic_Loop设计方案](./01_架构升级_Agentic_Loop设计方案.md) |

## 1. 问题陈述

当前 Agentic 模式的 DevAgentCore 从 ReAct 循环第一步就直接进入探索阶段，没有顶层任务分解：

- Agent 容易在子目标之间迷失方向
- 不知道该何时从一个子任务切换到另一个
- 无法评估整体进度
- 对大任务缺少全局视野

## 2. 业界参考

| 系统 | 规划方式 |
|------|---------|
| OpenHands | `PlannerAgent` 将任务分解为 `SubTask` 列表，Main Agent 委派给 Worker |
| Devin | 内部的有向无环图 (DAG) 任务分解，支持并行子任务 |
| SWE-agent | 无显式规划，依赖 ReAct Loop 的自然探索 |
| Claude Code | 无显式规划，依赖 `TodoWrite` 等内部工具追踪 |

## 3. 设计方案

### 3.1 核心架构：Planning → Execute → Verify

```
Task Input
    │
    ▼
┌─────────────┐
│ PlannerAgent │  ← LLM 调用：分解为 SubTask 列表
│ (1次LLM调用) │    输出: DAG 结构
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│           Plan Executor                      │
│                                              │
│  for each sub_task in dependency_order:      │
│    ┌──────────────────────┐                  │
│    │ Agentic Loop (限制范围)│                  │
│    │  - 上下文仅含当前子任务 │                 │
│    │  - 最大迭代 = 子任务预算 │                │
│    │  - 成功条件 = 子任务验证 │                │
│    └──────┬───────────────┘                  │
│           │                                   │
│           ▼                                   │
│    Verify sub_task                            │
│    if failed → retry or escalate              │
└─────────────────────────────────────────────┘
       │
       ▼
  Final Report
```

### 3.2 SubTask 数据模型

```python
from dataclasses import dataclass, field
from enum import Enum

class SubTaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class SubTaskType(Enum):
    LOCATE = "locate"        # 定位相关代码
    UNDERSTAND = "understand" # 理解代码逻辑
    EDIT = "edit"            # 修改代码
    TEST = "test"            # 运行测试验证
    INVESTIGATE = "investigate" # 调查问题

@dataclass
class SubTask:
    id: str                          # "ST-01"
    type: SubTaskType
    description: str                 # 人类可读描述
    success_condition: str           # 可验证的成功条件
    max_iterations: int = 10         # 此子任务的最大迭代数
    dependencies: list[str] = field(default_factory=list)  # 依赖的其他 SubTask ID
    status: SubTaskStatus = SubTaskStatus.PENDING
    result_summary: str = ""
    artifacts: list[str] = field(default_factory=list)  # 产生的文件

@dataclass
class ExecutionPlan:
    task_id: str
    original_task: str
    sub_tasks: list[SubTask]
    critical_path: list[str]         # 关键路径上的 SubTask ID 列表
    estimated_total_iterations: int
    parallel_groups: list[list[str]] # 可并行的 SubTask 分组
```

### 3.3 PlannerAgent 设计

```python
PLANNER_PROMPT = """You are a senior software engineering planner. Decompose the given task into a sequence of atomic sub-tasks.

## Task
{task_description}

## Workspace Context
{repo_map_summary}

## Decomposition Rules
1. Each sub-task must be ATOMIC — one clear objective that can be verified.
2. Each sub-task must have a VERIFIABLE success condition.
3. Sub-tasks must be ORDERED by dependency.
4. Prefer many small sub-tasks over a few large ones (target: 3-8 sub-tasks).
5. The first sub-task should ALWAYS be locating the relevant code.

## Sub-Task Types
- locate: Find files/code relevant to the task using search tools
- understand: Read and understand specific code logic
- edit: Make a focused code change
- test: Run tests to verify correctness
- investigate: Research an issue without modifying code

## Output JSON
{
  "sub_tasks": [
    {
      "id": "ST-01",
      "type": "locate",
      "description": "Search for the buggy function in src/auth.py related to login validation",
      "success_condition": "Located the specific function and understood its expected behavior",
      "dependencies": [],
      "max_iterations": 5,
      "estimated_tools": ["grep_text", "file_read"]
    }
  ],
  "critical_path": ["ST-01", "ST-02", "ST-04"],
  "estimated_total_iterations": 25
}"""
```

### 3.4 PlanExecutor 设计

```python
class PlanExecutor:
    """Executes an ExecutionPlan by running an Agentic Loop for each SubTask."""

    def __init__(self, core: DevAgentCore):
        self.core = core

    async def execute(self, plan: ExecutionPlan, state: AgentLoopState) -> PlanResult:
        results = {}
        completed_count = 0

        # Topological sort by dependency
        ready = [st for st in plan.sub_tasks if not st.dependencies]
        completed: set[str] = set()

        while ready:
            sub_task = ready.pop(0)

            # Scope the agentic loop to this sub-task
            scoped_task = self._build_scoped_description(sub_task, state)
            sub_state = await self.core.execute_scoped(
                scoped_task,
                max_iterations=sub_task.max_iterations,
                success_check=lambda s: self._verify(sub_task, s)
            )

            sub_task.status = SubTaskStatus.COMPLETED if sub_state.status == "COMPLETED" \
                            else SubTaskStatus.FAILED
            results[sub_task.id] = sub_task
            completed.add(sub_task.id)

            if sub_task.status == SubTaskStatus.FAILED:
                if self._is_critical(sub_task, plan.critical_path):
                    break  # Critical path failure → stop
                else:
                    continue  # Non-critical → skip and continue

            # Unblock dependent sub-tasks
            for st in plan.sub_tasks:
                if st.status == SubTaskStatus.PENDING:
                    if all(d in completed for d in st.dependencies):
                        ready.append(st)

        return PlanResult(sub_tasks=plan.sub_tasks, results=results)
```

### 3.5 与现有 Agentic Loop 的集成

在 `DevAgentCore.execute_async()` 的入口处增加规划阶段：

```python
async def execute_async(self, task_description, workspace, ...):
    # 如果启用 Planning Phase
    if self.agentic_config.get("enable_planning", True):
        planner = TaskPlanner(self.llm, workspace)
        plan = await planner.plan(task_description, workspace)

        if plan and len(plan.sub_tasks) > 1:
            executor = PlanExecutor(self)
            result = await executor.execute(plan, state)
            return result.final_state

    # Fallback: 原始的无规划 Agentic Loop
    return await self._run_standard_loop(task_description, state)
```

### 3.6 配置

```yaml
workflow:
  agentic:
    enable_planning: true
    planning:
      max_sub_tasks: 8
      min_sub_tasks: 2
      default_iterations_per_sub_task: 10
      critical_path_retry: 1
```

## 4. 评估指标

| 指标 | 当前(无规划) | 目标 |
|------|------------|------|
| 复杂任务完成率 | ~40% | > 70% |
| 平均迭代数/任务 | 15 | 12 (更聚焦) |
| 子任务完成率 | N/A | > 80% |
| Planning 准确率 | N/A | > 75% |
