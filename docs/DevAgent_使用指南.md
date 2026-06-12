# DevAgent 使用指南 — 全功能操作说明

> **版本**: 3.3 | **更新**: 2026-06-04 | **测试**: 288 passed

---

## 目录

1. [快速开始](#1-快速开始)
2. [CLI 命令行使用](#2-cli-命令行使用)
3. [API 服务器使用](#3-api-服务器使用)
4. [WebSocket 实时交互](#4-websocket-实时交互)
5. [VSCode 扩展使用](#5-vscode-扩展使用)
6. [Pipeline 全流程开发](#6-pipeline-全流程开发)
7. [交互审核系统](#7-交互审核系统)
8. [交互修改（Post-Delivery Revision）](#8-交互修改post-delivery-revision)
9. [Docker 容器化部署](#9-docker-容器化部署)
10. [配置说明](#10-配置说明)
11. [输出目录结构](#11-输出目录结构)
12. [完整工作流示例](#12-完整工作流示例)

---

## 1. 快速开始

### 1.1 环境要求

- Python 3.10+
- pip

### 1.2 安装

```bash
cd DevAgent
pip install -r requirements.txt
pip install -e ".[all]"
```

### 1.3 配置 API Key

编辑 `devagent/configs/config.yaml`：

```yaml
model:
  provider: deepseek          # 或 openai
  deepseek:
    api_key: sk-your-key-here
    model: deepseek-chat
    api_base: https://api.deepseek.com
  openai:
    api_key: sk-your-key-here
    model: gpt-4
    api_base: https://api.openai.com/v1
  temperature: 0.0
  max_tokens: 8192
```

### 1.4 验证安装

```bash
agent --version
# DevAgent v3.0.0

agent --help
# 显示完整帮助
```

---

## 2. CLI 命令行使用

### 2.1 命令格式

```bash
agent --mode <模式> [--input <文件>] [--workspace <目录>] [--output <目录>] [其他参数]
```

### 2.2 七种任务模式

| 模式 | 命令 | 执行引擎 | 说明 |
|------|------|---------|------|
| **full** | `agent -m full -i requirements.md` | PipelineRunner | 全流程: 需求→设计→代码→测试→修复→交付→交互修改 |
| **design** | `agent -m design -i requirements.md` | PipelineRunner | 仅分析+设计 (前2阶段) |
| **implement** | `agent -m implement -i requirements.md` | PipelineRunner | 分析→设计→编码→测试 (前4阶段) |
| **agentic** | `agent -m agentic -w ./project/` | DevAgentCore | 自主模式，LLM自主决策 |
| **repair** | `agent -m repair -w ./src/` | DevAgentCore | Bug修复（自动发现+定位+修复） |
| **test** | `agent -m test -w ./src/` | DevAgentCore | 测试生成+执行 |
| **debug** | `agent -m debug -w ./src/` | DevAgentCore | 调试分析+根因定位 |

### 2.3 完整参数列表

| 参数 | 简写 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--mode` | `-m` | str | `agentic` | 任务模式: design/implement/repair/full/agentic/test/debug |
| `--input` | `-i` | str | — | 输入文件路径（需求文档） |
| `--workspace` | `-w` | str | `.` | 工作空间目录 |
| `--output` | `-o` | str | `./outputs` | 产物输出目录 |
| `--max-iterations` | — | int | `50` | ReAct循环最大迭代次数 |
| `--config` | — | str | — | 自定义配置文件路径 |
| `--provider` | — | str | — | LLM提供商: openai / deepseek |
| `--interactive` | `-I` | str | `off` | 交互模式: full / approval / observe / off |
| `--verbose` | `-v` | flag | — | 详细输出 |
| `--version` | `-V` | flag | — | 显示版本号 |

### 2.4 使用示例

```bash
# 1. 全流程开发（非交互）
agent --mode full --input example/requirements.md --output ./outputs/calculator

# 2. 全流程 + CLI实时审核
agent --mode full --input requirements.md --interactive full

# 3. 仅需求分析和设计
agent --mode design --input requirements.md --output ./outputs/design

# 4. 修复Bug
agent --mode repair --workspace ./buggy_project/ --max-iterations 30

# 5. 自主探索模式
agent --mode agentic --workspace ./my_project/ -v

# 6. 使用OpenAI模型
agent --mode full --input requirements.md --provider openai

# 7. 指定配置文件
agent --mode full --input requirements.md --config ./my_config.yaml
```

---

## 3. API 服务器使用

### 3.1 启动服务器

```bash
# 基础启动
devagent-api

# 指定交互模式
devagent-api --interactive full

# 自定义端口
python -m uvicorn devagent.api.app:app --host 0.0.0.0 --port 8911
```

### 3.2 服务端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger 接口文档 |
| GET | `/dashboard` | Web 仪表盘 |
| POST | `/api/v2/tasks/{mode}` | 提交任务 |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态 |
| GET | `/api/v2/tasks/{task_id}/review/pending` | 查询待审核项 |
| POST | `/api/v2/tasks/{task_id}/review/respond` | 审核响应 |
| POST | `/api/v2/tasks/{task_id}/command` | 控制指令 |
| WS | `/api/v2/tasks/{task_id}/interactive` | WebSocket 双向通信 |

### 3.3 REST API 调用示例

#### 提交任务

```bash
# 全流程开发
curl -X POST http://127.0.0.1:8911/api/v2/tasks/full \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Build a calculator app with basic arithmetic operations",
    "input": "# Calculator App\n\n## Functional Requirements\n...",
    "code": ".",
    "output": "./outputs/calculator"
  }'
```

响应：
```json
{
  "task_id": "task_d516a9d1",
  "status": "RUNNING",
  "output_dir": "./outputs/calculator",
  "report_path": "",
  "errors": [],
  "warnings": [],
  "metrics": {}
}
```

#### 查询任务状态

```bash
curl http://127.0.0.1:8911/api/v1/tasks/task_d516a9d1
```

响应：
```json
{
  "status": "COMPLETED",
  "test_results": {"collected": 25, "passed": 25, "failed": 0},
  "modified_files": ["src/calculator.py", "tests/test_calculator.py"],
  "errors": [],
  "iterations": 7,
  "phase": "delivery"
}
```

#### 查询待审核项

```bash
curl http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/review/pending
```

响应：
```json
{
  "has_pending": true,
  "review_id": "rev_abc12345",
  "phase": "design",
  "title": "架构设计",
  "summary": "Phase: 架构设计 (design)...",
  "quality_score": "unknown",
  "status": "pending"
}
```

#### 审核响应

```bash
# 批准
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/review/respond?review_id=rev_abc12345&decision=approve&feedback=设计合理，继续"

# 要求修改
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/review/respond?review_id=rev_abc12345&decision=revise&feedback=需求分析不够详细&suggestions=添加领域实体列表&suggestions=补充实体关系描述"

# 拒绝
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/review/respond?review_id=rev_abc12345&decision=reject&feedback=设计方案不可行"
```

#### 控制指令

```bash
# 暂停 / 恢复 / 终止 / 重定向 / 注入上下文
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/command?command=pause"
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/command?command=resume"
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/command?command=abort"
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/command?command=redirect&focus=优先实现用户认证模块"
curl -X POST "http://127.0.0.1:8911/api/v2/tasks/task_d516a9d1/command?command=inject&context=请使用SQLite而不是PostgreSQL"
```

---

## 4. WebSocket 实时交互

### 4.1 连接

```javascript
const ws = new WebSocket(
  "ws://127.0.0.1:8911/api/v2/tasks/{task_id}/interactive?mode=controller"
);
```

### 4.2 接收事件

```javascript
ws.onmessage = (e) => {
  const event = JSON.parse(e.data);
  switch (event.type) {
    case "review.requested":
      // 阶段审核请求 — 弹窗展示审核界面
      console.log(`Phase review: ${event.data.phase} - ${event.data.title}`);
      // 用户选择 approve / revise / reject
      ws.send(JSON.stringify({
        type: "review.response",
        data: {
          review_id: event.data.review_id,
          decision: "approve",
          feedback: "通过",
          suggestions: []
        }
      }));
      break;

    case "agent.question":
      // Agent 向用户提问
      ws.send(JSON.stringify({
        type: "question.response",
        data: { question_id: event.data.id, answer: "使用JWT认证" }
      }));
      break;

    case "approval.requested":
      // 危险操作审批
      ws.send(JSON.stringify({
        type: "approval.response",
        data: { approval_id: event.data.id, resolution: "approve", note: "允许" }
      }));
      break;

    case "progress.snapshot":
      // 进度快照 — 更新进度条
      console.log(`Progress: ${event.data.phase} ${event.data.progress_pct}%`);
      break;

    case "artifact.created":
      // 新文件生成通知
      console.log(`Created: ${event.data.file}`);
      break;

    case "test.result":
      // 测试结果通知
      console.log(`Tests: ${event.data.passed} passed, ${event.data.failed} failed`);
      break;

    case "control.paused":
      console.log("Agent paused");
      break;

    case "control.resumed":
      console.log("Agent resumed");
      break;

    case "control.aborted":
      console.log("Agent aborted");
      break;
  }
};
```

### 4.3 发送控制指令

```javascript
// 暂停
ws.send(JSON.stringify({ type: "command.pause" }));

// 恢复
ws.send(JSON.stringify({ type: "command.resume" }));

// 终止
ws.send(JSON.stringify({ type: "command.abort" }));

// 心跳
ws.send(JSON.stringify({ type: "ping" }));
```

---

## 5. VSCode 扩展使用

### 5.1 安装

```bash
# 打开扩展项目
open -a "Visual Studio Code" vscode_extension

# 按 F5 启动扩展调试 → 新窗口 Cmd+Shift+P → DevAgent
```

### 5.2 可用命令（22个）

打开命令面板 (Cmd+Shift+P) 输入 `DevAgent`：

| 命令 | 说明 |
|------|------|
| **DevAgent: Full Pipeline** | 启动全流程开发（需求→交付） |
| **DevAgent: Analyze & Design** | 仅需求分析 + 架构设计 |
| **DevAgent: Generate Code** | 从设计生成代码 |
| **DevAgent: Generate Tests** | 从代码生成测试 |
| **DevAgent: Repair Code** | Bug 修复 |
| **DevAgent: Review & Approve** | 查看并批准审核项 |
| **DevAgent: Pause / Resume** | 暂停/恢复执行 |
| **DevAgent: Start API Server** | 启动后端 API 服务 |

### 5.3 右键菜单

在编辑器中右键 → **DevAgent** 子菜单：
- 全流程开发
- 分析设计
- 代码生成
- Bug 修复
- 审核审批
- 暂停 / 恢复

### 5.4 交互审核弹窗

Agent 完成每个阶段后，VSCode 右下角自动弹出通知：
- **[批准]** — 通过，进入下一阶段
- **[要求修改]** — 输入反馈，Agent 重新执行当前阶段
- **[拒绝]** — 拒绝，终止 Pipeline

---

## 6. Pipeline 全流程开发

### 6.1 七阶段流水线

DevAgent 的 Pipeline 包含 7 个阶段：

```
Phase 1: 需求分析      → analyze_requirements  → 01_requirements/
Phase 2: 架构设计      → design_architecture   → 02_design/
Phase 3: 代码生成      → generate_code         → 03_implementation/
Phase 4: 测试执行      → generate_tests        → 04_tests/
Phase 5: Bug修复       → debug+repair          → 05_repair/      [条件触发]
Phase 6: 最终交付      → generate_report       → 06_reports/
Phase 7: 交互修改      → 人工反馈→Agentic修改   → 07_revision/    [条件触发]
```

### 6.2 阶段说明

| 阶段 | 工具 | 输入 | 输出 | 超时 | 重试 |
|------|------|------|------|------|------|
| **需求分析** | analyze_requirements | requirements.md | requirement_specification.md + structured_requirements.json | 600s | 2 |
| **架构设计** | design_architecture | 需求JSON | architecture_design_spec.md + design_artifacts.json + DFD/ADR/威胁模型 | 480s | 2 |
| **代码生成** | generate_code | 设计JSON | Python 源码 + pyproject.toml + Dockerfile + .env.example | 480s | 3 |
| **测试执行** | generate_tests | 代码文件 | 测试文件 + pytest_result.json | 480s | 2 |
| **Bug修复** | debug_issue→repair_code | 测试结果+代码 | debug_analysis.json + patch.diff | 600s | 2 |
| **最终交付** | generate_report | 全部产物 | executive_report.md + result_summary.json | 300s | 1 |
| **交互修改** | DevAgentCore ReAct | 人工反馈 | 修改后的代码+测试 | 1800s | 100 |

### 6.3 条件触发机制

**Phase 5 (Bug修复)**: 仅当 Phase 4 测试失败时触发。修复后自动回到 Phase 4 重新验证（最多2个循环）。

**Phase 7 (交互修改)**: 仅当 WebSocket 客户端已连接时触发。用户可以无限轮修改（上限100轮），输入 "done" / "完成" 结束。

### 6.4 每阶段执行流程

```
1. _run_tool() → LLM 生成内容
2. 文件存在验证 → 检查 wait_files 是否生成
3. 确定性验证 → syntax + lint + pytest (部分阶段)
4. 提交人工审核 → ThreadChannel → WebSocket/CLI/REST
5. 处理审核决策:
   - approve → 进入下一阶段
   - revise → 注入反馈 → 重新执行当前阶段（最多 N 次）
   - reject → 终止 Pipeline
   - timeout → 自动推进
```

---

## 7. 交互审核系统

### 7.1 四种交互模式

| 模式 | 命令 | 审批 | 对话 | 流式 | 阶段审核 |
|------|------|------|------|------|---------|
| **full** | `--interactive full` | ✅ | ✅ | ✅ | ✅ |
| **approval** | `--interactive approval` | ✅ | ❌ | ✅ | ✅ |
| **observe** | `--interactive observe` | ❌ | ❌ | ✅ | ❌ |
| **off** | `--interactive off` (默认) | ❌ | ❌ | ❌ | ❌ |

### 7.2 审核决策三态

| 决策 | 效果 |
|------|------|
| **approve** (批准) | 通过，更新状态，进入下一阶段 |
| **revise** (要求修改) | 注入反馈文本，重新执行当前阶段工具（最多2次） |
| **reject** (拒绝) | 终止 Pipeline，标记 FAILED |

### 7.3 通道回退策略

```
优先:  WebSocket → VSCode 弹窗 / Web 界面
  ↓ 不可用
备选:  CLI 终端 → 命令行输入决策
  ↓ 不可用
备选:  REST API → GET /review/pending + POST /review/respond
  ↓ 不可用
兜底:  自动批准 → 超时15秒后 auto-approve
```

### 7.4 稳定性保障

| 机制 | 作用 |
|------|------|
| **顺序锁** | 已批准阶段不可重复审核 |
| **速率限制** | 全局计数器 ≥ 3 → 强制 submit |
| **修订上限** | 同阶段 revise ≥ 3 → 自动批准 |
| **强制工具注入** | 审核后 100% 执行指定工具 |
| **文件验证** | 每项 files_changed 都 os.path.exists |
| **分级超时** | req:600s / design:480s / code:480s / test:600s |

---

## 8. 交互修改（Post-Delivery Revision）

### 8.1 功能概述

Pipeline 完成后，如果 WebSocket 客户端保持连接，自动进入**交互修改模式**。用户可以：
- 查看完整的项目交付物摘要
- 提出任意修改意见（中文/English）
- DevAgent 自动执行修改、运行测试、提交结果
- 无限轮修改直到满意

### 8.2 工作流程

```
Pipeline 六阶段完成
    ↓
Phase 7: 交互修改
    ├─ 展示项目摘要（所有文件 + 测试结果）
    ├─ 等待用户反馈 ───────────────┐
    │  "请添加日志功能"              │
    │  "改成支持浮点数运算"           │
    │  "增加错误处理覆盖"             │
    ↓                               │
    DevAgent ReAct 循环 ────────────┘
    ├─ 读取当前代码
    ├─ 根据反馈修改
    ├─ 运行测试
    ├─ 提交修改
    ↓
    展示结果 → 继续等待下一轮反馈
    ↓
    用户输入 "done" / "完成" → Pipeline 完成 ✅
```

### 8.3 用户输入

| 输入 | 效果 |
|------|------|
| 任意修改意见 | DevAgent 自动执行修改 |
| `done` / `完成` / `approve` / `好的` / `ok` / `可以` / `满意` / `通过` / `结束` | 结束修改，Pipeline 完成 |
| 空输入或超时 30 分钟 | 自动完成 |

### 8.4 WebSocket 事件

```json
// 收到交互修改请求
{
  "type": "review.requested",
  "data": {
    "phase": "interactive_revision",
    "title": "交互修改 (第1轮)",
    "summary": "# 📦 项目交付物 — 第 1 轮修改\n\n## 需求分析\n  - 01_requirements/requirement_specification.md (8KB)\n...",
    "quality_score": "interactive_revision",
    "feedback_mode": true
  }
}

// 用户响应
ws.send(JSON.stringify({
  type: "review.response",
  data: {
    review_id: "fb_abc12345",
    decision: "continue",
    feedback: "请增加浮点数运算支持，并添加日志功能"
  }
}));
```

---

## 9. Docker 容器化部署

### 9.1 使用 Docker Compose

```bash
# 启动 API 服务
docker-compose up -d

# 查看日志
docker-compose logs -f devagent-api

# 停止
docker-compose down
```

### 9.2 单独构建

```bash
# 构建镜像
docker build -t devagent:latest .

# 运行容器
docker run -d \
  --name devagent-api \
  -p 8911:8911 \
  -e DEEPSEEK_API_KEY=sk-your-key \
  -v $(pwd)/outputs:/app/outputs \
  -v $(pwd)/.devagent:/app/.devagent \
  devagent:latest
```

### 9.3 Docker Compose 配置

```yaml
version: "3.8"
services:
  devagent-api:
    build:
      context: .
      dockerfile: Dockerfile
    image: devagent:latest
    container_name: devagent-api
    ports:
      - "8911:8911"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./outputs:/app/outputs
      - ./.devagent:/app/.devagent
      - ./devagent/configs/config.yaml:/app/devagent/configs/config.yaml:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8911/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 9.4 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 使用 DeepSeek 时 |
| `OPENAI_API_KEY` | OpenAI API 密钥 | 使用 OpenAI 时 |
| `HUAWEI_API_KEY` | 华为云 API 密钥 | 使用华为云时 |
| `DEVAGENT_CONFIG` | 配置文件路径 | 否（默认 config.yaml） |

---

## 10. 配置说明

### 10.1 完整配置文件

```yaml
# devagent/configs/config.yaml

model:
  provider: deepseek          # deepseek / openai
  temperature: 0.0
  max_tokens: 8192

  deepseek:
    api_key: sk-xxx
    model: deepseek-chat
    api_base: https://api.deepseek.com

  openai:
    api_key: sk-xxx
    model: gpt-4
    api_base: https://api.openai.com/v1

workflow:
  enable_quality_gates: true
  enable_planning: true
  enable_experience_library: true
  max_retry: 2
  output_root: "./outputs"

  agentic:
    max_iterations: 100
    stuck_window: 30
    enable_planning: true
    enable_experience_library: true
    enable_fault_localization: true
    enable_instant_validation: true
    enable_tool_filtering: true
    enable_sandbox: false
    snapshot_interval_ms: 500

tools:
  pytest_timeout: 120
  enable_quality: true
```

### 10.2 质量开关

| 配置项 | 说明 | 建议 |
|--------|------|------|
| `enable_quality_gates` | 阶段间LLM质量评估 | 开启 |
| `enable_planning` | Agent自动任务分解 | 开启 |
| `enable_fault_localization` | 三层混合故障定位 | 开启 |
| `enable_instant_validation` | 编辑后即时语法检查 | 开启 |
| `enable_tool_filtering` | 阶段自适应工具过滤 | 开启 |
| `enable_sandbox` | Docker沙箱隔离 | 需要时开启 |
| `enable_experience_library` | 经验库（Bug修复记录） | 开启 |

---

## 11. 输出目录结构

```
outputs/run_task_xxxx/
├── 01_requirements/               # Phase 1: 需求分析
│   ├── requirement_specification.md    # IEEE 830 风格需求规格说明
│   ├── structured_requirements.json    # 结构化需求JSON
│   ├── index.json                      # Artifact索引
│   └── execution_plan.md               # 执行计划（如启用）
│
├── 02_design/                     # Phase 2: 架构设计
│   ├── architecture_design_spec.md     # IEEE 1016 风格设计说明
│   ├── design_artifacts.json           # 结构化设计JSON（含DFD/ADR/威胁模型）
│   ├── class_diagram.mmd              # Mermaid 类图
│   ├── er_diagram.mmd                 # Mermaid ER图
│   ├── sequence_diagram_1.mmd         # Mermaid 时序图
│   ├── api_contracts.json             # API 合约
│   ├── database_schema.json           # 数据库 Schema
│   ├── technology_stack.json          # 技术栈
│   └── index.json
│
├── 03_implementation/             # Phase 3: 代码生成
│   ├── src/                            # 源代码
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── exceptions.py
│   │   ├── logging_config.py
│   │   ├── models/                     # 领域模型
│   │   ├── services/                   # 业务逻辑
│   │   ├── repositories/              # 数据访问
│   │   ├── api/                        # API 路由
│   │   └── main.py                     # 入口点
│   ├── tests/                          # 测试（初始生成）
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── Dockerfile
│   ├── .env.example
│   ├── Makefile
│   ├── README.md
│   ├── quality_report.json
│   └── index.json
│
├── 04_tests/                      # Phase 4: 测试执行
│   ├── tests/                          # 测试文件
│   ├── test_strategy.json
│   ├── pytest_result.json
│   ├── test_execution_report.md
│   └── index.json
│
├── 05_repair/                     # Phase 5: Bug修复（条件触发）
│   ├── patch.diff                      # 统一 diff 补丁
│   ├── debug_analysis.json
│   ├── repair_result.json
│   ├── repair_report.md
│   ├── repaired_code/                  # 修复后的代码
│   └── index.json
│
├── 06_reports/                    # Phase 6: 最终交付
│   ├── executive_report.md             # 执行报告（含 Dashboard + RTM）
│   ├── result_summary.json             # 结构化结果摘要
│   └── index.json
│
└── 07_revision/                   # Phase 7: 交互修改（条件触发）
    └── (修改记录)
```

---

## 12. 完整工作流示例

### 12.1 场景一：全流程非交互开发

```bash
# 1. 准备需求文件
cat > calculator_requirements.md << 'EOF'
# 计算器应用需求

## 功能需求
1. 支持四则运算: +, -, *, /
2. 支持运算符优先级
3. 括号支持
4. 除零错误处理

## 验收标准
- 3 + 4 * 2 = 11
- (3 + 4) * 2 = 14
- 10 / 0 → 错误提示
EOF

# 2. 执行全流程
agent --mode full --input calculator_requirements.md --output ./outputs/calculator

# 3. 查看产物
ls -la outputs/calculator/run_task_*/01_requirements/
ls -la outputs/calculator/run_task_*/03_implementation/src/
cat outputs/calculator/run_task_*/06_reports/executive_report.md

# 4. 运行生成的代码
cd outputs/calculator/run_task_*/03_implementation/
pip install -r requirements.txt
python src/main.py
```

### 12.2 场景二：CLI 交互审核模式

```bash
agent --mode full --input requirements.md --interactive full

# 每个阶段完成后，终端会提示:
# ══════════════════════════════════════════════
#   PHASE REVIEW — requirements
# ══════════════════════════════════════════════
#   Title: 需求分析
#   Quality: unknown
#   (auto-approve in 600s)
#
#   [A]pprove  [R]evise (with feedback)  Re[J]ect
# Your choice: a
# Optional comment: 需求分析很详细
```

### 12.3 场景三：API + WebSocket 实时交互

```bash
# 终端1: 启动 API 服务器
devagent-api --interactive full

# 终端2: 提交任务
curl -X POST http://127.0.0.1:8911/api/v2/tasks/full \
  -H "Content-Type: application/json" \
  -d '{"task":"Build a calculator app", "input":"# Calculator App\n...", "code":".","output":"./outputs/calc"}'
# 返回 {"task_id": "task_abc123", ...}

# 终端3: WebSocket 连接
websocat ws://127.0.0.1:8911/api/v2/tasks/task_abc123/interactive?mode=controller
# 实时接收事件流，手动回复审核
```

### 12.4 场景四：Bug 修复

```bash
# 假设 your_project/ 有测试失败的代码
agent --mode repair --workspace ./your_project/ --output ./outputs/repair

# DevAgent 自动:
# 1. 运行 test_run 发现失败测试
# 2. 调用 debug_issue 定位根因
# 3. 调用 repair_code 生成最小补丁
# 4. 重新运行测试验证修复
# 5. 生成 debug_analysis.json + patch.diff + repair_report.md
```

### 12.5 场景五：交互修改（Post-Delivery Revision）

```bash
# 前提: API 服务器运行中 + WebSocket 客户端已连接

# 1. 提交全流程任务（通过 VSCode 扩展或 REST API）
# 2. Pipeline 执行完毕，自动进入交互修改阶段
# 3. WebSocket 收到反馈请求弹窗:
#    "📦 项目交付物 — 第 1 轮修改"
#    "输入修改意见..."
# 4. 用户输入: "请添加浮点数运算支持，把整数改成 float"
# 5. DevAgent 自动修改代码 → 运行测试 → 展示结果
# 6. 用户输入: "done"  → Pipeline 完成
```

---

> **相关文档**:
> - 系统架构与实现细节: `docs/DevAgent_系统架构与实现细节完整文档.md`
> - 质量提升方案: `~/.claude-general/plans/hashed-sparking-sky.md`
> - 中期答辩 PPT: `docs/DevAgent_midterm.pptx`
> - 讲稿: `docs/DevAgent_midterm_script.md`
