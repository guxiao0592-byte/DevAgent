# DevAgent V2 架构升级：Agentic Loop 设计方案

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | DevAgent V2 架构升级：Agentic Loop 设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-20 |
| 文档状态 | 初稿 |
| 参考系统 | SWE-agent、OpenHands、Claude Code、CodeR、Devin |

## 1. 概述

### 1.1 文档目的

本文档详细描述将 DevAgent 从**线性流水线架构**升级到**智能体自主循环架构（Agentic Loop）**的设计方案。

### 1.2 当前架构问题

```
当前架构（线性流水线）：
  Planner → RequirementAgent → DesignAgent → CodeAgent → TestAgent
              → [DebugAgent → RepairAgent]* → ReviewAgent → 输出

核心缺陷：
  1. 阶段顺序硬编码，Agent 无法自主决策
  2. 每阶段仅一次 LLM 调用，无观察-行动-反思循环
  3. 执行反馈未能驱动后续决策
  4. 遇到未预见场景时缺乏自适应能力
```

### 1.3 目标架构

```
目标架构（Agentic Loop）：
  ┌─────────────────────────────────────────────────────┐
  │                   DevAgent Core                     │
  │  ┌─────────┐   ┌──────────┐   ┌─────────────────┐  │
  │  │Planning │   │ReAct Loop│   │   Tool System   │  │
  │  │ Phase   │──▶│          │──▶│  (Shell/Edit/    │  │
  │  └─────────┘   │ Observe  │   │   Grep/Git/...)  │  │
  │                │ →Think   │   └─────────────────┘  │
  │                │ →Act     │                         │
  │                └──────────┘                         │
  └─────────────────────────────────────────────────────┘
```

## 2. 问题分析

### 2.1 流水线架构的根本局限

| 局限 | 表现 | 影响 |
|------|------|------|
| 缺乏反馈闭环 | 生成代码后不运行验证 | 语法错误、导入错误留到 TestAgent 才发现 |
| 无自适应能力 | 修复失败后仅简单重试 | 相同错误反复出现 |
| 上下文断裂 | 各 Agent 独立运行，不共享中间发现 | DebugAgent 需重新理解代码 |
| 无法应对复杂任务 | 多文件修改需精确协调 | 修改遗漏依赖更新 |

### 2.2 业界方案对比

| 系统 | 架构模式 | 核心循环 | 工具数量 | 关键特征 |
|------|---------|---------|---------|---------|
| SWE-agent | ReAct Loop | 观察→思考→行动 | 6+ | ACI 接口，终端工具 |
| OpenHands | Event-driven | Agent Delegate | 10+ | 子代理委派，事件溯源 |
| Claude Code | Tool-calling Loop | 思考→工具调用 | 15+ | 丰富工具，子代理 |
| CodeR | MCTS + Tool Use | 搜索树+工具 | 8+ | 蒙特卡洛树搜索 |
| DevAgent(当前) | 线性流水线 | 无自主循环 | 3 | 单次LLM调用/阶段 |

### 2.3 核心差距

1. **无自主决策能力**：当前系统的工作流在 `workflow.py` 中硬编码，而非由 Agent 根据任务状态动态决定
2. **无执行反馈**：Agent 无法"尝试执行→观察错误→修正"这样的基本开发行为
3. **无工具组合能力**：无法组合使用多个工具完成复杂操作（如 `grep 定位 → read 阅读 → edit 修改 → test 验证`）

## 3. 详细设计方案

### 3.1 ReAct Agent Core

#### 3.1.1 核心数据结构

```python
class AgentAction:
    """Agent 决策产生的动作"""
    thought: str           # 思维链：为什么选择这个动作
    tool_name: str         # 工具名：shell / grep / read / edit / ...
    tool_params: dict      # 工具参数
    expected_outcome: str  # 预期结果

class Observation:
    """动作执行后的观察"""
    action: AgentAction
    result: ToolResult     # 工具返回
    delta: dict            # 状态变化
    timestamp: datetime

class AgentLoopState:
    """Agentic Loop 的核心状态"""
    task_description: str
    workspace_path: str
    git_initial_commit: str
    action_history: list[AgentAction]
    observation_history: list[Observation]
    current_iteration: int
    max_iterations: int
    sub_tasks: list[dict]   # Planner 分解的子任务
```

#### 3.1.2 ReAct 循环核心算法

```python
class DevAgentCore:
    def execute(self, task: TaskSpec) -> LoopResult:
        state = self._init_state(task)
        context = self._build_context(state)

        while state.current_iteration < state.max_iterations:
            # Step 1: LLM 决策
            action = self.llm_decide(context, self.tools)

            # Step 2: 执行工具
            observation = self.execute_tool(action)

            # Step 3: 更新上下文
            context.add(action, observation)

            # Step 4: 检查终止条件
            if self._should_stop(state, observation):
                break

            # Step 5: 反思与压缩（避免上下文溢出）
            if self._context_near_limit(context):
                context = self._compress(context)

            state.current_iteration += 1

        return self._build_result(state)
```

#### 3.1.3 系统 Prompt 设计

```python
SYSTEM_PROMPT = """You are DevAgent, an autonomous software engineering agent.
You work in a ReAct loop: observe the environment, think about what to do next,
execute a tool, and observe the result.

## Available Tools
{tool_descriptions}

## Task
{task_description}

## Working Environment
- Workspace: {workspace}
- OS: {os_info}
- Language: {language}

## Rules
1. ALWAYS read a file before editing it
2. After editing, ALWAYS run relevant tests
3. Use grep/search to locate relevant code before editing
4. Fix one issue at a time, then verify
5. If stuck after 3 attempts on the same issue, explain why and try an alternative approach
6. When the task is complete, call the "submit" tool with a summary

## Output Format
For each step, respond with:
THOUGHT: <your reasoning about the current state and what to do next>
ACTION: <tool_name>
PARAMS: <json params>
```
```

### 3.2 任务规划阶段

在执行 ReAct Loop 之前，增加一个 Planning 阶段进行任务分解：

```python
class TaskPlanner:
    PROMPT = """Decompose the given software task into a sequence of sub-tasks.

Each sub-task must be:
1. Atomic: one clear objective
2. Verifiable: has a clear success condition
3. Ordered: dependencies correctly sequenced

Output JSON:
{
  "sub_tasks": [
    {
      "id": "ST-01",
      "description": "Locate the buggy function in src/auth.py",
      "success_condition": "Found the function and understood its logic",
      "estimated_tool_calls": ["grep", "read"],
      "dependencies": []
    },
    ...
  ],
  "estimated_total_steps": 15,
  "critical_path": ["ST-01", "ST-02", ...]
}
"""
```

### 3.3 终止条件设计

```python
class TerminationChecker:
    def should_stop(self, state: AgentLoopState, last_obs: Observation) -> tuple[bool, str]:
        # 条件 1: Agent 主动提交
        if last_obs.action.tool_name == "submit":
            return True, "task_submitted"

        # 条件 2: 所有测试通过且无残留 TODO
        if self._all_tests_pass(state) and self._no_remaining_work(state):
            return True, "all_tests_pass"

        # 条件 3: 达到最大迭代数
        if state.current_iteration >= state.max_iterations:
            return True, "max_iterations_reached"

        # 条件 4: 连续空转检测（最近 N 步无进展）
        if self._is_stuck(state, window=5):
            return True, "no_progress"

        # 条件 5: Agent 声明无法完成
        if "cannot complete" in last_obs.result.output.lower():
            return True, "agent_declared_failure"

        return False, ""
```

### 3.4 上下文管理策略

```python
class ContextManager:
    """管理 Agent 的上下文窗口，避免溢出"""

    def build_context(self, state: AgentLoopState) -> list[dict]:
        messages = []

        # 1. 系统 Prompt（固定）
        messages.append({"role": "system", "content": self.system_prompt})

        # 2. 任务描述（固定）
        messages.append({"role": "user", "content": state.task_description})

        # 3. 最近 K 轮的完整历史
        recent = state.observation_history[-K_FULL_ROUNDS:]

        # 4. 更早轮次的摘要
        if len(state.observation_history) > K_FULL_ROUNDS:
            earlier = state.observation_history[:-K_FULL_ROUNDS]
            summary = self._summarize_history(earlier)
            messages.append({"role": "user", "content": f"[Earlier actions summary]\n{summary}"})

        # 5. 当前环境和状态
        messages.append({"role": "user", "content": self._format_current_state(state)})

        return messages

    def _summarize_history(self, observations: list[Observation]) -> str:
        """用 LLM 压缩历史记录"""
        # 保留关键信息：修改了哪些文件、测试结果、错误信息
        # 丢弃：中间探索性的 grep/read 操作
        pass
```

## 4. 关键接口设计

### 4.1 Agent-Tool 接口

```python
class Tool(ABC):
    """工具基类"""

    name: str
    description: str  # 给 LLM 的工具描述
    parameters: dict  # JSON Schema

    @abstractmethod
    async def execute(self, params: dict, workspace: str) -> ToolResult:
        """执行工具并返回结果"""
        pass

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI function calling 格式"""
        pass

class ToolResult:
    success: bool
    output: str         # 人类可读的输出
    structured: dict    # 结构化数据（可选）
    error: str | None
    artifacts: list[str]  # 产生的文件路径
```

### 4.2 Agent-LLM 接口

```python
class LLMInterface:
    def decide(self,
               messages: list[dict],
               tools: list[Tool],
               model: str = None) -> AgentAction:
        """向 LLM 请求下一步动作决策"""
        # 使用 OpenAI function calling 或等效机制
        # 支持 DeepSeek、OpenAI 两种后端
        pass

    def reflect(self,
                action: AgentAction,
                observation: Observation,
                context: str) -> str:
        """反思：从结果中提炼经验"""
        pass
```

## 5. 与现有系统的兼容

### 5.1 渐进迁移策略

```
Phase 1 (兼容模式)：保留现有 Pipeline，Agentic Loop 作为 repair 阶段的增强
Phase 2 (并行运行)：Agentic Loop 作为新的 task_type="agentic" 与旧模式并行
Phase 3 (默认模式)：Agentic Loop 成为默认，旧 Pipeline 作为 fallback
```

### 5.2 配置项设计

```yaml
workflow:
  mode: agentic  # pipeline | agentic
  agentic:
    max_iterations: 50
    max_tool_calls_per_iteration: 1
    context_window_size: 8000
    full_history_rounds: 5
    termination:
      max_iterations: 50
      stuck_window: 5
      test_pass_threshold: 0.95
  pipeline:  # 保留旧配置
    enable_quality_gates: true
    max_retry: 2
```

## 6. 数据设计

### 6.1 事件溯源（Event Sourcing）

```python
class EventStore:
    """持久化所有 Agent 行为事件"""

    events: list[Event]

    def append(self, event: Event):
        """追加事件"""

    def replay(self, until: int) -> AgentLoopState:
        """从事件重建状态"""
```

### 6.2 持久化与恢复

```python
class CheckpointManager:
    def save(self, state: AgentLoopState) -> str:
        """保存检查点到磁盘"""
        checkpoint = {
            "version": 2,
            "task_id": state.task_id,
            "current_iteration": state.current_iteration,
            "action_history": [a.__dict__ for a in state.action_history],
            "observation_history": [o.__dict__ for o in state.observation_history],
            "workspace_snapshot": self._git_snapshot(state.workspace_path)
        }
        # 写入 .devagent/checkpoints/{task_id}.json

    def restore(self, task_id: str) -> AgentLoopState:
        """从检查点恢复"""
```

## 7. 实施方案

### 7.1 实施阶段

| 阶段 | 内容 | 工期估计 | 产出物 |
|------|------|---------|--------|
| Phase 1 | Agentic Core + Shell/Grep/Read 工具 | 2周 | 最小可运行循环 |
| Phase 2 | Edit/Test/Git 工具 + 上下文管理 | 2周 | 完整工具链 |
| Phase 3 | 事件溯源 + Checkpointing | 1周 | 可恢复执行 |
| Phase 4 | SWE-bench 评测 + 调优 | 2周 | 评测报告 |

### 7.2 关键风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| LLM 决策质量不足 | 中 | 高 | 微调 System Prompt，增加 few-shot 示例 |
| 上下文溢出 | 高 | 中 | 分级压缩策略，Repo Map 引导 |
| 工具滥用（rm -rf 等） | 低 | 高 | 沙箱化执行，白名单命令 |
| 循环死锁 | 中 | 中 | 空转检测，最大迭代限制 |

## 8. 评估指标

| 指标 | 说明 | 目标值 |
|------|------|--------|
| SWE-bench Lite 解决率 | 标准评测得分 | > 25% |
| 平均修复步数 | 修复一个 bug 的平均工具调用数 | < 15 |
| 空转率 | 无进展的迭代占比 | < 10% |
| 首次尝试成功率 | 首次修复成功的比例 | > 60% |
