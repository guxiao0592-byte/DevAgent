# DevAgent V2 — 多 Agent 协作并行 Worker 设计

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | 多 Agent 协作 — 并行 Worker 详细设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-21 |
| 优先级 | P2 |
| 前置依赖 | [01_架构升级_Agentic_Loop设计方案](./01_架构升级_Agentic_Loop设计方案.md)、[07_Planning_Phase_任务分解设计](./07_Planning_Phase_任务分解设计.md) |

## 1. 问题陈述

当前 DevAgent 只有一个 Agent 串行执行，无法利用并行性。实际软件开发中，多个开发者可以并行处理独立的模块。

## 2. 业界参考

| 系统 | 多 Agent 模式 |
|------|-------------|
| OpenHands | `AgentDelegateAction` — 主 Agent 委派子任务给 Worker Agent |
| ChatGPT Codex | Multi-agent collaboration with shared workspace |
| MetaGPT | 多角色 Agent（PM/Architect/Engineer/QA）协作 |
| ChatDev | 多 Agent 通过结构化对话协作（瀑布模型式的角色扮演） |

## 3. 设计方案

### 3.1 核心架构：Coordinator + Worker 模式

```
                    ┌──────────────┐
                    │  Task Input  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Planner     │ ← 分解任务为 DAG
                    │  (Coordinator)│
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         ┌────▼───┐   ┌───▼────┐  ┌────▼───┐
         │Worker 1│   │Worker 2│  │Worker 3│
         │        │   │        │  │        │
         │Agentic │   │Agentic │  │Agentic │
         │ Loop   │   │ Loop   │  │ Loop   │
         └────┬───┘   └───┬────┘  └────┬───┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────▼───────┐
                    │  Merger      │ ← 合并 Worker 结果
                    │  (Coordinator)│
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Reviewer    │ ← 全局审查 + 集成测试
                    └──────────────┘
```

### 3.2 Worker Agent

```python
@dataclass
class WorkerConfig:
    worker_id: str
    sub_task: SubTask
    tool_allowlist: list[str]  # 可用的工具列表
    file_allowlist: list[str]  # 可编辑的文件列表
    max_iterations: int = 10
    timeout_seconds: int = 300

class WorkerAgent:
    """独立的 Agentic Loop 实例，沙箱化在子任务范围内"""

    def __init__(self, config: WorkerConfig, shared_state: SharedState):
        self.config = config
        self.shared = shared_state  # 只读共享状态
        self.core = ScopedDevAgentCore(  # 受限的 Agent Core
            tools=config.tool_allowlist,
            files=config.file_allowlist,
        )

    async def execute(self) -> WorkerResult:
        """在受限范围内执行子任务"""
        scoped_task = f"Sub-task {self.config.sub_task.id}: {self.config.sub_task.description}"
        return await self.core.execute_async(
            scoped_task,
            max_iterations=self.config.max_iterations
        )
```

### 3.3 共享状态管理

```python
class SharedState:
    """多 Agent 共享的工作空间状态

    设计原则：
    - Coordinator 可以读写
    - Worker 只能读写自己分配的文件
    - Worker 可以通过 MergeRequest 请求修改其他文件
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.file_locks: dict[str, str] = {}  # file_path → worker_id
        self.merge_requests: list[MergeRequest] = []
        self.global_test_results: dict = {}

    def acquire_lock(self, file_path: str, worker_id: str) -> bool:
        """Worker 获取文件的编辑锁"""
        if file_path in self.file_locks:
            return self.file_locks[file_path] == worker_id
        self.file_locks[file_path] = worker_id
        return True

    def release_lock(self, file_path: str, worker_id: str):
        if self.file_locks.get(file_path) == worker_id:
            del self.file_locks[file_path]
```

### 3.4 Coordinator

```python
class Coordinator:
    """协调多个 Worker 的并行执行"""

    def __init__(self, plan: ExecutionPlan, workspace: str):
        self.plan = plan
        self.shared = SharedState(workspace)
        self.workers: dict[str, WorkerAgent] = {}
        self.results: dict[str, WorkerResult] = {}

    async def execute(self) -> MultiAgentResult:
        """执行 Plan，并行化独立的子任务"""
        # 1. 组织并发组
        groups = self._topological_groups(self.plan)

        for group in groups:
            # 2. 并行启动组内所有 Worker
            tasks = []
            for sub_task_id in group:
                worker = self._create_worker(sub_task_id)
                tasks.append(asyncio.create_task(worker.execute()))

            # 3. 等待组内所有 Worker 完成
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 4. 检查失败
            for sub_task_id, result in zip(group, results):
                if isinstance(result, Exception) or not result.success:
                    if self.plan.is_on_critical_path(sub_task_id):
                        return MultiAgentResult.failed(sub_task_id, result)

            # 5. 释放锁，下一组可用
            self._release_group_locks(group)

        # 6. 全局集成验证
        return await self._run_integration_verification()

    def _topological_groups(self, plan: ExecutionPlan) -> list[list[str]]:
        """将 DAG 分解为拓扑排序的并行组"""
        groups = []
        remaining = {st.id for st in plan.sub_tasks}
        completed = set()

        while remaining:
            group = []
            for st_id in sorted(remaining):
                st = plan.get_sub_task(st_id)
                if all(d in completed for d in st.dependencies):
                    group.append(st_id)
            groups.append(group)
            completed.update(group)
            remaining -= set(group)

        return groups
```

### 3.5 冲突解决

```python
class ConflictResolver:
    """处理 Worker 之间的文件修改冲突"""

    @staticmethod
    async def resolve(conflict: FileConflict) -> str:
        """冲突解决策略：
        1. 如果两个 Worker 修改了同一文件的不同函数 → 自动合并
        2. 如果修改了同一函数 → Coordinator LLM 裁决
        3. 如果修改了同一行 → 放弃一个，记录原因
        """
        if conflict.is_different_functions():
            return ConflictResolver._auto_merge(conflict)
        elif conflict.is_same_function():
            return await ConflictResolver._llm_mediate(conflict)
        else:
            # 同一行 → 保留先完成的，记录
            return conflict.earlier_version()
```

### 3.6 配置

```yaml
multi_agent:
  enabled: true
  max_workers: 4
  worker:
    max_iterations_per_sub_task: 10
    timeout_seconds: 300
  coordinator:
    merge_strategy: "auto"  # auto | llm_mediate | manual
    integration_test: true
  file_locking:
    enabled: true
    granularity: "function"  # file | function | line
```

## 4. 评估指标

| 指标 | 当前(单Agent) | 目标 |
|------|------------|------|
| 大任务执行时间 | 15 min | < 5 min（4 Workers） |
| 并行利用率 | 0% | > 60% |
| 冲突发生率 | N/A | < 15% |
| 集成成功率 | ~40% | > 70% |
