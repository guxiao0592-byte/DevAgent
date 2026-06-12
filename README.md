# DevAgent — 统一软件工程智能体

> 基于大语言模型的自主软件工程智能体。支持全流程开发（需求分析→架构设计→代码生成→测试→修复），内置实时交互审核系统，中文文档输出。

## 特性

- **统一 ReAct 引擎**：所有任务通过同一执行引擎，Agent 自主 Think → Act → Observe
- **30 种专业工具**：8 种 Pipeline 工具 + 14 种代码操作工具 + 5 种 GitHub 工具 + 3 种交互审核工具
- **实时人工交互**：WebSocket 双向通信、VSCode 弹窗审核、CLI 终端交互、REST API
- **阶段质量审核**：Agent 完成每个阶段后提交人工审核，支持批准/要求修改/拒绝
- **LLM 质量评估**：10 维自动化评分（正确性/结构/类型安全/文档/测试覆盖等）
- **中文文档输出**：生成的报告和文档使用中文
- **工程级产物**：完整项目脚手架、类型标注、docstring、pytest 测试套件
- **双模型**：DeepSeek + OpenAI，支持 SSE 流式输出

## 快速开始

```bash
cd DevAgent
pip install -r requirements.txt
pip install -e ".[all]"
```

配置 `devagent/configs/config.yaml`：

```yaml
model:
  provider: deepseek
  deepseek:
    api_key: sk-xxx
    model: deepseek-chat
```

```bash
# 准备需求文件
echo "# Calculator App Requirements
Build a calculator supporting +, -, *, / with error handling." > requirements.md

# 全流程开发
agent --mode full --input requirements.md
```

---

## CLI 用法

### 任务模式

| 命令 | 功能 |
|------|------|
| `agent --mode full --input requirements.md` | 全流程：分析→设计→编码→测试→交付 |
| `agent --mode design --input requirements.md` | 仅分析+设计 |
| `agent --mode implement --input requirements.md` | 分析→设计→编码→测试 |
| `agent --mode repair --workspace ./src/` | Bug 修复 |
| `agent --mode agentic` | 自主模式 |
| `agent --mode test --workspace ./src/` | 测试生成+执行 |
| `agent --mode debug --workspace ./src/` | 调试分析 |

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mode, -m` | 任务模式 | `agentic` |
| `--input, -i` | 输入文件 | — |
| `--workspace, -w` | 工作空间 | `.` |
| `--output, -o` | 产物目录 | `./outputs` |
| `--interactive, -I` | `full`/`approval`/`observe`/`off` | `off` |
| `--provider` | `openai`/`deepseek` | 配置文件 |
| `--verbose, -v` | 详细输出 | — |

### 交互模式

```bash
# 完整交互：审批 + 阶段审核 + Agent提问
agent --mode full --input requirements.md --interactive full

# 仅审批危险操作
agent --mode repair --workspace ./src/ --interactive approval
```

交互模式下，Agent 每个阶段完成后等待人工审核。终端用户直接在命令行输入，VSCode 用户通过弹窗操作。

---

## API 服务

```bash
# 启动
devagent-api

# 指定交互模式
devagent-api --interactive full
```

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8911/docs` | Swagger 文档 |
| `http://127.0.0.1:8911/dashboard` | Web 仪表盘 |
| `ws://127.0.0.1:8911/api/v2/tasks/{id}/interactive` | 双向 WebSocket |

### REST API

```bash
# 提交任务
curl -X POST http://127.0.0.1:8911/api/v2/tasks/agentic \
  -H "Content-Type: application/json" \
  -d '{"task":"agentic","input":"需求文档内容...","code":".","output":"./outputs/task1"}'

# 查询状态
curl http://127.0.0.1:8911/api/v1/tasks/{task_id}

# 查询待审核项
curl http://127.0.0.1:8911/api/v2/tasks/{task_id}/review/pending

# 审核响应
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/{task_id}/review/respond?review_id=xxx&decision=approve&feedback=OK"

# 控制指令
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/{task_id}/command?command=pause"
```

### WebSocket 实时交互

```javascript
const ws = new WebSocket("ws://127.0.0.1:8911/api/v2/tasks/{task_id}/interactive?mode=controller");

ws.onmessage = (e) => {
  const event = JSON.parse(e.data);
  switch (event.type) {
    case "review.requested":  // 阶段审核请求
    case "agent.question":    // Agent 向用户提问
    case "approval.requested": // 危险操作审批
    case "progress.snapshot":  // 进度快照
  }
};

// 审核响应
ws.send(JSON.stringify({
  type: "review.response",
  data: { review_id: "rev_xxx", decision: "approve",
          feedback: "通过", suggestions: [] }
}));

// 要求修改
ws.send(JSON.stringify({
  type: "review.response",
  data: { review_id: "rev_xxx", decision: "revise",
          feedback: "需求分析不够详细",
          suggestions: ["添加领域实体列表", "补充实体关系描述"] }
}));

// 控制指令
ws.send(JSON.stringify({ type: "command.pause" }));
ws.send(JSON.stringify({ type: "command.resume" }));
```

---

## VSCode 扩展

```bash
# 打开扩展项目
open -a "Visual Studio Code" vscode_extension

# 按 F5 启动扩展 → 新窗口 Cmd+Shift+P → DevAgent
```

**右键菜单** → DevAgent 子菜单：全流程、分析设计、代码生成、Bug修复、审核审批、暂停恢复。

**交互审核**：Agent 每个阶段完成后右下角弹窗 — [批准] / [要求修改] / [拒绝]。

---

## 工具体系（30 种）

### Pipeline 工具（8 种）

| 工具 | 功能 |
|------|------|
| `plan_task` | 任务分解为执行计划 DAG |
| `analyze_requirements` | 5 步需求分析（领域模型 + FR + NFR + 用例） |
| `design_architecture` | C4 架构 + Mermaid 图表（类图/ER图/API契约） |
| `generate_code` | 工程级 Python 项目（类型标注/docstring/脚手架） |
| `generate_tests` | pytest 测试套件 + 执行 |
| `debug_issue` | 5 步根因分析（10 种缺陷分类） |
| `repair_code` | 最小变更修复 + 回归验证 |
| `generate_report` | 执行报告 + 质量仪表盘 |

### 代码操作工具（14 种）

| 类别 | 工具 |
|------|------|
| 文件 | `file_read` `file_edit` `file_write` `file_list` |
| 搜索 | `grep_text` `grep_ast` `find_symbol` |
| 执行 | `shell_run` `test_run` `lint_check` |
| 版本 | `git_diff` `git_log` `git_blame` |
| 信息 | `web_search` `read_docs` |

### 交互审核工具（3 种）

| 工具 | 功能 | 非交互模式 |
|------|------|----------|
| `ask_user` | Agent 向人类提问 | 自动使用默认值 |
| `request_review` | 阶段人工审核 | 自动批准 |
| `submit` | 提交完成 | — |

### GitHub 集成（5 种）

`gh_issue_read` `gh_pr_create` `gh_pr_comment` `image_read`（多模态）

---

## 实时交互架构

```
VSCode 提交任务 → 后台 Agent 启动 → 到达审核阶段
      ↓                                    ↓
 WebSocket 连接                      ThreadChannel 等待
      ↓                                    ↓
 接收事件流 ←─────────────── review.requested 事件
      ↓
 [批准] / [要求修改] / [拒绝]
      ↓
 响应 → ThreadChannel.resolve() → Agent 继续
```

### 审核流程

1. Agent 完成阶段工作 → 调用 `request_review`
2. QualityEvaluator（LLM）自动质量评分（10 维）
3. 审核文档推送到客户端（WebSocket / CLI / REST）
4. 人工选择：**批准**（继续）/ **要求修改**（注入反馈重做）/ **拒绝**（重新设计）
5. Agent 根据决策执行下一步

---

## 输出目录

```
outputs/{task_id}/
├── 01_requirements/    需求分析文档
├── 02_design/          架构设计文档 + Mermaid 图表
├── 03_implementation/  源代码 + 测试文件
├── 04_tests/           测试执行报告
├── 05_repair/          调试分析 + 修复补丁
└── 06_reports/         最终报告
```

---

## 项目结构

```
DevAgent/
├── devagent/
│   ├── agent_core/          核心基础设施（LLM客户端、配置、状态）
│   ├── agentic/             ReAct 引擎 + 工具系统 + 交互审核
│   │   ├── core.py              主循环（Think→Act→Observe）
│   │   ├── context.py           四层上下文 + 系统提示词
│   │   ├── tools.py             22 种基础工具
│   │   ├── pipeline_tools.py     8 种 Pipeline 工具（V1 适配器）
│   │   ├── interaction.py       交互控制器（审批+对话+进度）
│   │   ├── review_gate.py       阶段审核（LLM评估+人工终审）
│   │   ├── session.py           会话管理（多客户端+重连）
│   │   └── thread_channel.py    跨线程通信桥梁
│   ├── agents/              V1 专业 Agent（被 pipeline_tools 适配）
│   ├── api/app.py           FastAPI + WebSocket V2
│   ├── cli/main.py          统一 CLI 入口
│   └── configs/config.yaml  配置文件
├── vscode_extension/        VSCode 扩展（22 命令 + WS + 审核弹窗）
├── docs/                    设计文档 + 完整架构文档
├── tests/                   286 个单元测试
├── setup.py / pyproject.toml
└── requirements.txt
```

---

## 许可证

MIT License
