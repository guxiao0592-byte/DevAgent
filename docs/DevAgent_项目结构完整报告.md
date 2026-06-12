# DevAgent 项目结构完整报告

> 版本：1.0 | 日期：2026-06-12 | 覆盖：后端内核、CLI、API 服务、Web 前端、VSCode 插件

---

## 目录

1. [总体结论](#1-总体结论)
2. [项目总览](#2-项目总览)
3. [统一内核架构](#3-统一内核架构)
4. [模块详解](#4-模块详解)
5. [前端视图与功能映射](#5-前端视图与功能映射)
6. [API 端点完整清单](#6-api-端点完整清单)
7. [三条通道对比](#7-三条通道对比)
8. [数据流图](#8-数据流图)
9. [代码量统计](#9-代码量统计)
10. [启动与部署](#10-启动与部署)

---

## 1. 总体结论

### ✅ 前端已完全接入 DevAgent 内核

CLI、VSCode 插件、Web 前端**三者共用同一个 `DevAgentCore` 引擎**，不存在多条代码路径。前端通过 36 个 REST API + WebSocket 端点，覆盖了 DevAgent 的全部 7 种任务模式和 6 个流水线阶段。

---

## 2. 项目总览

### 项目定位

DevAgent 是一个**基于大语言模型的统一软件工程智能体平台**，实现需求→设计→编码→测试→修复的端到端自动化工作流。支持三种使用方式：

| 通道 | 适用场景 | 入口 |
|------|---------|------|
| **命令行 (CLI)** | 快速单次任务、CI/CD 自动化 | `agent --mode full --input requirements.md` |
| **Web 前端** | 全功能可视化操作、团队协作 | 浏览器 http://127.0.0.1:8911/app/ |
| **VSCode 插件** | IDE 内集成开发、实时弹窗审核 | 右键菜单 → DevAgent |

### 项目根目录结构

```
DevAgent/
├── devagent/              ← Python 后端内核（45 文件，~14,000 行）
├── frontend/              ← Web 前端（14 文件，~5,000 行）
├── vscode_extension/      ← VS Code 插件
├── intellij_plugin/       ← IntelliJ IDEA 插件
├── eclipse_plugin/        ← Eclipse 插件
├── tests/                 ← 288 单元测试
├── docs/                  ← 设计文档
├── Dockerfile             ← 容器化
├── docker-compose.yml     ← 编排部署
├── requirements.txt
├── setup.py
└── pyproject.toml
```

---

## 3. 统一内核架构

```
┌─────────────────────────────────────────────────────────────┐
│                     DevAgentCore                            │
│             (devagent/agentic/core.py)                      │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Think → Act → Observe                  │    │
│  │                 (ReAct 循环)                         │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  execute()              run_pipeline()       execute_async()│
│  ReAct 自主决策         确定性 Plan-Execute   异步模式      │
│                          -Gate 流水线                       │
└──────────┬─────────────────────┬──────────────────┬────────┘
           │                     │                  │
    ┌──────┴────────┐  ┌─────────┴────────┐  ┌─────┴─────────┐
    │  CLI 入口      │  │  API Server     │  │  VSCode 插件   │
    │  cli/main.py   │  │  api/app.py     │  │  HTTP/WS 调用  │
    │  直接 import    │  │  FastAPI 包装   │  │  API Server    │
    └───────────────┘  └────────┬─────────┘  └───────────────┘
                                │
                         ┌──────┴─────────┐
                         │   Web 前端      │
                         │   frontend/    │
                         │   Fetch + WS   │
                         └────────────────┘
```

### 内核核心能力

| 模块 | 文件 | 功能 |
|------|------|------|
| **执行引擎** | `agentic/core.py` | ReAct 循环、PipelineRunner 集成、终止条件检测 |
| **工具系统** | `agentic/tools.py` | 22 种基础工具（文件、搜索、执行、版本、信息） |
| **Pipeline** | `agentic/pipeline_tools.py` | 8 种专业工具（需求→设计→编码→测试→修复→报告） |
| **上下文管理** | `agentic/context.py` | 四层上下文缓存、RepoMap、幻觉检测 |
| **交互控制** | `agentic/interaction.py` | 审批门、对话管理、进度推送 |
| **阶段审核** | `agentic/review_gate.py` | LLM 10 维质量评分 + 人工终审 |
| **故障定位** | `agentic/fault_locator.py` | 三层混合：SBFL + 静态分析 + LLM 融合 |
| **验证** | `agentic/verification.py` | L1-L4 验证（语法→单元测试→符号执行→形式化） |
| **沙箱** | `agentic/sandbox.py` | Docker/Podman/Local 三级安全执行 |
| **经验学习** | `agentic/experience.py` | 跨任务 bug→fix 模式复用 |
| **多 Agent** | `agentic/multi_agent.py` | 并行 Worker 调度、冲突解决 |
| **多模态** | `agentic/multimodal.py` | 截图分析、图表识别 |
| **可观测性** | `agentic/observability.py` | 事件总线、SSE 推送、执行回放、任务历史 |

---

## 4. 模块详解

### 4.1 后端核心包结构

```
devagent/
│
├── agentic/                          ← V2 核心引擎（52% 代码量）
│   ├── core.py                       → DevAgentCore 主循环
│   ├── tools.py                      → 22 种基础工具实现
│   ├── pipeline_tools.py             → 8 种流水线工具（V1 Agent 适配器）
│   ├── pipeline_runner.py            → Plan-Execute-Gate 流水线
│   ├── pipeline_validator.py         → 确定性验证器
│   ├── context.py                    → 智能上下文管理
│   ├── interaction.py                → 交互控制器
│   ├── review_gate.py                → 阶段审核门
│   ├── session.py                    → 多客户端会话管理
│   ├── thread_channel.py             → 跨线程通信
│   ├── fault_locator.py              → 三层故障定位
│   ├── verification.py               → 形式化验证
│   ├── validation.py                 → 即时验证
│   ├── planning.py                   → 任务规划与分解
│   ├── experience.py                 → 经验存储与检索
│   ├── observability.py              → 可观测性系统
│   ├── sandbox.py                    → 容器沙箱
│   ├── state_graph.py                → LangGraph 兼容状态图
│   ├── multi_agent.py                → 多 Agent 协作
│   ├── multimodal.py                 → 多模态分析
│   ├── state.py                      → AgentLoopState
│   └── events.py                     → 事件总线
│
├── agent_core/                       ← V1 基础设施
│   ├── llm_client.py                 → LLM 客户端（DeepSeek + OpenAI）
│   ├── workflow.py                   → V1 WorkflowController
│   ├── langgraph_workflow.py         → LangGraph 工作流
│   ├── config_loader.py             → YAML 配置加载
│   ├── router.py                     → 任务路由器
│   ├── state.py                      → AgentState
│   └── schemas.py                    → 数据模型（TaskSpec 等）
│
├── agents/                           ← V1 专业 Agent（被 pipeline_tools 适配）
│   ├── requirement_agent.py          → 需求分析（5 步多轮分析）
│   ├── design_agent.py              → 架构设计（C4 模型 + Mermaid 图表）
│   ├── code_agent.py                → 代码生成（类型标注/docstring/脚手架）
│   ├── test_agent.py                → 测试生成与执行
│   ├── debug_agent.py               → 调试分析（5 步根因分析 + 10 种缺陷分类）
│   ├── repair_agent.py              → Bug 修复（最小变更 + 回归验证）
│   ├── review_agent.py              → 质量评估与报告
│   └── planner_agent.py             → 任务分解为工作分解结构
│
├── api/                              ← 服务入口
│   ├── app.py                        → FastAPI + WebSocket（36 个端点）
│   └── ide_server.py                 → IDE 集成服务器（API + LSP）
│
├── cli/                              ← 命令行入口
│   └── main.py                       → argparse CLI，7 种模式
│
├── reporting/                        ← 文档 + 图表生成
│   ├── ieee830.py                    → IEEE 830 SRS 需求规格说明书渲染
│   ├── ieee1016.py                   → IEEE 1016 SDD 软件设计说明书渲染
│   ├── diagrams.py                   → Mermaid 图表生成器（9 种图表）
│   ├── renderer.py                   → Kroki.io 渲染引擎（PNG/SVG）
│   ├── executive.py                  → 执行摘要报告
│   └── templates.py                  → 文档模板片段
│
├── tools/                            ← 工具集
│   ├── artifact_registry.py          → 中央产物仓库
│   ├── diagram_validator.py          → Mermaid 语法验证
│   ├── file_tool.py                  → 文件操作
│   ├── patch_tool.py                 → 补丁生成
│   ├── quality.py                    → 质量检查
│   ├── sandbox_runner.py             → 沙箱运行
│   ├── static_analyzer.py            → 静态代码分析
│   └── test_runner.py                → 测试执行器
│
├── lsp/                              ← LSP 语言服务器
│   └── server.py
│
└── benchmarks/                       ← 基准测试
    ├── benchmark_runner.py
    ├── pipeline_evaluator.py
    ├── swebench_adapter.py
    └── dr_cases/                     → 4 种缺陷测试用例
```

### 4.2 前端结构

```
frontend/
├── index.html                        ← SPA 单页面入口（179 行）
├── css/
│   └── devagent.css                  ← 深色科技风主题（1,103 行）
└── js/
    ├── app.js                        ← 应用核心：路由、状态管理、事件（252 行）
    ├── api.js                        ← REST + WebSocket 客户端（385 行）
    ├── diagrams.js                   ← 三级渲染引擎（476 行）
    ├── utils.js                      ← 工具函数库（282 行）
    └── components/
        ├── dashboard.js              ← 仪表盘（170 行）
        ├── task-creator.js           ← 任务创建含文件上传（314 行）
        ├── pipeline.js               ← 流水线进度含下载（324 行）
        ├── requirements.js           ← 需求分析文档+图表（218 行）
        ├── design.js                 ← 总体+详细设计（285 行）
        ├── implementation.js         ← 代码实现+测试（272 行）
        ├── debug-repair.js           ← 调试修复文档（150 行）
        ├── reports.js                ← 报告+产物下载（200 行）
        └── interaction.js            ← WebSocket 实时交互（372 行）
```

### 4.3 图表渲染引擎（三级降级策略）

| 优先级 | 策略 | 说明 |
|--------|------|------|
| **1st** | 浏览器 Mermaid.js | 本地渲染，零网络依赖，毫秒级 |
| **2nd** | 服务端 Kroki.io | HTTP 调用，8s 超时，更宽容解析器 |
| **3rd** | 展示原始代码 | 两次都失败后显示 Mermaid 源码 + 错误信息 |

自动修复 7 类常见 LLM 生成语法错误：
1. 中文/智能引号 → ASCII
2. 不可见控制字符移除
3. `classDiagram` 类名空格 → 反引号包裹
4. 节点 ID 含 `.` `/` 替换为 `_`
5. 节点标签未加引号自动添加
6. `erDiagram` 实体名空格替换
7. `sequenceDiagram` 参与者特殊字符处理

---

## 5. 前端视图与功能映射

### 视图覆盖全流程（10 个页面）

| 前端视图 | 核心功能 | 数据来源（API） |
|---------|---------|----------------|
| **仪表盘** | 实时指标卡片、任务历史、系统状态、快速启动 | `GET /tasks/history`, `GET /dashboard/metrics` |
| **创建任务** | 7 种模式选择、文件拖拽上传、快捷模板 | `POST /upload`, `POST /tasks/{mode}` |
| **流水线执行** | 6 阶段进度条（从文件系统扫描）、自动 3s 轮询、分阶段下载 | `GET /tasks/{id}/phases`, `GET /tasks/{id}` |
| **需求分析** | IEEE 830 SRS 文档渲染 + 用例图/DFD Lv0/1/活动图 | `GET /document/requirements`, `GET /diagrams/requirements` |
| **总体设计** | IEEE 1016 SDD 文档 + 架构图/组件图/部署图/技术栈 | `GET /document/design`, `GET /diagrams/design` |
| **代码实现** | 文件树浏览器、代码预览 | `GET /phases`, `GET /document/implementation` |
| **测试执行** | 测试指标卡片、阶段文档 | `GET /tasks/{id}`, `GET /document/tests` |
| **调试修复** | 修复文档、SBFL 分析图表 | `GET /document/repair`, `GET /diagrams/repair` |
| **实时交互** | WebSocket 事件时间线、审核弹窗、命令控制台 | `WS /interactive`, `GET /review/pending`, `POST /command` |
| **报告文档** | 最终执行报告、完整/6 阶段 ZIP 下载、产物清单 | `GET /document/reports`, `GET /download`, `GET /download/{phase}` |

### 阶段下载

| 阶段 | 下载内容 | 典型文件 |
|------|---------|---------|
| `requirements` | 需求分析产物 | IEEE 830 SRS, 用例图, DFD, 活动图, 领域模型 |
| `design` | 架构设计产物 | IEEE 1016 SDD, 类图, 时序图, ER 图, 状态机图 |
| `implementation` | 源代码 | Python 文件, 项目脚手架, README |
| `tests` | 测试套件 | pytest 文件, 覆盖率报告, lint 结果 |
| `repair` | 修复产物 | 故障定位报告, 修复补丁 diff |
| `reports` | 最终报告 | 执行摘要, 质量仪表盘, 产物清单 |

---

## 6. API 端点完整清单

### 任务管理（7 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks` | 通用任务创建（JSON body） |
| `POST` | `/api/v1/tasks/design` | 分析+设计模式 |
| `POST` | `/api/v1/tasks/implement` | 实现模式 |
| `POST` | `/api/v1/tasks/repair` | 修复模式 |
| `POST` | `/api/v1/tasks/full` | 全流程模式 |
| `POST` | `/api/v1/tasks/agentic` | 自主模式（V1 同步） |
| `POST` | `/api/v2/tasks/agentic` | 自主模式（V2 异步，后台运行） |

### 任务状态与历史（3 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/v1/tasks/{id}` | 任务详情（含 phase_index, phases_completed） |
| `GET` | `/api/v1/tasks/history` | 最近 100 条任务历史 |
| `GET` | `/api/v2/tasks/{id}/status` | V2 交互状态（暂停/中止/客户端列表） |

### 实时交互（6 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `WS` | `/api/v2/tasks/{id}/interactive` | 双向 WebSocket（事件推送 + 命令/审核响应） |
| `WS` | `/api/v1/tasks/{id}/stream` | V1 兼容流（转发到 interactive） |
| `GET` | `/api/v2/tasks/{id}/review/pending` | 当前待审核项 |
| `POST` | `/api/v2/tasks/{id}/review/respond` | 审核响应（approve/revise/reject） |
| `GET` | `/api/v2/tasks/{id}/review/history` | 审核历史 |
| `GET` | `/api/v2/tasks/{id}/pending-approval` | 当前待审批项 |
| `POST` | `/api/v2/tasks/{id}/approve` | 审批响应（approve/deny） |
| `POST` | `/api/v2/tasks/{id}/command` | 控制命令（pause/resume/abort/retry/redirect/inject） |

### 文档与图表（4 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/v1/tasks/{id}/document/{phase}` | 获取阶段 IEEE 文档（Markdown） |
| `GET` | `/api/v1/tasks/{id}/diagrams/{phase}` | 获取阶段所有图表代码（Mermaid/PUML） |
| `GET` | `/api/v1/tasks/{id}/structured/{phase}` | 获取阶段结构化 JSON 数据 |
| `POST` | `/api/v1/diagrams/render` | 渲染图表为 SVG（Kroki 服务端） |

### 文件上传与下载（5 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/v1/upload` | 上传需求文件（支持 .md .txt .py .json .yaml） |
| `GET` | `/api/v1/tasks/{id}/download` | 下载完整项目 ZIP |
| `GET` | `/api/v1/tasks/{id}/download/{phase}` | 下载指定阶段 ZIP |
| `GET` | `/api/v1/tasks/{id}/phases` | 列出所有阶段及文件数和大小 |
| `GET` | `/api/v1/project/structure` | 项目目录结构树 |

### 仪表盘与分析（5 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/` | 服务信息 + 前端入口链接 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/dashboard` | 内嵌仪表盘 HTML |
| `GET` | `/api/v1/dashboard/metrics` | 仪表盘指标（总数/成功率/活跃/平均迭代） |
| `GET` | `/api/v1/dashboard/trend` | 趋势数据（success_rate 等） |
| `POST` | `/api/v1/analyze/file` | 单文件 LLM 分析 |

---

## 7. 三条通道对比

### 7.1 命令行（CLI）

**启动方式**：
```bash
agent --mode full --input requirements.md --output ./outputs
agent --mode design --input requirements.md
agent --mode repair --workspace ./buggy_app/
agent --mode agentic --input task.md --interactive full
```

**核心文件**：`devagent/cli/main.py`（278 行）

**执行流程**：
```
args → MODE_INSTRUCTIONS → task_description
     → DevAgentCore
         → (full/design/implement) → run_pipeline()
         → (agentic/repair/test/debug) → execute()
     → _print_summary()
```

### 7.2 API 服务

**启动方式**：
```bash
devagent-api
# 或
uvicorn devagent.api.app:app --host 127.0.0.1 --port 8911
```

**核心文件**：`devagent/api/app.py`（~1,500 行）

**关键设计**：
- 同步模式走 `create_task()` → `WorkflowController`（V1）
- 异步模式走 `_run_agent()` 后台线程 → `DevAgentCore`（V2）
- 前端通过 REST + WebSocket 调用

### 7.3 Web 前端

**启动方式**：API 服务自动挂载，访问 `http://127.0.0.1:8911/app/`

**核心文件**：`frontend/js/app.js`（252 行 SPA 框架）

**依赖**：
- Mermaid.js CDN（图表本地渲染）
- 无其他第三方 JS 库
- 纯静态文件，无构建步骤

### 7.4 VSCode 插件

**核心文件**：`vscode_extension/src/extension.js`、`package.json`（423 行，22 个命令）

**22 个命令覆盖**：
- 任务：analyzeRequirement, generateCode, repairBug, fullPipeline, runAgentic, runTests, debugCode, runWithConfig
- 审核：approveReview, reviseReview, rejectReview
- 控制：pauseAgent, resumeAgent, abortAgent
- 服务：startApiServer, stopApiServer, startLsp, stopLsp
- 工具：analyzeCurrentFile, showTaskHistory, openOutputDir, clearDiagnostics

**16 个快捷键**：`Ctrl+Alt+D` + A/G/R/F/C/P/O 等

### 7.5 功能覆盖完整对比

| 功能 | CLI | 前端 | VSCode 插件 |
|------|-----|------|------------|
| 全流程开发 | ✅ `--mode full` | ✅ 任务创建 + 流水线视图 | ✅ fullPipeline |
| 分步执行 | ✅ 各 mode | ✅ 分阶段视图 + 下载 | ✅ 各命令 |
| 实时审核 | ✅ `--interactive` | ✅ WS 弹窗 + 事件时间线 | ✅ 弹窗审核 |
| 文件上传 | ❌ 仅本地路径 | ✅ 拖拽/点击上传 | ❌ 当前文件 |
| 文档查看 | ❌ 终端文本 | ✅ IEEE 830/1016 渲染 | ❌ 外部浏览器 |
| 图表展示 | ❌ | ✅ 三级渲染引擎 | ❌ |
| ZIP 下载 | ❌ 手动打包 | ✅ 完整 + 分阶段 | ❌ |
| 任务历史 | ❌ | ✅ 仪表盘 + 搜索 | ✅ 侧栏视图 |
| 进度可视化 | ❌ 终端文本 | ✅ 6 阶段进度条 | ❌ |
| LSP 诊断 | ❌ | ❌ | ✅ 行内诊断 |

---

## 8. 数据流图

### 全流程数据流

```
用户输入（文字/文件上传）
        │
        ▼
  ┌─────────────┐
  │  任务创建器   │  frontend/js/components/task-creator.js
  │  + 7 种模式   │
  └──────┬──────┘
         │ POST /api/v2/tasks/agentic
         ▼
  ┌─────────────┐
  │  FastAPI     │  devagent/api/app.py
  │  _run_agent()│  后台线程
  └──────┬──────┘
         │
         ▼
  ┌─────────────────────────────────┐
  │         DevAgentCore            │  devagent/agentic/core.py
  │                                 │
  │  Router → PipelineRunner 或     │
  │           ReAct 自主循环         │
  └──────┬──────────────────────────┘
         │
         ├──→ RequirementAgent  → outputs/01_requirements/
         │    ├── requirement_specification.md  (IEEE 830 SRS)
         │    ├── structured_requirements.json
         │    └── *.puml (用例图, 活动图)
         │
         ├──→ DesignAgent       → outputs/02_design/
         │    ├── design_spec.md              (IEEE 1016 SDD)
         │    ├── class_diagram.mmd
         │    ├── er_diagram.mmd
         │    ├── sequence_diagram_*.mmd
         │    └── tech_stack.json
         │
         ├──→ CodeAgent         → outputs/03_implementation/
         │    ├── src/*.py
         │    └── pyproject.toml
         │
         ├──→ TestAgent         → outputs/04_tests/
         │    ├── test_*.py
         │    └── test_report.json
         │
         ├──→ RepairAgent       → outputs/05_repair/
         │    └── fix_patch.diff
         │
         └──→ ReviewAgent       → outputs/06_reports/
              └── executive_report.md
         │
         ▼
  ┌─────────────────────────────────┐
  │  前端读取                        │
  │  GET /document/{phase}          │  → 文档渲染
  │  GET /diagrams/{phase}          │  → 图表渲染（三级降级）
  │  GET /structured/{phase}        │  → 结构化数据展示
  │  GET /download/{phase}          │  → ZIP 下载
  └─────────────────────────────────┘
```

### 实时交互数据流

```
浏览器 WebSocket                        后台 Agent 线程
     │                                        │
     │  ws://.../interactive?mode=controller  │
     │────────────────────────────────────────>│
     │                                        │
     │  ← session.created                     │
     │  ← progress.snapshot                   │ 工具执行
     │  ← tool.completed                      │ 每步
     │  ← phase.completed                     │ 每阶段
     │  ← review.requested              ThreadChannel
     │  ← agent.question                .wait()
     │                                        │
     │  用户点击 [批准]                        │
     │  → review.response              ThreadChannel
     │  → (approve/revise/reject)      .resolve()
     │                                        │
     │  用户点击 [暂停]                        │
     │  → command.pause                InteractionController
     │                                  .pause()
     │                                        │
     │  ← task.completed ✅                   │
```

---

## 9. 代码量统计

| 层 | 模块 | 文件数 | 代码行 | 占比 |
|---|------|--------|--------|------|
| **V2 核心引擎** | agentic/ | 25 | ~9,500 | 44% |
| **V1 Agent** | agents/ + agent_core/ | 15 | ~3,500 | 16% |
| **文档报告** | reporting/ | 6 | ~1,800 | 8% |
| **工具集** | tools/ | 8 | ~1,200 | 6% |
| **API 服务** | api/ | 2 | ~1,500 | 7% |
| **CLI/LSP** | cli/ + lsp/ | 3 | ~500 | 2% |
| **前端** | frontend/ | 14 | ~5,000 | 23% |
| **VS Code 扩展** | vscode_extension/ | 3 | ~800 | 4% |
| **测试** | tests/ | 14 | ~1,800 | 8% |
| **配置/基准** | 其他 | 8 | ~500 | 2% |
| **总计** | | **98** | **~26,100** | 100% |

---

## 10. 启动与部署

### 开发环境启动

```bash
# 1. 进入项目
cd DevAgent

# 2. 激活虚拟环境
source venv/bin/activate

# 3. 启�� API 服务（含前端）
uvicorn devagent.api.app:app --host 127.0.0.1 --port 8911

# 4. 浏览器访问
open http://127.0.0.1:8911/app/
```

### CLI 使用

```bash
# 全流程
agent --mode full --input requirements.md

# 仅设计
agent --mode design --input requirements.md

# Bug 修复
agent --mode repair --workspace ./src/

# 自主模式 + 交互审核
agent --mode agentic --input task.md --interactive full
```

### Docker 部署

```bash
docker-compose up -d
```

### API 文档

```
http://127.0.0.1:8911/docs    ← Swagger UI
http://127.0.0.1:8911/redoc   ← ReDoc
```

### 前端入口

```
http://127.0.0.1:8911/app/    ← 新全功能前端
http://127.0.0.1:8911/dashboard  ← 旧内嵌仪表盘
```

---

> **文档生成**：DevAgent 自主分析生成 | **内核版本**：DevAgentCore V2 | **前端版本**：3.0
