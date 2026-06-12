# DevAgent 项目——中期进展总结

> **版本**: 3.1 | **日期**: 2026-06-04 | **测试**: 286 全部通过 | **代码**: 23,493 行 / 62 文件

---

## 一、项目概述

DevAgent 是一个**基于大语言模型的统一软件工程智能体平台**，目标是通过 LLM 驱动的多智能体协同，实现从需求分析、架构设计、代码生成、测试执行到 Bug 修复与审查的**端到端自动化软件工程工作流**。

### 核心能力
- **全流程开发**：需求 → 设计 → 编码 → 测试 → 修复 → 交付
- **双模执行引擎**：Plan-Execute-Gate（结构化流水线）+ ReAct（自主探索）
- **实时人工交互**：WebSocket / CLI / REST 三通道，阶段审核 + 危险操作审批
- **30 种专业工具**：Pipeline 工具 + 代码操作 + GitHub 集成 + 交互审核
- **双模型支持**：DeepSeek + OpenAI，SSE 流式输出
- **多 IDE 集成**：VSCode / IntelliJ / Eclipse 扩展
- **容器化部署**：Docker + Docker Compose + GitLab CI

---

## 二、系统总体架构

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
    │  PipelineValidator (确定验证)   │
    └───────────────┬─────────────────┘
                    │
    ┌───────────────┴─────────────────┐
    │        增强模块                 │
    │  ContextManager (4层缓存)       │
    │  FaultLocator (3层融合)         │
    │  ArtifactRegistry / Sandbox     │
    │  ExperienceStore / Observability│
    └─────────────────────────────────┘
```

### 分层说明

| 层 | 职责 | 核心模块 |
|---|------|---------|
| **入口层** | 用户交互界面 | CLI (click)、REST API (FastAPI)、VSCode/IntelliJ/Eclipse 扩展 |
| **路由层** | 模式分发 | 根据 --mode 参数路由到 PipelineRunner 或 DevAgentCore |
| **执行引擎层** | 任务执行 | PipelineRunner (Plan-Execute-Gate) / DevAgentCore (ReAct Loop) |
| **工具层** | 能力提供 | 30 种工具 (8 Pipeline + 14 Code + 5 GitHub + 3 Interactive) |
| **交互控制层** | 人机协同 | ThreadChannel、SessionManager、PhaseReviewGate、InteractionController |
| **增强模块层** | 质量保障 | ContextManager、FaultLocator、ArtifactRegistry、Sandbox、Validation |

---

## 三、双模执行引擎

### 3.1 Plan-Execute-Gate 模式（PipelineRunner）

适用于 `--mode full / design / implement` 等结构化任务，**代码硬编码阶段序列，不依赖 LLM 决策**：

```
Phase 1: requirements → analyze_requirements → 确定验证 → 审核 → approve
Phase 2: design       → design_architecture    → 确定验证 → 审核 → approve
Phase 3: code         → generate_code          → 确定验证 → 审核 → approve
Phase 4: test         → generate_tests+test_run → 确定验证 → 审核 → approve
Phase 5: delivery     → generate_report        → 审核 → submit
```

### 3.2 ReAct 自主模式（DevAgentCore）

适用于 `--mode agentic / repair / debug` 等探索性任务：

```
Think → Act → Observe → 循环
  │       │       │
  │   5个交互检查点 (CP1-CP5)
  │   - 用户指令处理 / 暂停恢复 / 终止检测 / 审批门 / 进度发布
  │
  └── 强制工具注入 (审核响应后跳过LLM直接执行)
```

---

## 四、工具体系（30 种）

| 类别 | 数量 | 工具 |
|------|------|------|
| **Pipeline 工具** | 8 | plan_task, analyze_requirements, design_architecture, generate_code, generate_tests, debug_issue, repair_code, generate_report |
| **代码操作工具** | 14 | file_read/edit/write/list, grep_text/ast, find_symbol, shell_run, test_run, lint_check, git_diff/log/blame, web_search, read_docs |
| **GitHub 集成** | 5 | gh_issue_read, gh_pr_create/comment, image_read (多模态) |
| **交互审核** | 3 | ask_user, request_review, submit |

---

## 五、实时交互审核系统

### 完整交互链路

```
Agent 调用 request_review → 文件存在验证 → 顺序锁检查 → 速率限制
  → ThreadChannel 轮询客户端 (15s, 每0.5s)
  → 推送 review.requested 事件 → threading.Event 阻塞Agent线程
  → 人工 approve/revise/reject → ThreadChannel.resolve()
  → 强制工具注入 → Agent 继续执行
```

### 通道回退策略

```
优先: WebSocket (实时弹窗)
  ↓ 不可用
备选: CLI 终端 (命令行输入)
  ↓ 不可用
备选: REST API (轮询审核)
  ↓ 不可用
最终: 自动批准 (超时15s)
```

### 稳定性保障

| 机制 | 作用 |
|------|------|
| **顺序锁** | 禁止已批准阶段重复审核 |
| **速率限制** | 全局计数器 ≥ 3 强制提交 |
| **修订上限** | 同阶段 revise ≥ 3 次自动批准 |
| **强制工具注入** | 审核后 100% 执行指定工具 |
| **文件验证** | 确保有真实产出才提交审核 |
| **分级超时** | requirements:600s / design:480s / code:480s / test:600s |

---

## 六、8 个专业 Agent

| Agent | 职责 | 关键能力 |
|-------|------|---------|
| **PlannerAgent** | 任务分解 | 将原始输入分解为可执行阶段 |
| **RequirementAgent** | 需求分析 | 5步分析（领域模型+FR+NFR+用例） |
| **DesignAgent** | 架构设计 | C4架构+Mermaid图表（类图/ER图/API契约） |
| **CodeAgent** | 代码生成 | 工程级Python项目（类型标注/docstring/脚手架） |
| **TestAgent** | 测试生成 | pytest测试套件生成+执行 |
| **DebugAgent** | 调试分析 | 5步根因分析（10种缺陷分类） |
| **RepairAgent** | Bug修复 | 最小变更修复+回归验证 |
| **ReviewAgent** | 代码审查 | 质量审查+改进建议 |

所有 Agent 基于 `BaseAgent` 抽象基类，内置：
- **自反射机制**：输出自我审查 + 最多2轮迭代改进
- **ArtifactRegistry 集成**：自动注册产物到中央仓库（带 SHA256 校验）
- **结构化输出**：LLM 返回 JSON 结构化数据

---

## 七、核心技术亮点

### 7.1 四层上下文管理（ContextManager）

```
MESSAGE 1: SYSTEM (静态，完全可缓存)
  - Agent 身份、规则、输出格式、工具目录

MESSAGE 2: REPO MAP (半静态，增量更新)
  - 目录树、符号表、依赖关系

MESSAGE 3: DYNAMIC CONTEXT (每轮变化)
  - 阶段感知预算分配、焦点文件内容、近期历史
  - 接地锚点 (file:line)、测试失败原文

MESSAGE 4: 幻觉防护
  - Read-before-edit 强制、歧义标记 [UNCERTAIN]
  - 阶段自适应工具范围缩小
```

### 7.2 三层混合故障定位（FaultLocator）

```
Layer 1 — SBFL: Spectrum-based Fault Localization
  - sys.settrace 收集执行轨迹 + Ochiai 可疑度评分

Layer 2 — Static: AST 缺陷模式检测
  - null-check / boundary / exception / type 模式

Layer 3 — LLM Fusion: 融合前两层结果
  - 输出精确 Bug 位置 + 置信度评分
```

### 7.3 中央产物仓库（ArtifactRegistry）

- 所有 Agent 产物统一注册到 `index.json`（含 metadata、SHA256 checksum、size、mime_type）
- 原子索引写入（临时文件替换，防并发损坏）
- 渐进式迁移（优先 registry，失败回退文件系统）

### 7.4 确定性验证体系

| 阶段 | 检查项 | 阻塞性 |
|------|--------|--------|
| requirements | syntax (AST parse) | 否 |
| design | syntax (Mermaid + Python) | 否 |
| implementation | syntax + lint (ruff) + import check | **是** |
| testing | syntax + pytest (all must pass) | **是** |
| delivery | syntax | 否 |

---

## 八、量化进展

### 8.1 代码规模

| 层 | 模块数 | 代码行 | 占比 |
|---|--------|--------|------|
| **V2 Agentic 核心** | 22 文件 | 12,810 行 | 55% |
| **V1 Agent + Tools + Core** | 17 文件 | 4,403 行 | 19% |
| **API + CLI + IDE 扩展** | 4 文件 | 2,060 行 | 9% |
| **测试** | 14 文件 | 3,465 行 | 15% |
| **配置 + LSP + Benchmarks** | 5 文件 | 755 行 | 3% |
| **总计** | **62 文件** | **23,493 行** | 100% |

### 8.2 测试覆盖

- **286 个测试用例，全部通过**
- 覆盖：Agent 核心循环、工具执行、上下文管理、故障定位、多模态、沙箱、验证、规划、多智能体、状态图、经验存储、可观测性

### 8.3 验证基线

| 场景 | 成功率 | 平均迭代 | 平均耗时 |
|------|--------|---------|---------|
| 单阶段 approve (WS) | 100% | 3-7 | 15-25s |
| Pipeline 全5阶段 (auto) | 100% | 5 | 60-90s |
| Pipeline + WS 审核 | 100% | 5 | 90-180s |
| approve+revise 闭环 | ~70% | 25-40 | 60-120s |

### 8.4 已实现功能清单

- [x] 双模执行引擎（Plan-Execute-Gate + ReAct）
- [x] 30 种专业工具
- [x] 8 个专业 Agent（Planner → Review）
- [x] 实时交互审核系统（WS + CLI + REST 三通道）
- [x] 四层上下文管理（含缓存优化 + 幻觉防护）
- [x] 三层混合故障定位（SBFL + Static + LLM）
- [x] 中央产物仓库（ArtifactRegistry + SHA256）
- [x] 确定性验证体系（syntax + lint + pytest）
- [x] BaseAgent 自反射 + 迭代改进
- [x] 中文文档输出保障
- [x] Docker 容器化 + GitLab CI
- [x] VSCode / IntelliJ / Eclipse IDE 扩展
- [x] FastAPI REST + WebSocket V2 API
- [x] LSP 语言服务器
- [x] SWE-bench 适配器 + 基准测试框架

---

## 九、部署方案

```bash
# Docker 部署
docker-compose up -d

# 本地 CLI
agent --mode full --input requirements.md

# API 服务
devagent-api  # http://127.0.0.1:8911
```

---

## 十、后续工作计划

### 短期（1-2 周）
1. **导出工具**：实现按 workflow_id 导出 artifact bundle（含 checksum）供审计/交付
2. **CI 集成**：将 registry smoke-test 与 pytest 集成到 CI 流水线
3. **前后端联调**：完善 VSCode 扩展的 WebSocket 审核交互

### 中期（1-2 月）
4. **扩展 Registry 后端**：加入 S3/远端存储适配器，支持生命周期策略
5. **增强索引**：checksum → artifact_id 反向索引，支持快速去重
6. **SWE-bench 评测**：完成标准化基准测试，量化对比业界方案
7. **多语言支持**：扩展代码生成至 TypeScript/Go/Java

### 长期
8. **强化学习微调**：基于经验存储的 Agent 策略优化
9. **多 Agent 协作**：多 Agent 并行执行 + 结果合并
10. **企业级部署**：Kubernetes Helm Chart + 多租户隔离

---

## 十一、演示方案（答辩建议）

### 演示 1：全流程自动化
从 `requirements.md` 输入 → 需求分析 → 架构设计 → 代码生成 → 测试执行 → 最终报告，展示 `outputs/` 目录下各阶段产物。

### 演示 2：实时交互审核
VSCode 扩展提交任务 → WebSocket 连接 → 阶段完成后弹窗审核 → 批准/要求修改 → Agent 继续执行。

### 演示 3：Bug 修复流程
输入含 Bug 的代码 → FaultLocator 三层定位 → RepairAgent 最小变更修复 → 回归测试通过。

---

> **答辩准备清单**：
> - 确保 API 服务可启动（`devagent-api`）
> - 准备一个简洁的需求文件（如 Calculator App）
> - VSCode 扩展已加载并可用
> - Docker 镜像可用于展示部署
