# DevAgent 改进方向详细设计方案

## 概述

本目录包含 DevAgent V2 的全部详细改进设计方案（共 14 份），参照业界顶级软件工程智能体系统设计。

## 文档索引

### 已实施方案（01-06）

| 编号 | 文档 | 说明 | 优先级 | 状态 |
|------|------|------|--------|------|
| 01 | [架构升级_Agentic_Loop设计方案](./01_架构升级_Agentic_Loop设计方案.md) | 线性流水线 → 智能体自主循环核心架构重构 | P0 | ✅ 已实施 |
| 02 | [工具生态扩展详细设计](./02_工具生态扩展详细设计.md) | 从3个工具扩展到16个工具 | P0 | ✅ 已实施 |
| 03 | [智能上下文管理设计](./03_智能上下文管理设计.md) | 缓存感知多消息、幻觉防护、RepoMap | P1 | ✅ 已实施 |
| 04 | [故障定位增强设计](./04_故障定位增强设计.md) | SBFL + AST静态 + LLM融合三层定位 | P2 | ✅ 已实施 |
| 05 | [测试与验证增强设计](./05_测试与验证增强设计.md) | 即时验证、回归选择、变异测试、质量门 | P2 | ✅ 已实施 |
| 06 | [可观测性与用户体验设计](./06_可观测性与用户体验设计.md) | 事件流、Dashboard、HITL、回放 | P3 | ✅ 已实施 |

### 待实施方案（07-14）

| 编号 | 文档 | 说明 | 优先级 | 复杂度 |
|------|------|------|--------|--------|
| 07 | [Planning Phase 任务分解设计](./07_Planning_Phase_任务分解设计.md) | Planner → PlanExecutor 架构，DAG子任务分解 | P0 | 中 |
| 08 | [经验库跨任务学习设计](./08_经验库_跨任务学习设计.md) | bug→fix模式向量库 + 混合检索 + few-shot注入 | P0 | 中 |
| 09 | [容器化沙箱 Docker隔离执行设计](./09_容器化沙箱_Docker隔离执行设计.md) | Docker/Podman/本地三级降级，资源限制 | P1 | 中 |
| 10 | [GitHub工具集成 PR/Issue API设计](./10_GitHub工具集成_PR_Issue_API设计.md) | 6个GitHub工具，端到端Issue→PR流程 | P1 | 低 |
| 11 | [多Agent协作并行Worker设计](./11_多Agent协作_并行Worker设计.md) | Coordinator+Worker，DAG拓扑并行组 | P2 | 高 |
| 12 | [LLM驱动上下文压缩设计](./12_LLM驱动上下文压缩设计.md) | 自适应压缩策略 + 关键决策点保护 | P2 | 中 |
| 13 | [多模态支持截图理解设计](./13_多模态支持_截图理解设计.md) | 图片分析工具 + 截图专用分析器 | P3 | 高 |
| 14 | [符号执行形式化验证设计](./14_符号执行_形式化验证设计.md) | CrossHair符号执行 + LLM合约生成 | P3 | 高 |

## 参考系统

- [SWE-agent](https://github.com/SWE-agent/SWE-agent) — Princeton, ReAct Loop + ACI
- [OpenHands](https://github.com/All-Hands-AI/OpenHands) — 事件驱动 + Agent委派
- [Claude Code](https://claude.ai/code) — Anthropic, 丰富工具链 + 子代理
- [CodeR](https://github.com/NL2Code/CodeR) — 微软, MCTS + Tool Use
- [Agentless](https://github.com/OpenAutoCoder/Agentless) — UIUC, 两阶段定位+修复
- [AutoCodeRover](https://github.com/nus-apr/auto-code-rover) — NUS, SBFL + LLM
- [SWE-Fixer](https://github.com/SWE-Fixer/SWE-Fixer) — 静态+动态+LLM三位一体
- [MetaGPT](https://github.com/geekan/MetaGPT) — 多角色Agent协作

## 关系图

```
01_架构升级_Agentic_Loop   ← 核心架构，所有其他设计的基础
    ├── 07_Planning_Phase   ← Agentic Loop 的任务分解前驱
    ├── 11_多Agent协作       ← Agentic Loop 的并行扩展
    │
    ├── 02_工具生态扩展      ← 手和脚
    │   ├── 10_GitHub工具    ← 工具生态的外部扩展
    │   └── 13_多模态支持    ← 工具的视觉维度
    │
    ├── 03_智能上下文管理    ← 眼睛和记忆
    │   ├── 12_LLM上下文压缩 ← 上下文管理的智能升级
    │   └── 08_经验库        ← 长期记忆系统
    │
    ├── 04_故障定位增强      ← 推理能力
    ├── 05_测试与验证增强    ← 质量保证
    │   ├── 09_容器化沙箱     ← 安全隔离执行
    │   └── 14_形式化验证     ← 关键代码数学保证
    │
    └── 06_可观测性与UX      ← 透明度与交互
```

## 实施路线图

```
Phase 1 (当前):  01-06 已全部实施，192测试通过
Phase 2 (P0):    07_Planning + 08_经验库 → 执行效率 + 成功率跃升
Phase 3 (P1):    09_沙箱 + 10_GitHub → 安全 + 真实工作流覆盖
Phase 4 (P2):    11_多Agent + 12_LLM压缩 → 规模 + 稳定性
Phase 5 (P3):    13_多模态 + 14_形式化 → 完整智能体能力
```
