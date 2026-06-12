# DevAgent 系统架构与实现细节完整文档

> **版本**: 3.3 | **语言**: Python 3.10+ | **架构**: Plan-Execute-Gate + ReAct 双模 + 交互修改 | **测试**: 288 全部通过

---

## 目录

1. [项目总览与度量](#1-项目总览与度量)
2. [系统架构总图](#2-系统架构总图)
3. [模块详解: agentic/ — V2核心引擎](#3-模块详解-agentic--v2核心引擎)
4. [模块详解: agents/ — 8个专业Agent](#4-模块详解-agents--8个专业agent)
5. [模块详解: agent_core/ — 基础设施层](#5-模块详解-agent_core--基础设施层)
6. [模块详解: tools/ — 工具集](#6-模块详解-tools--工具集)
7. [模块详解: api/ + cli/ — 入口层](#7-模块详解-api--cli--入口层)
8. [模块详解: IDE扩展](#8-模块详解-ide扩展)
9. [完整交互链路详解](#9-完整交互链路详解)
10. [已知不足与改进方向](#10-已知不足与改进方向)

---

## 1. 项目总览与度量

### 1.1 项目定位

DevAgent 是一个**基于大语言模型的统一软件工程智能体平台**，实现需求→设计→编码→测试→修复的端到端自动化工作流。

### 1.2 代码量统计

| 层 | 模块数 | 代码行 | 占比 |
|---|--------|--------|------|
| **V2 Agentic 核心** | 25 文件 | 13,500 行 | 52% |
| **V1 Agent + Tools + Core** | 20 文件 | 5,200 行 | 20% |
| **API + CLI + IDE 扩展** | 5 文件 | 2,400 行 | 9% |
| **测试** | 14 文件 | 3,465 行 | 14% |
| **配置 + LSP + Benchmarks** | 5 文件 | 755 行 | 3% |
| **总计** | **69 文件** | **25,320 行** | 100% |

> 注: v3.3 新增 quality/ 包、prompt共享段（~400行）、交互修改模块（~300行）、IEEE 报告模板

### 1.3 文件树

```
DevAgent/
├── devagent/
│   ├── __init__.py
│   ├── agent_core/           # 基础设施层
│   │   ├── config_loader.py  # YAML配置加载
│   │   ├── llm_client.py     # LLM客户端(DeepSeek/OpenAI)
│   │   ├── state.py          # AgentState共享状态
│   │   ├── schemas.py        # 数据模型定义
│   │   ├── router.py         # 任务路由器
│   │   ├── workflow.py       # WorkflowController编排器
│   │   └── langgraph_workflow.py  # LangGraph工作流
│   │
│   ├── agentic/              # V2核心执行引擎
│   │   ├── __init__.py
│   │   ├── core.py           # DevAgentCore: ReAct主循环
│   │   ├── state.py          # AgentLoopState: 执行状态
│   │   ├── context.py        # ContextManager: 四层上下文管理
│   │   ├── tools.py          # 22种基础工具实现
│   │   ├── pipeline_runner.py    # PipelineRunner: Plan-Execute-Gate
│   │   ├── pipeline_tools.py     # V1Agent→V2工具适配器
│   │   ├── pipeline_validator.py # 确定性验证器
│   │   ├── interaction.py    # InteractionController: 交互控制
│   │   ├── review_gate.py    # PhaseReviewGate: 阶段审核
│   │   ├── session.py        # SessionManager: 多客户端管理
│   │   ├── thread_channel.py # 跨线程通信桥梁
│   │   ├── fault_locator.py  # 三层混合故障定位
│   │   ├── verification.py   # 形式化验证
│   │   ├── validation.py     # 即时验证
│   │   ├── planning.py       # PlannerAgent (V2)
│   │   ├── experience.py     # 经验存储与检索
│   │   ├── observability.py  # 可观测性(事件/流式/历史)
│   │   ├── sandbox.py        # Docker沙箱
│   │   ├── state_graph.py    # 状态图
│   │   ├── multi_agent.py    # 多Agent协作
│   │   ├── multimodal.py     # 多模态(图片读取)
│   │   └── events.py         # 事件总线
│   │
│   ├── agents/               # V1专业Agent
│   │   ├── base_agent.py     # BaseAgent抽象基类
│   │   ├── planner_agent.py  # 任务分解
│   │   ├── requirement_agent.py  # 需求分析
│   │   ├── design_agent.py   # 架构设计
│   │   ├── code_agent.py     # 代码生成
│   │   ├── test_agent.py     # 测试生成
│   │   ├── debug_agent.py    # 调试分析
│   │   ├── repair_agent.py   # Bug修复
│   │   └── review_agent.py   # 代码审查/报告
│   │
│   ├── tools/                # 工具集
│   │   ├── artifact_registry.py  # 中央产物仓库
│   │   ├── file_tool.py      # 文件操作
│   │   ├── quality.py        # 质量工具
│   │   ├── patch_tool.py     # 补丁工具
│   │   ├── test_runner.py    # 测试执行
│   │   ├── sandbox_runner.py # 沙箱运行
│   │   ├── static_analyzer.py # 静态分析
│   │   └── diagram_validator.py # 图表验证
│   │
│   ├── api/
│   │   ├── app.py            # FastAPI + WebSocket V2
│   │   └── ide_server.py     # IDE服务端
│   │
│   ├── cli/
│   │   └── main.py           # 统一CLI入口
│   │
│   ├── lsp/
│   │   └── server.py         # LSP语言服务器
│   │
│   ├── benchmarks/           # 基准测试
│   │   ├── benchmark_runner.py
│   │   ├── swebench_adapter.py
│   │   └── dr_cases/         # 测试用例
│   │
│   └── configs/
│       └── config.yaml       # 配置文件
│
├── vscode_extension/         # VSCode扩展
├── intellij_plugin/          # IntelliJ插件
├── eclipse_plugin/           # Eclipse插件
├── tests/                    # 测试套件(286用例)
├── docs/                     # 文档
├── Dockerfile                # 容器化
├── docker-compose.yml        # 编排
├── requirements.txt
├── setup.py
└── pyproject.toml
```

---

## 2. 系统架构总图

```
                        用户入口
          CLI ──── API ──── VSCode Extension
           │         │           │
           │    ┌────┴────┐      │
           │    │ FastAPI │      │
           │    │ WS V2   │      │
           │    └────┬────┘      │
           │         │           │
           └────┬────┴────┬──────┘
                │         │
        ┌───────┴─────────┴───────┐
        │      模式路由            │
        │  Pipeline Mode / ReAct  │
        └───────┬─────────┬───────┘
                │         │
    ┌───────────┴──┐  ┌───┴──────────┐
    │PipelineRunner│  │ DevAgentCore │  双执行引擎
    │ (代码驱动)     │  │ (LLM自主)    │
    └───────┬──────┘  └───────┬──────┘
            │                 │
    ┌───────┴─────────────────┴───────┐
    │         30 种工具               │
    │  8 Pipeline + 14 Code + 8 Etc  │
    └───────────────┬─────────────────┘
                    │
    ┌───────────────┴─────────────────┐
    │        交互控制层               │
    │  ThreadChannel (跨线程通信)     │
    │  SessionManager (多客户端)      │
    │  PhaseReviewGate (阶段审核)     │
    │  InteractionController (审批)   │
    │  TerminalChannel (CLI终端)      │
    │  PipelineValidator (确定验证)   │
    └───────────────┬─────────────────┘
                    │
    ┌───────────────┴─────────────────┐
    │        增强模块                 │
    │  ContextManager(4层缓存)        │
    │  FaultLocator(3层融合)          │
    │  DeterministicValidator         │
    │  ExperienceStore / Sandbox / ...│
    └─────────────────────────────────┘
```

### 分层职责

| 层 | 核心职责 |
|---|---------|
| **入口层** | 提供 CLI / REST API / WebSocket / IDE Extension 多种用户交互方式 |
| **路由层** | 根据 --mode 参数路由到不同执行引擎 |
| **执行引擎层** | PipelineRunner 提供确定性流程控制; DevAgentCore 提供LLM自主决策 |
| **工具层** | 30种工具覆盖文件、搜索、执行、版本、交互、Pipeline全场景 |
| **交互控制层** | 跨线程通信、多客户端管理、阶段审核、危险操作审批、进度流式推送 |
| **增强模块层** | 上下文缓存优化、故障定位、产物仓库、经验学习、沙箱隔离 |

### 数据流

```
用户输入 → 模式路由 → 执行引擎决策 → 工具调用 → 交互审核 → 产物写入
                                    ↑                        ↓
                              ContextManager            ArtifactRegistry
                              (上下文构建)              (产物索引)
```

---

## 3. 模块详解: agentic/ — V2核心引擎

### 3.1 DevAgentCore — `core.py` (906行)

**用途**: 整个系统的中枢,实现 ReAct (Think→Act→Observe) 自主循环。

#### 输入
| 参数 | 类型 | 说明 |
|------|------|------|
| `task_description` | str | 自然语言任务描述(含需求文档内容) |
| `workspace` | str | 工作目录路径,默认"." |
| `language` | str | 目标编程语言,默认"python" |
| `max_iterations` | int | 最大迭代次数,默认100 |
| `config_path` | str | 配置文件路径,可选 |

#### 输出
| 类型 | 说明 |
|------|------|
| `AgentLoopState` | 包含完整执行历史、修改文件列表、测试结果、状态的执行状态对象 |

#### 核心技术细节

**1. TerminationChecker (6种终止条件)**

| 条件 | 触发逻辑 |
|------|---------|
| `agent_submitted` | Agent调用submit工具, structured.submitted=True |
| `agent_declared_failure` | 输出包含 "cannot complete" 或 "unable to fix" |
| `all_tests_pass` | test_results中failed=0且collected>0 |
| `max_iterations_reached` | current_iteration >= max_iterations |
| `stuck_no_progress` | 最近stuck_window次操作无生产性工具调用 |
| `consecutive_errors` | 最近3次observation全部success=False |

生产性工具包括: file_edit/write, submit, test_run, shell_run, 所有pipeline工具, request_review, ask_user, grep/find类

**2. ActionParser — LLM输出解析**

```
解析格式: THOUGHT: <推理> \n ACTION: <工具名> \n PARAMS: <JSON参数>
```

- 主正则: `THOUGHT:\s*(.+?)\s*\n\s*ACTION:\s*(\w+)\s*\n\s*PARAMS:\s*(\{.+?\})\s*$`
- 回退1: 仅匹配ACTION行
- 回退2: 正则提取 key="value" 和 key=数字 模式
- JSON解析失败时用 `re.findall(r'\{[^{}]*\}', ...)` 提取第一个合法JSON

**3. 主循环架构 (5个交互检查点)**

```
while not state.is_terminal():
    CP1: check_commands() → 处理用户实时指令
    CP2: handle_pause → 暂停循环等待恢复
    CP3: check_abort → 终止检测
    → 强制工具注入(跳过LLM) OR LLM决策
    CP4: pre_action hooks → 审批门
    → 执行工具
    CP5: post_action hooks → 进度发布
    → 终止条件检查
```

**4. 强制工具注入机制**

当review响应后,系统跳过LLM推理,直接执行指定工具:
```python
forced = getattr(state, '_forced_next_tool', '')
if forced:
    # 1. 清除forced标记(仅执行一次)
    # 2. 构建action(programmatic, no LLM)
    # 3. 执行工具
    # 4. 继续循环(让LLM接管下一步)
```

**5. Side Effects处理**

| 工具 | 副作用 |
|------|--------|
| file_edit / file_write | 记录modified_files, 通知ContextManager缓存失效 |
| request_review (approved) | 设置_forced_next_tool指向下一阶段工具 |
| request_review (revise) | 设置_forced_next_tool指向重做工具,注入feedback |
| file_edit / file_write | 触发InstantValidator即时语法检查 |
| test_run (有失败) | 异步触发FaultLocalizationPipeline |

**6. LLM调用**

- OpenAI: 使用原生 Function Calling (`/chat/completions` + tools参数)
- DeepSeek: 使用文本合并方式(将所有消息拼成一个prompt)

#### 使用方法

```bash
# CLI
agent --mode agentic --workspace ./my_project/

# Python API
from devagent.agentic.core import DevAgentCore
core = DevAgentCore()
state = core.execute("Build a calculator app", workspace="./my_project")
```

```python
# 带交互模式
core = DevAgentCore()
core.enable_interaction(mode="full")  # full/approval/observe/off
state = core.execute("...")

# Pipeline模式
state = core.run_pipeline("Build a calculator", workspace=".")
```

#### 不足

1. **文本模式LLM调用的JSON解析脆弱**: DeepSeek不使用原生Function Calling,依赖正则解析THOUGHT/ACTION/PARAMS格式,模型输出格式不稳定时可能解析失败
2. **强制工具注入的参数构建不完整**: `_build_force_params` 仅在review approve/revise后构建参数,如果参数构建不正确,下一阶段的工具可能拿到错误输入
3. **LLM调用错误处理简单**: `_call_text_based` 中所有消息拼成一个字符串,可能超过token限制
4. **同步/异步混用**: `execute()` 用 `asyncio.run()` 包装,在某些已有事件循环的环境中会失败
5. **Pipeline模式的callback是print**: `run_pipeline()` 中的callback只是简单print,缺乏正式的事件发布机制

---

### 3.2 PipelineRunner — `pipeline_runner.py` (585行)

**用途**: Plan-Execute-Gate流水线执行器。**代码决定流程,LLM只负责内容生成**。这是DevAgent稳定性的核心。

#### 输入
| 参数 | 类型 | 说明 |
|------|------|------|
| `llm_client` | LLMClient | LLM客户端实例 |
| `tools` | ToolRegistry | 工具注册表 |
| `thread_channel` | ThreadChannel | 跨线程通信通道(可选) |
| `workspace` | str | 工作目录 |
| `phases` | list[PhaseConfig] | 阶段配置,默认5阶段 |

#### 输出
| 类型 | 说明 |
|------|------|
| `PipelineState` | 包含所有阶段结果、产物累积、最终状态的流水线状态 |

#### 核心技术细节

**1. 默认5阶段流水线**

| 阶段 | 工具 | 输出目录 | 必须产物 | 超时 |
|------|------|---------|---------|------|
| requirements | analyze_requirements | 01_requirements | requirement_specification.md + structured_requirements.json | 600s |
| design | design_architecture | 02_design | architecture_design_spec.md + design_artifacts.json | 480s |
| implementation | generate_code | 03_implementation | src/calculator.py (wait_files可配置) | 300s |
| testing | generate_tests | 04_tests | pytest_result.json | 300s |
| delivery | generate_report | 06_reports | executive_report.md | 120s |

**2. 每阶段执行流程**

```
1. _run_tool() → 执行LLM工具,传入accumulated state
2. 文件存在验证: 检查wait_files是否全部生成
3. _run_checks() → 确定性验证(syntax + lint)
4. _submit_review() → 通过ThreadChannel提交人工审核
5. 处理审核决策:
   - approve → _update_state_from_phase → 下一阶段
   - revise → task_desc注入feedback → 重做当前阶段(max 2次)
   - reject → 标记FAILED,终止
   - timeout/auto → 自动推进
```

**3. 质量强制前缀**

每个工具执行前注入质量要求:
```
## LANGUAGE: ALL documents MUST be in SIMPLIFIED CHINESE
## QUALITY: Complete professional output
## TESTING: Every public function needs 1 happy-path + 1 error-path test
```

**4. 确定性验证**

```python
class DeterministicValidator:
    def validate(phase, files) -> ValidationReport
    # requirements: syntax only
    # design: syntax only
    # implementation: syntax + lint + import check (阻塞)
    # testing: syntax + pytest (阻塞,全部必须通过)
    # delivery: syntax only
```

**5. 产物文件发现**

`_extract_generated_files()` 采用双策略:
- 从工具返回的structured中提取 code_files/test_files/modified_files
- 扫描对应阶段的输出目录,收集所有 .py/.md/.json/.mmd 文件

#### 使用方法

```python
from devagent.agentic.pipeline_runner import PipelineRunner, run_full_pipeline

# 方式1: 直接构建
runner = PipelineRunner(llm_client, tools, channel, workspace=".")
state = runner.run(task_description="Build a calculator app")

# 方式2: 通过DevAgentCore
from devagent.agentic.core import DevAgentCore
core = DevAgentCore()
state = core.run_pipeline("Build a calculator", workspace=".", output_root="./outputs")
```

#### 不足

1. **wait_files硬编码**: 默认phases中 `wait_files=["03_implementation/src/calculator.py"]` 是示例代码的产物名,不同项目需要手动调整
2. **ruff/lint检查被跳过**: `_run_checks()` 中lint永远返回 "SKIPPED" (注释写着"Ruff check too slow for inline")
3. **review无LLM质量评估**: Pipeline模式由于性能考虑,不启用QualityEvaluator,直接展示原始deterministic check结果
4. **阶段间状态传递简单**: `_update_state_from_phase()` 只是直接拷贝structured输出,没有做schema验证
5. **asyncio.run()在后台线程中容易冲突**: 如果调用方已经在event loop中,会报错

---

### 3.3 AgentLoopState — `state.py` (135行)

**用途**: ReAct循环的执行状态,支持checkpoint保存/恢复。

#### 核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| task_id | str | 自动生成的UUID短ID |
| task_type | str | 任务类型(agentic/design/implement/...) |
| workspace | str | 工作目录绝对路径 |
| task_description | str | 任务自然语言描述 |
| current_iteration | int | 当前循环迭代次数 |
| max_iterations | int | 最大迭代次数限制 |
| status | str | INIT→RUNNING→COMPLETED/FAILED/STUCK |
| action_history | list[dict] | 完整Action历史(含timestamp/iteration) |
| observation_history | list[dict] | 完整Observation历史 |
| modified_files | list[str] | 已修改文件列表(去重) |
| test_results | dict | 最近测试结果 {passed, failed, collected} |
| sub_tasks | list[dict] | 从Planner分解的子任务 |
| checkpoint_dir | str | 检查点存储目录 |

#### 核心技术细节

**1. Checkpoint机制**

```python
def to_checkpoint() -> dict  # 序列化所有状态到字典(含完整历史)
def from_checkpoint(data)    # 从字典恢复
def save(directory)          # 写入JSON文件(.devagent/checkpoints/)
def restore(task_id, dir)    # 从文件恢复
```

**2. 进度计算**

```python
def progress_ratio() -> float:
    return min(1.0, current_iteration / max(max_iterations, 1))
```

#### 不足

1. **checkpoint文件可能很大**: action/observation历史全量保存,长时间运行的任务checkpoint可达数MB
2. **modified_files去重用list而非set**: `list(dict.fromkeys(...))` 在大量文件时效率不如set
3. **无增量checkpoint**: 每次save都全量写入,不支持追加

---

### 3.4 ContextManager — `context.py` (1107行)

**用途**: 四层缓存优化的上下文管理,降低token消耗和幻觉风险。

#### 输入
| 参数 | 说明 |
|------|------|
| `repo_path` | 仓库根路径 |
| `task` | 任务描述 |
| `state` | AgentLoopState实例 |
| `tool_descriptions` | 工具描述文本 |

#### 输出
| 类型 | 说明 |
|------|------|
| `list[dict]` | 标准LLM messages列表,按System/RepoMap/Dynamic分层 |

#### 核心技术细节

**1. 四层消息架构**

```
MESSAGE 1: SYSTEM (完全静态,可跨轮次缓存)
  ├─ BASE_SYSTEM_PROMPT (任务指令+输出格式+反幻觉规则)
  ├─ TOOL_CATALOG_HEADER (工具目录)
  └─ 阶段提示 (EXPLORATION/EDITING/VERIFICATION)

MESSAGE 2: REPO MAP (半静态,文件变化时增量更新)
  ├─ 目录树 (含函数/类签名)
  ├─ 模块依赖图
  ├─ 关键符号表
  └─ 测试→源文件映射

MESSAGE 3: DYNAMIC CONTEXT (每轮变化)
  ├─ Grounding signals (反幻觉锚点)
  ├─ 相关文件检索
  ├─ Focus context (当前编辑文件+依赖)
  ├─ 压缩的早期历史
  └─ 近期详细历史+状态
```

**2. 缓存策略**

| 缓存项 | 刷新策略 | Hash方法 |
|--------|---------|---------|
| System Prompt | 内容不变时永久缓存 | SHA256前16字符 |
| Repo Map | 文件增删/修改mtime变化时重建 | SHA256前16字符 |
| File Signatures | 基于mtime增量,仅解析修改的文件 | AST解析 |
| File Imports | 与signature同步缓存 | AST解析 |

缓存命中率提升: 约50%的上下文可跨轮次复用,降低延迟和API成本。

**3. ContextBudget — 阶段感知Token分配**

```
exploration: relevant_files=3000 focus=1500 history=1000
editing:      focus=3500 history=1500 relevant=1000
verification: history=2500 focus=1500 relevant=1000
```

**4. HallucinationGuard — 四类反幻觉检查**

| 检查 | 机制 |
|------|------|
| validate_edit_target | 编辑前检查文件是否真实存在(os.path.exists) |
| validate_function_name | AST验证引用的函数名是否在文件中定义 |
| build_grounding_context | 在prompt中注入"Files You Have Read"和"Files You Have Modified" |
| 歧义标记 | 5+轮探索无修改时标记[UNCERTAIN] |

**5. PhaseDetector — 执行阶段检测**

```python
# 基于最近3个action的工具类型判断:
# exploration: grep/find/list类工具多 → 分配更多搜索token
# editing: file_edit/write类工具多 → 分配更多文件内容token
# verification: test_run/lint类工具多 → 分配更多错误输出token
```

**6. HistoryCompressor — 早期历史压缩**

将早期轮次(保留最近5轮)压缩为结构化摘要:
- 已读文件列表
- 已修改文件+操作
- 测试结果历史
- 关键发现(find/grep结果)
- 遇到的错误

**7. ContextualToolFilter — 阶段工具过滤**

```python
exploration: grep_text, grep_ast, find_symbol, file_list, file_read, web_search, read_docs
editing: file_read, file_edit, file_write, lint_check, git_diff, shell_run
verification: test_run, lint_check, git_diff, git_log, git_blame, file_read, file_edit
```

缩小决策空间,降低模型选错工具的概率。

**8. RepoMap生成**

- `_generate_tree()`: 递归遍历目录树,Python文件自动附加函数/类签名
- `_generate_dependencies()`: 解析所有import语句构建模块依赖图
- `_generate_symbols()`: 收集所有函数/类定义(上限50条)
- `_map_tests()`: 建立 test_*.py → 源文件的映射关系

#### 使用方法

```python
from devagent.agentic.context import ContextManager

ctx = ContextManager(workspace)
messages = ctx.build_messages(task, state, tool_descriptions)
# → [{"role": "system", "content": "..."},   # cache-stable system prompt
#    {"role": "system", "content": "..."},   # repo map
#    {"role": "user", "content": "..."}]     # dynamic context
```

#### 不足

1. **RepoMap的skip_dirs硬编码**: 跳过的目录列表在代码中硬编码,不支持用户自定义
2. **签名解析仅限顶层**: `get_or_parse_signature()` 只解析`ast.iter_child_nodes`,嵌套函数/类不被识别
3. **Token预算为估算值**: 使用 `1 token ≈ 3 chars (English)` 估算,中文实际消耗更多
4. **ContextualToolFilter不包含Pipeline工具**: 阶段工具黑白名单只有基础工具,8个Pipeline工具不在过滤范围内
5. **RelevantFileRetriever的关键词匹配粗糙**: 去停用词后简单匹配文件名和内容,无语义相似度

---

### 3.5 ToolRegistry + 22种基础工具 — `tools.py` (1785行)

**用途**: 工具抽象层和22种基础工具实现。

#### 核心抽象

```python
class BaseTool(ABC):
    name: str           # 工具名
    description: str    # 工具描述
    parameters: dict    # JSON Schema参数定义

    async def execute(params, workspace) -> ToolResult  # 抽象方法
    def to_openai_schema() -> dict                      # 生成Function Calling Schema
    def description_text() -> str                       # 人类可读描述
```

```python
class ToolRegistry:
    def register(tool)     # 注册工具
    def execute(name, params, workspace) -> ToolResult  # 异步执行
    def get_openai_schemas() -> list[dict]  # 生成所有工具的OpenAI Schema
    def get_descriptions() -> str           # 所有工具的人类可读描述
    def create_default(workspace, llm_client, include_pipeline) -> ToolRegistry  # 工厂方法
```

#### 22种基础工具详解

| # | 工具名 | 类别 | 输入 | 输出 | 核心实现 |
|---|--------|------|------|------|---------|
| 1 | **file_read** | 文件 | path, offset?, limit? | 带行号的文件内容 | Path.read_text + 行号前缀 |
| 2 | **file_edit** | 文件 | path, old_string, new_string, replace_all? | unified diff | str.replace + difflib.unified_diff |
| 3 | **file_write** | 文件 | path, content | 写入确认 | Path.write_text + mkdir parents |
| 4 | **file_list** | 文件 | path?, depth?, include? | 目录树 | os.scandir递归, 跳过隐藏/缓存目录 |
| 5 | **grep_text** | 搜索 | pattern, path?, include?, context_lines? | 匹配列表(max50) | re.compile + rglob, 按行匹配 |
| 6 | **grep_ast** | 搜索 | query, name?, path? | AST查询结果 | ast.parse + ast.walk |
| 7 | **find_symbol** | 搜索 | name, kind?, path? | 定义+引用位置 | AST遍历找FunctionDef/ClassDef/Call/Attribute |
| 8 | **shell_run** | 执行 | command, timeout? | stdout+stderr | asyncio.create_subprocess_shell |
| 9 | **test_run** | 执行 | paths?, filter?, verbose? | pytest输出+summary | 自动发现测试目录, SmartRegressionSelector |
| 10 | **lint_check** | 执行 | path, checkers? | 语法/lint/导入检查 | ast.parse + ruff subprocess + python import check |
| 11 | **git_diff** | 版本 | staged? | diff输出 | git diff subprocess |
| 12 | **git_log** | 版本 | max_count?, path?, oneline? | 提交历史 | git log subprocess |
| 13 | **git_blame** | 版本 | path, start_line?, end_line? | blame输出 | git blame subprocess |
| 14 | **web_search** | 信息 | query | 搜索结果(max8) | DuckDuckGo Lite爬取 |
| 15 | **read_docs** | 信息 | path, page? | 文档内容/列表 | 文件读取+目录分页 |
| 16 | **gh_issue_read** | GitHub | issue(owner/repo#N) | Issue内容 | GitHub REST API |
| 17 | **gh_pr_create** | GitHub | title, body, base?, draft? | PR URL | gh CLI subprocess |
| 18 | **gh_pr_comment** | GitHub | pr, body | 确认 | gh CLI subprocess |
| 19 | **image_read** | 多模态 | path | 图片分析 | multimodal.py (base64编码) |
| 20 | **submit** | 控制 | summary | 提交确认 | structured.submitted=True |
| 21 | **ask_user** | 交互 | question, context?, options?, default? | 用户回答 | InteractionController.ask_user() |
| 22 | **request_review** | 交互 | phase, title, summary, files_changed, self_assessment | 审核决策 | PhaseReviewGate.submit_for_review() |

#### 安全机制

| 机制 | 实现 |
|------|------|
| **PathSandbox** | 所有文件操作限制在workspace内,`Path.resolve()` + prefix检查 |
| **危险命令拦截** | ShellRunTool.DANGEROUS列表,正则匹配 `rm -rf /`, `sudo`, `mkfs` |
| **GitHub可用性检测** | `GITHUB_TOKEN` 环境变量或 `gh auth status` 返回值检查 |
| **文件修改验证** | file_edit 检查 old_string 是否存在且唯一(或replace_all) |
| **测试超时** | 每条命令120s超时,测试发现10s超时 |

#### 核心实现细节

**test_run的SmartRegressionSelector**:
```python
# 自动发现测试目录: tests/ → 03_implementation/tests/ → 04_tests/tests/ → test/
# 智能回归: 通过git diff获取修改文件,仅运行相关测试
```

**lint_check的三步检查**:
```python
# 1. syntax: ast.parse() 即时检查
# 2. lint: ruff check子进程
# 3. import: python -c "import ast; ast.parse(...)" 检查
```

**request_review的速率限制和顺序锁**:
```python
# 全局计数器: _global_review_count > 3 → 强制submit
# 顺序锁: _approved_phases set, 已批准阶段不可重复
# 修订上限: _revise_counts[phase] >= 3 → 自动批准
# 文件验证: files_changed每项os.path.exists
```

#### 不足

1. **web_search依赖DuckDuckGo Lite**: 无API Key的免费方案,但HTML解析脆弱,DDG改版可能导致搜索失效
2. **git操作无认证管理**: 依赖系统级git配置,在容器中可能未配置
3. **context_lines实现低效**: grep时rglob后逐文件读取全文,大仓库慢
4. **test_run的SmartRegressionSelector是best-effort**: git diff可能失败,测试选择可能不准确
5. **GH PR comment无文件附件**: 不能上传patch/diff到PR
6. **image_read依赖模型的多模态能力**: DeepSeek多模态能力弱
7. **submit只是标记**: 没有实际的产物打包/导出功能
8. **PathSandbox无法防止符号链接逃逸**: 只用prefix检查,`realpath`可以绕过

---

### 3.6 InteractionController — `interaction.py` (873行)

**用途**: 人机交互的总控制器,桥接Agent循环与用户交互通道。

#### 输入/输出

**接收**: action(待执行), state(执行状态)
**产出**: 审批决策(approve/deny), 用户命令, 流式事件

#### 核心技术细节

**1. Hook架构**

```python
# DevAgentCore调用:
await controller.pre_action(action, state)    # 返回False阻止操作
await controller.post_action(action, result, state)
await controller.check_commands(state)
```

**2. 审批分类 (4种)**

| 审批类型 | 触发条件 | 超时 |
|---------|---------|------|
| DESTRUCTIVE_SHELL | 匹配`rm -rf`, `sudo`, `mkfs`等 | 120s |
| LARGE_EDIT | old_string > 50行 | 60s |
| UNCERTAIN_FIX | 低置信度修复 | 300s |
| UNVERIFIED_EDIT | 编辑未读取的文件 | 60s |

**3. 通道优先级**

```
1. ThreadChannel (后台Agent线程) → threading.Event阻塞等待
2. WebSocket (WS客户端在线) → asyncio.Future等待
3. TerminalChannel (stdin.isatty) → 终端输入
4. 自动决策 (无可用通道) → auto_approve
```

**4. TerminalChannel**

- 后台线程读取stdin,放入asyncio.Queue
- `prompt_approval()`: 打印审批描述+选项,等待终端输入
- `prompt_review()`: 打印阶段审核信息,支持[A]pprove/[R]evise/re[J]ect
- `prompt_question()`: 打印Agent问题+编号选项

**5. 用户命令类型**

| 命令 | 效果 |
|------|------|
| PAUSE | 暂停Agent循环 |
| RESUME | 恢复运行 |
| ABORT | 标记终止+保存checkpoint |
| REDIRECT | 改变任务焦点 |
| INJECT_CONTEXT | 注入用户上下文到prompt |
| RETRY | 注入重试提示 |

**6. ProgressStreamer**

- 每500ms(可配置)发布进度快照
- 快照包含: status, iteration, phase, progress_pct, modified_files, test_summary, recent_steps
- 双推: streaming event + session broadcast

#### 使用方法

```python
# 在DevAgentCore中:
core = DevAgentCore()
core.enable_interaction(mode="full")
# 内部创建 InteractionController + SessionManager + ProgressStreamer

# 启用后自动:
# - 危险操作弹窗审批
# - 阶段审核推送
# - Agent问题交互
# - 进度流式推送
```

#### 不足

1. **TerminalChannel在IDE环境中不工作**: stdin.isatty()在VSCode集成终端中可能返回False
2. **审批决策无持久化**: approve_all策略只在内存中,重启后丢失
3. **max_questions_per_task限制粗糙**: 按计数硬限制,不区分问题重要性

---

### 3.7 PhaseReviewGate — `review_gate.py` (906行)

**用途**: 阶段边界的人工审核系统,包含LLM质量评估和格式化审核文档生成。

#### 核心组件

| 组件 | 职责 |
|------|------|
| QualityEvaluator | LLM驱动的10维质量评分 |
| ReviewFormatter | 生成结构化Markdown审核文档 |
| ReviewSession | 管理审核生命周期(超时、决策、回滚) |
| PhaseReviewGate | 编排上述组件的总入口 |
| ArtifactBuilder | 从常见模式构建ReviewArtifact |

#### 审核流程

```
Agent调用request_review
  → PhaseReviewGate.submit_for_review()
    → 1. 创建ReviewRequest(含artifacts, summary, self_assessment)
    → 2. QualityEvaluator.evaluate() — LLM 10维评分
    → 3. ReviewFormatter.format() — 生成Markdown审核文档
    → 4. 推送到交互通道 (ThreadChannel > WS > Terminal > Auto)
    → 5. ReviewSession.wait_for_decision() — 阻塞等待
    → 6. 返回决策: approve / revise (+feedback) / reject (+reason)
```

#### 10维质量评估标准

```json
{
  "code_correctness": "PASS/NEEDS_WORK/MISSING/N/A",
  "code_structure": "...",
  "error_handling": "...",
  "type_safety": "...",
  "documentation": "...",
  "test_coverage": "...",
  "test_isolation": "...",
  "report_completeness": "...",
  "report_specificity": "...",
  "report_professionalism": "..."
}
```

#### 5个质量等级

| 等级 | 标准 | 图标 |
|------|------|------|
| critical_failure | 根本性错误或不可用 | ❌ |
| needs_major_rework | 缺少关键元素 | ⚠️ |
| needs_minor_fixes | 大体正确,有小问题 | 🔧 |
| meets_standard | 达到生产标准 | ✅ |
| exceeds_standard | 超出最低要求 | 🌟 |

#### ArtifactBuilder便捷方法

| 方法 | 用途 |
|------|------|
| `from_code_files(files, desc, workspace)` | 从代码文件构建 |
| `from_test_results(test_files, results, desc)` | 从测试结果构建 |
| `from_diff(modified_files, diff_text, desc)` | 从git diff构建 |
| `from_plan(plan_data, desc)` | 从执行计划构建 |

#### 审核文档格式(ReviewFormatter输出)

生成的Markdown包含:
1. 阶段标题 + 元数据(task_id, status, timestamp)
2. Agent自述摘要 + 自我评估
3. 自动质量评分 (10维表格 + 等级图标)
4. 优点 / 缺点 / 具体建议
5. 提交的Artifacts清单 (含文件内容预览)
6. 人工操作区 (Approve / Revise / Reject + 反馈输入)

#### 不足

1. **QualityEvaluator依赖LLM**: 当llm_client不可用时使用`_fallback_evaluation`,返回默认"needs_minor_fixes"
2. **LLM评估token消耗**: 如果code文件很大,`_build_eval_context`可能发送大量代码给LLM (>5000 chars截断)
3. **ReviewSession无持久化**: 审核历史和决策只在内存,服务重启丢失
4. **无批量审核支持**: 一次只能一个阶段,不支持批量多个阶段

---

### 3.8 ThreadChannel — `thread_channel.py` (229行)

**用途**: 跨线程通信桥梁。当Agent在后台线程运行而WebSocket在uvicorn事件循环时,asyncio.Future无法跨线程使用。

#### 核心技术

```python
@dataclass
class ThreadRequest:
    _event: threading.Event    # 线程安全信号
    _response: dict            # 响应数据

    def wait(timeout) -> dict:    # threading.Event.wait() 阻塞Agent线程
    def resolve(decision, feedback):  # WS handler调用,set()解除阻塞
```

#### 三种请求类型

| 类型 | 用途 |
|------|------|
| approval | 危险操作审批 |
| review | 阶段成果审核 |
| question | Agent向用户提问 |

#### 工作流程

```
Background Thread                    Uvicorn Event Loop
┌──────────────────┐                ┌──────────────────┐
│ Agent调用工具      │                │ WebSocket Handler │
│   ↓              │                │   ↓              │
│ ThreadChannel    │ ──创建请求──→  │ 推送到WS客户端    │
│   .create_xxx()  │                │   ↓              │
│   ↓              │                │ 用户做出决策      │
│ ThreadRequest    │                │   ↓              │
│   .wait() 阻塞   │ ←──set()解除── │ ThreadChannel    │
│   ↓              │                │   .resolve()     │
│ 获取决策继续执行   │                │                  │
└──────────────────┘                └──────────────────┘
```

#### 轮询机制

```python
# ThreadChannel._has_clients() 每0.5s轮询一次,最多15s
# 15s后无客户端连接 → 超时auto-approve
```

#### 不足

1. **单请求阻塞**: 同一时间只能有一个pending请求
2. **轮询浪费CPU**: 0.5s间隔的忙等,不如用condition variable
3. **无请求队列**: 后到的请求会覆盖先前的pending请求

---

### 3.9 FaultLocator — `fault_locator.py` (964行)

**用途**: 三层混合故障定位,结合动态SBFL、静态AST分析和LLM推理。

#### 三层架构

```
Layer 1: SBFL (频谱故障定位)
  → 运行每个测试 + 收集执行轨迹
  → Ochiai公式计算可疑度
  → 输出排序的语句列表

Layer 2: Static Analysis (AST静态分析)
  → 5类缺陷模式检测
  → 输出StaticIssue列表

Layer 3: LLM Fusion (LLM融合推理)
  → 取SBFL∩Static的文件(优先级最高)
  → LLM综合分析给出精确定位+修复建议
  → 输出FaultReport
```

#### Layer 1: TraceCollector + SBFLocalizer

**TraceCollector**:
```python
def run_test(test_file, test_func) -> TraceResult:
    # 1. 运行 pytest {file}::{func} --tb=long
    # 2. 从traceback提取 File "path", line N, in func
    # 3. 对通过的测试,解析imports估算覆盖
```

**SBFLocalizer**:
```python
# Ochiai公式: suspiciousness = a_ef / sqrt((a_ef + a_nf) * (a_ef + a_ep))
# a_ef: 执行该语句的失败测试数
# a_ep: 执行该语句的通过测试数
# a_nf: 未执行该语句的失败测试数
```

#### Layer 2: StaticAnalyzer (5类检查)

| 检查类 | 检测内容 | 置信度 |
|--------|---------|--------|
| C1: null_check | 返回值可能为None但未检查 | 0.65 |
| C2: boundary | 索引/除法缺少边界守卫 | 0.45-0.5 |
| C3: exception | risky函数未包裹try/except | 0.35 |
| C4: type | 不兼容类型比较 | 0.3 |
| C5: propagation | 空except块/裸except | 0.55-0.7 |

#### Layer 3: LLMFaultLocalizer

输入: 错误信息 + SBFL排名 + Static警告 + 代码片段
输出: JSON定位结果(bug_file, bug_line, bug_function, root_cause, fix_code, confidence)

#### FaultLocalizationPipeline (端到端编排)

```python
async def localize(test_path, source_path, error_info) -> FaultReport:
    # 1. 并行运行 SBFL + Static分析
    # 2. 收集SBFL+Static交集的代码文件
    # 3. LLM融合推理
    # 4. 构建FaultReport(包含所有三层结果)
```

#### 使用方法

```python
from devagent.agentic.fault_locator import FaultLocalizationPipeline

pipeline = FaultLocalizationPipeline(workspace, llm_client)
report = await pipeline.localize(
    test_path="tests/",
    source_path="src/",
    error_info={"message": "3 tests failed"}
)
print(f"Bug: {report.bug_file}:{report.bug_line} in {report.bug_function}()")
print(f"Root cause: {report.root_cause}")
print(f"Fix: {report.fix_suggestion}")
```

#### 不足

1. **traceback提取是heuristic**: 不是真正的coverage instrumentation,只从pytest输出提取traceback帧
2. **通过的测试覆盖估算粗糙**: `_estimate_imported_stmts`只是import-based估算,不是真实执行路径
3. **StaticAnalyzer无父节点引用**: `_enclosing_function`, `_has_null_check`, `_has_surrounding_boundary_guard`都返回默认值或False
4. **LLM prompt可能超token**: code_snippets中每个函数2000字符,多文件可能超限
5. **SBFL需要逐个运行测试**: 大测试套件(>100 test)会非常慢

---

### 3.10 VerificationGate — `verification.py` (378行)

**用途**: 形式化验证,包括符号执行和合约检查。

#### 四级验证层次

```
L1: Syntax + Lint (始终执行)
L2: Unit Tests + Mutation (始终执行)
L3: Symbolic Execution (高重要性代码)
L4: Formal Verification (关键代码)
```

#### CodeImportanceClassifier

基于关键词自动分类代码重要性:
- auth/payment/data_integrity/security 四类关键词
- 文件名、函数名、代码内容三处匹配加权评分
- 分级: low → medium → high → critical

#### SymbolicExecutor

```python
# 基于CrossHair工具进行符号执行
# 1. 从注释提取@requires/@ensures/@raises合约
# 2. 或通过LLMContractGenerator生成合约
# 3. 生成CrossHair验证脚本
# 4. 运行crosshair check,解析counterexample
```

#### 不足

1. **CrossHair依赖**: 需要额外的pip包 `crosshair-tool`,不是默认安装
2. **合约提取简单**: `ContractExtractor`只支持正则匹配docstring,不支持Python typing
3. **LLM生成的合约质量不确定**: 没有合约正确性验证

---

### 3.11 其他agentic/模块简述

| 模块 | 行数 | 用途 |
|------|------|------|
| **events.py** | - | EventBus事件总线,支持订阅/发布,ConsoleEmitter+FileLogger |
| **observability.py** | - | StreamingServer(SSE), HumanInTheLoop, TaskHistoryManager |
| **planning.py** | - | V2的PlannerAgent,将自然语言任务分解为ExecutionPlan DAG |
| **experience.py** | - | ExperienceStore(经验库), ExperienceInjector(检索), ExperienceRecorder(记录) |
| **sandbox.py** | - | SandboxManager管理Docker容器,提供ContainerSpec配置 |
| **validation.py** | - | InstantValidator即时验证,SmartRegressionSelector智能测试选择 |
| **session.py** | - | SessionManager多客户端管理,断线重连,事件重放 |
| **state_graph.py** | - | 基于LangGraph的状态图定义 |
| **multi_agent.py** | - | 多Agent协作编排 |
| **multimodal.py** | - | 图片读取(Base64编码+LLM理解) |
| **pipeline_validator.py** | - | 确定性验证器(syntax+lint+test) |

---

## 4. 模块详解: agents/ — 8个专业Agent

### 4.1 BaseAgent — `base_agent.py` (173行)

**用途**: 所有V1 Agent的抽象基类,提供自反射和产物保存能力。

#### 输入/输出
- **run(state: AgentState) → AgentState**: 抽象方法,每个Agent实现各自逻辑
- **reflect_and_refine(output, context, max_iterations=2) → dict**: 自动质量审查

#### 自反射机制

```
1. Agent.run() 生成输出
2. BaseAgent.reflect_and_refine() 自动调用
   → _self_review() — LLM 5维质量审查
     (完整性/正确性/一致性/具体性/可操作性)
   → 如果 quality=fail:
     → _refine() — 注入feedback让LLM重新生成
     → 最多2轮迭代
   → 如果 quality=pass: 返回
```

#### ArtifactRegistry集成

```python
def _save_artifact(state, phase, filename, content) -> str:
    # 优先: ArtifactRegistry.register_from_state()
    # 回退: FileTool.write_text() 直接写文件
```

#### 不足

1. **self_review增加LLM调用**: 每轮额外2次LLM调用(审查+优化),增加延迟和成本
2. **自反射不验证代码可运行性**: 只检查JSON结构的完整性/一致性,不运行代码
3. **_refine的prompt不含原始需求**: 只基于feedback优化,可能偏离原始意图

### 4.2-4.9 八个专业Agent

| Agent | 文件 | 行数 (估) | 输入 | 输出到AgentState | 核心能力 |
|-------|------|----------|------|-----------------|---------|
| **PlannerAgent** | planner_agent.py | ~100 | task_description | input_manifest (DAG) | 分解任务为有序阶段+依赖+复杂度 |
| **RequirementAgent** | requirement_agent.py | ~200 | input.md内容 | requirements (JSON) | 5步分析: 领域模型→FR→NFR→用例→风险 |
| **DesignAgent** | design_agent.py | ~250 | requirements JSON | design_artifacts (JSON) | C4架构+Mermaid图表(类图/ER图/时序图)+API契约 |
| **CodeAgent** | code_agent.py | ~300 | design_artifacts | code_files (list) | 工程级Python项目(类型标注/docstring/脚手架/配置) |
| **TestAgent** | test_agent.py | ~200 | code_files | test_files + test_results | pytest套件+fixtures+参数化+边角用例 |
| **DebugAgent** | debug_agent.py | ~200 | test_results + code | debug_analysis (JSON) | 5步根因分析+10种缺陷分类+修复假说 |
| **RepairAgent** | repair_agent.py | ~150 | debug_analysis | repair_patch (diff) | 最小变更修复+回归测试验证 |
| **ReviewAgent** | review_agent.py | ~150 | 全阶段产物 | final_report | 质量仪表盘+分阶段指标+执行时间线 |

#### 不足(Agent层通用)

1. **Prompt硬编码在类中**: 修改prompt需要修改Python源码,不支持配置化
2. **JSON输出依赖模型能力**: DeepSeek等较弱模型可能输出非标准JSON
3. **无增量执行**: 每次都完整重新生成,不支持只修改某个阶段的产物
4. **Agent间依赖传递通过AgentState**: 上游Agent的输出格式变化可能导致下游Agent失败

---

## 5. 模块详解: agent_core/ — 基础设施层

### 5.1 config_loader.py

YAML配置文件加载。支持 `deepseek` / `openai` / `huawei` 三种provider。

### 5.2 llm_client.py

LLM客户端封装,提供:
- `chat()`: 文本对话
- `chat_structured()`: 结构化JSON输出

### 5.3 state.py — AgentState

```python
@dataclass
class AgentState:
    task_id, task_type, input_path, output_root
    requirements, design_artifacts, code_files, test_files
    test_results, debug_analysis, repair_patch
    retry_count, max_retry, status, execution_trace
    errors, warnings
    artifact_registry  # 可选: 中央产物仓库引用
```

### 5.4 workflow.py — WorkflowController

V1的编排器,管理8个Agent的顺序执行和质量门:

```python
class WorkflowController:
    # 初始化所有Agent
    # execute(spec: TaskSpec) → AgentState
    #   planner → requirement → design → code → test → debug/repair → review
    # 每个阶段后调用 _check_quality_gate()
```

#### 质量门实现

```python
def _check_quality_gate(phase, output) -> PhaseResult:
    # LLM评估: pass/fail + feedback
    # fail → retry (max_retry次)
    # 超过retry → 标记error继续
```

### 5.5 langgraph_workflow.py

基于LangGraph的工作流替代实现,使用Graph State管理。

### 5.6 router.py

任务路由器,根据task_type决定使用WorkflowController还是DevAgentCore。

### 5.7 schemas.py

数据模型定义: TaskSpec, Artifact, PhaseConfig 等。

---

## 6. 模块详解: tools/ — 工具集

### 6.1 ArtifactRegistry — `artifact_registry.py`

**用途**: 中央产物仓库,统一管理所有Agent生成的产物。

#### 核心方法

```python
class ArtifactRegistry:
    def register(artifact: Artifact) -> dict
        # 写入artifact文件 + 更新index.json
        # 包含: id, type, format, content, metadata, checksum(SHA256), size, mime_type

    def register_from_state(state, phase, artifact) -> dict
        # 自动确定存储路径: {output_root}/{phase_dir}/{artifact.filename}

    def get_index() -> dict  # 读取完整索引
    def find_by_type(type) -> list  # 按类型查找
    def find_by_checksum(checksum) -> list  # 按SHA256查找(去重)
```

#### 原子索引写入

```python
# 使用临时文件+重命名防止并发损坏:
tmp_path = index_path + ".tmp"
write(tmp_path, new_index)
os.replace(tmp_path, index_path)  # 原子操作
```

#### 不足

1. **单文件index.json**: 大项目可能index过大,需要分片或数据库
2. **无生命周期管理**: artifact只增不删,磁盘持续增长
3. **无远端后端**: 仅本地文件系统,不支持S3等对象存储
4. **mime_type基于扩展名**: 不使用python-magic进行内容检测

---

### 6.2 其他工具模块

| 模块 | 行数 | 用途 |
|------|------|------|
| **file_tool.py** | ~150 | 文件读写(文本/二进制/JSON),目录操作 |
| **quality.py** | ~100 | 质量检查流程封装: 格式化(black) + 静态分析(ruff/mypy) + 测试执行 |
| **patch_tool.py** | ~80 | unified diff生成和应用 |
| **test_runner.py** | ~100 | pytest封装,结果解析(passed/failed/errors) |
| **sandbox_runner.py** | ~80 | Docker容器内执行隔离 |
| **static_analyzer.py** | ~100 | AST/类型检查封装 |
| **diagram_validator.py** | ~60 | Mermaid图表语法验证 |

---

## 7. 模块详解: api/ + cli/ — 入口层

### 7.1 FastAPI Server — `api/app.py` (910行)

#### API端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger文档 |
| GET | `/dashboard` | Web仪表盘 |
| POST | `/api/v2/tasks/{mode}` | 提交任务(后台执行) |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态 |
| GET | `/api/v2/tasks/{task_id}/review/pending` | 查询待审核项 |
| POST | `/api/v2/tasks/{task_id}/review/respond` | 审核响应 |
| POST | `/api/v2/tasks/{task_id}/command` | 控制指令(pause/resume/abort) |
| WS | `/api/v2/tasks/{task_id}/interactive` | WebSocket双向通信 |

#### WebSocket事件类型

| 事件 | 方向 | 说明 |
|------|------|------|
| `review.requested` | Server→Client | 阶段审核请求 |
| `review.resolved` | Server→Client | 审核已处理 |
| `agent.question` | Server→Client | Agent向用户提问 |
| `approval.requested` | Server→Client | 危险操作审批 |
| `progress.snapshot` | Server→Client | 进度快照 |
| `control.paused/resumed/aborted` | Server→Client | 控制状态变化 |
| `artifact.created` | Server→Client | 新产物创建通知 |
| `test.result` | Server→Client | 测试结果通知 |
| `review.response` | Client→Server | 审核决策 |
| `command.*` | Client→Server | 控制指令 |

#### 仪表盘HTML

内置一个HTML仪表盘 (`/dashboard`),显示:
- 当前运行任务列表
- 任务状态(progress bar)
- 最近事件流
- 产物文件列表

#### 不足

1. **单进程架构**: 所有任务在同一进程中,无水平扩展
2. **WebSocket无认证**: 任何连接都能控制任务
3. **仪表盘轮询刷新**: 非实时推送,使用定时器轮询API
4. **后台任务用asyncio.create_task**: 无任务队列,任务丢失不可恢复

---

### 7.2 CLI — `cli/main.py` (276行)

#### 命令

```bash
agent --mode full --input requirements.md          # 全流程Pipeline
agent --mode design --input requirements.md        # 仅分析+设计
agent --mode implement --input requirements.md     # 分析→设计→编码→测试
agent --mode repair --workspace ./src/             # Bug修复(ReAct)
agent --mode agentic --workspace ./my_project/     # 自主模式(ReAct)
agent --mode test --workspace ./src/               # 测试生成+执行
agent --mode debug --workspace ./src/              # 调试分析
```

#### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mode, -m` | 任务模式 | agentic |
| `--input, -i` | 输入文件(需求文档) | — |
| `--workspace, -w` | 工作空间目录 | `.` |
| `--output, -o` | 产物输出目录 | `./outputs` |
| `--interactive, -I` | 交互模式(full/approval/observe/off) | off |
| `--provider` | LLM提供商(openai/deepseek) | 配置文件 |
| `--verbose, -v` | 详细输出 | — |
| `--config` | 配置文件路径 | — |

#### 模式路由逻辑

```python
if mode in ("full", "design", "implement", "test"):
    # → PipelineRunner (Plan-Execute-Gate)
elif mode in ("agentic", "repair", "debug"):
    # → DevAgentCore (ReAct Loop)
```

#### 不足

1. **CLI不支持dry-run**: 无法预览将要执行的操作
2. **错误输出到stderr简单**: 只有print,无结构化日志
3. **无配置文件校验**: 配置项缺失时在运行时崩溃而非启动时报错

---

## 8. 模块详解: IDE扩展

### 8.1 VSCode Extension

| 特性 | 说明 |
|------|------|
| **右键菜单** | 全流程、分析设计、代码生成、Bug修复、审核审批、暂停恢复 |
| **22个命令** | 覆盖所有CLI模式 |
| **WebSocket连接** | 实时接收审核/进度事件 |
| **审核弹窗** | 右下角通知+输入框反馈 |
| **状态轮询** | REST API定时查询任务状态 |

### 8.2 IntelliJ Plugin

Gradle项目,提供类似VSCode扩展的操作菜单。

### 8.3 Eclipse Plugin

基于plugin.xml声明式扩展。

---

## 9. 完整交互链路详解

### 9.1 阶段审核完整流程 (14步)

```
1.  Agent调用 request_review(phase, title, summary, files_changed, self_assessment)
2.  文件存在性验证: os.path.exists(每个files_changed项)
3.  顺序锁检查: phase in _approved_phases → 已批准,返回next_tool
4.  速率限制: _global_review_count++ > 3 → 强制submit
5.  修订上限: _revise_counts[phase] >= 3 → 自动批准
6.  构建ReviewArtifact: ArtifactBuilder.from_code_files / from_test_results
7.  调用 PhaseReviewGate.submit_for_review()
8.  QualityEvaluator.evaluate() → LLM 10维评分
9.  ReviewFormatter.format() → Markdown审核文档
10. 推送review.requested事件:
    优先: ThreadChannel → WS客户端弹窗
    备选: TerminalChannel → CLI交互
    兜底: 自动批准
11. ReviewSession.wait_for_decision() → threading.Event阻塞Agent线程
12. 人工通过 WS/CLI/REST 选择: approve / revise (+feedback) / reject
13. ThreadChannel.resolve() → Event.set() 解除阻塞
14. 返回决策给Agent:
    approve → 设置_forced_next_tool指向下一阶段工具 → 跳过LLM直接执行
    revise → 设置_forced_next_tool指向重做工具 + feedback注入prompt
    reject → 标记FAILED终止
```

### 9.2 危险操作审批流程

```
1. Agent产生一个shell_run/file_edit操作
2. InteractionController._check_approval_gate() 分类:
   → 匹配DESTRUCTIVE_PATTERNS → DESTRUCTIVE_SHELL
   → old_string > 50行 → LARGE_EDIT
   → 无匹配 → 跳过审批
3. 创建ApprovalRequest,调用wait_for_approval()
4. 通道优先级: ThreadChannel → WS → Terminal → Auto
5. 返回: approve / deny / approve_all
```

### 9.3 Agent→用户提问流程

```
1. Agent调用 ask_user(question, options, default_choice)
2. AskUserTool.execute() 检查 _get_active_controller()
3. 非交互模式 → 自动返回default_choice
4. 交互模式 → InteractionController.ask_user()
5. 通道路由 → 用户回答 → 返回给Agent
```

---

## 10. v3.3 新增功能

### 10.1 交互修改（Interactive Revision）— Phase 7

**用途**: Pipeline 交付完成后，用户可通过 WebSocket 实时对项目提出修改意见，DevAgent 自动执行修改、测试、提交，形成无限轮的反馈修改循环。

**核心方法**: `PipelineRunner._run_interactive_revision()` (~120行)

**工作流程**:
```
Pipeline 六阶段完成
  → Phase 7: 展示项目摘要 → 等待人工反馈
  → 用户输入修改意见 → DevAgent ReAct 循环执行修改
  → 展示结果 → 继续等待反馈
  → 用户输入 "done"/"完成" → Pipeline 完成
```

**11 个完成信号**: `done`, `完成`, `approve`, `好的`, `ok`, `可以`, `满意`, `通过`, `结束`, `没问题`, `没有问题`

**技术实现**:
- 通过 `ThreadChannel.create_feedback()` 创建跨线程反馈请求
- `DevAgentCore.execute()` 在 agentic 模式下执行修改
- `max_iterations=30` 控制单次修改范围
- `feedback_mode=True` 标志传给 WebSocket 客户端
- 30 分钟超时自动完成

### 10.2 Bug修复循环 — Phase 5

**用途**: 当测试阶段发现失败时，自动进入 debug→repair→retest 循环。

**触发条件**:
- 测试 `failed > 0`
- 或前一个测试阶段的 `review_decision == "revise"` 且反馈包含 "fix"

**循环机制**: `on_complete="testing"` → 修复后自动回到 Phase 4 重新验证，最多 2 个循环后强制推进到交付阶段。

### 10.3 Prompt 工程质量提升

v3.3 新增 **5 个共享 Prompt 段**，注入到 `BASE_SYSTEM_PROMPT` 和各 Agent prompt:

| Block | 行数 | 内容 |
|------|------|------|
| `SECURITY_STANDARDS_BLOCK` | 30行 | OWASP Top 10、注入防护、密钥管理、14条禁止模式 |
| `OBSERVABILITY_STANDARDS_BLOCK` | 15行 | 结构化日志、`/health`、Prometheus 指标、链路追踪 |
| `CITATION_REQUIREMENTS_BLOCK` | 20行 | FR/NFR/UC 编号引证、追溯矩阵、文档控制表 |
| `ANTI_PATTERN_BLOCK` | 25行 | God Class、Long Method、Magic Numbers、20种反模式 |
| `MEASURABLE_QUALITY_CHECKLIST` | 20行 | 代码/测试/设计/需求的自动化验证清单 |

**Agent Prompt 升级**:
- `CODE_PROMPT`: +安全规则(S1-S10)、+可观测性规则(O1-O5)、+SOLID原则、+设计模式、+质量清单
- `DESIGN_PROMPT`: +DFD Level 0/1、+状态机图、+部署图、+ADR模板、+STRIDE威胁模型
- `REQUIREMENT_PROMPT`: +安全NFR(OWASP对齐)、+可观测性需求、+术语表(glossary)
- `TEST_PROMPT`: +安全测试(injection/auth/secret)、+需求引证[FR-XX]、+基于需求的测试生成

**报告升级**:
- `ReviewAgent._generate_executive_report()`: +文档控制表、+Executive Dashboard（20+指标）、+需求追溯矩阵(RTM)

---

## 11. 已知不足与改进方向

### 10.1 架构层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 1 | V1/V2双架构并存 | 代码维护成本高,V1 Agent和V2 Pipeline工具功能重叠 | 将V1 Agent逐步重构为纯LLM prompt,去掉中间层 |
| 2 | 单进程无状态设计 | 服务重启丢失所有任务状态和审核历史 | 引入持久化存储(Redis/PostgreSQL) |
| 3 | AgentState全内存 | 长时间任务内存占用持续增长 | 引入增量checkpoint和history truncation |
| 4 | LLM调用无重试 | 临时网络错误导致任务失败 | 添加指数退避重试逻辑 |

### 10.2 执行引擎层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 5 | Pipeline阶段hardcoded | 不同项目的阶段和工具不同,无法灵活配置 | 支持用户自定义phases配置(JSON/YAML) |
| 6 | ReAct模式JSON解析脆弱 | 非标准LLM输出格式导致解析失败 | 使用structured output / tool calling替代正则解析 |
| 7 | 强制工具注入参数不完整 | review后的工具可能拿到错误的输入 | 从state中自动提取前一阶段的完整输出 |
| 8 | 终止条件不完善 | "stuck"检测可能误判正在阅读文档的Agent | 增加stuck检测的adaptive窗口 |

### 10.3 工具层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 9 | web_search依赖DDG | DDG改版可能导致搜索失效 | 接入Google/Bing Search API |
| 10 | file_edit无语法树感知 | 简单的字符串替换可能破坏缩进/括号 | 使用tree-sitter进行结构化编辑 |
| 11 | test_run的SmartRegressionSelector是best-effort | 测试选择不准确,多跑或少跑测试 | 使用覆盖率数据精确关联测试和源码 |
| 12 | lint_check的ruff总是SKIPPED | 无法反馈代码风格问题 | 异步执行ruff,非阻塞地添加到结果 |

### 10.4 上下文管理层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 13 | RepoMap签名解析仅顶层 | 嵌套类和闭包不被识别 | 使用全树遍历,标记深度 |
| 14 | Token预算估算粗糙 | 中文token消耗被低估 | 使用tiktoken进行精确计数 |
| 15 | 无跨文件语义理解 | 上下文不包含调用链/数据流 | 引入Language Server的引用/定义跳转 |

### 10.5 交互审核层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 16 | 审核历史无持久化 | 服务重启审核状态丢失 | 审核状态写入数据库 |
| 17 | 单审核阻塞 | 不能同时审核多个阶段 | 支持并行审核pipeline |
| 18 | 非交互模式为auto-approve | 无人工监督的质量风险 | 添加审核结果通知(邮件/Webhook) |
| 19 | TerminalChannel依赖isatty() | IDE集成终端可能返回False | 使用环境变量或命令行flag强制开启 |

### 10.6 测试与质量层面

| # | 问题 | 影响 | 建议改进 |
|---|------|------|---------|
| 20 | 无集成测试覆盖完整pipeline | 各模块单独测试OK但组合可能失败 | 添加E2E测试:从input到output全流程 |
| 21 | 无LLM输出质量回归测试 | prompt修改可能导致输出质量下降 | 建立golden test set + 自动化对比 |
| 22 | 性能测试缺失 | 不知道pipeline在大型项目上的表现 | 添加SWE-bench评测和性能benchmark |

---

> **版本**: 3.3 | **更新**: 2026-06-04 | **测试**: 288 passed | **总代码**: ~25,320 行 | **新增**: 交互修改 + Prompt工程质量提升
