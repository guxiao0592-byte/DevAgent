# DevAgent Docker 部署与使用指南

> **版本**: 3.4 | **镜像**: `devagent:3.4` | **端口**: 8911

---

## 目录

1. [环境要求](#1-环境要求)
2. [方式一: Docker Compose (推荐)](#2-方式一-docker-compose-推荐)
3. [方式二: Docker 命令行](#3-方式二-docker-命令行)
4. [方式三: 源码构建](#4-方式三-源码构建)
5. [配置 API Key](#5-配置-api-key)
6. [使用方法](#6-使用方法)
7. [共享给别人](#7-共享给别人)
8. [常见问题](#8-常见问题)

---

## 1. 环境要求

其他用户只需要安装 **Docker**：

```bash
# macOS
brew install docker  # 或下载 Docker Desktop

# Linux
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER  # 免 sudo

# Windows
# 下载 Docker Desktop: https://www.docker.com/products/docker-desktop/

# 验证
docker --version    # 需 >= 24.0
docker compose version
```

---

## 2. 方式一: Docker Compose (推荐)

### 2.1 获取项目

```bash
# 方式 A: Git 克隆
git clone <your-repo-url> DevAgent
cd DevAgent

# 方式 B: 解压压缩包
unzip DevAgent-v3.4.zip
cd DevAgent-v3.4
```

### 2.2 配置 API Key

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 填入你的 API Key
# 至少填一个: DEEPSEEK_API_KEY 或 OPENAI_API_KEY
vim .env

# .env 内容示例:
# DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 2.3 启动服务

```bash
# 构建并启动 (首次需下载镜像，约3-5分钟)
docker compose up -d

# 查看日志
docker compose logs -f devagent

# 验证服务
curl http://localhost:8911/health
# → {"status":"ok"}
```

### 2.4 访问

| 地址 | 功能 |
|------|------|
| http://localhost:8911/health | 健康检查 |
| http://localhost:8911/docs | Swagger API 文档 |
| http://localhost:8911/dashboard | Web 仪表盘 |

### 2.5 停止

```bash
docker compose down        # 停止容器
docker compose down -v     # 停止 + 删除数据卷
```

---

## 3. 方式二: Docker 命令行

### 3.1 拉取/加载镜像

```bash
# 方式 A: 从 Docker Hub 拉取 (如果已发布)
docker pull your-registry/devagent:3.4

# 方式 B: 从本地 tar 文件加载 (分享给离线用户)
docker load -i devagent-3.4.tar.gz
```

### 3.2 运行

```bash
docker run -d \
  --name devagent \
  --restart unless-stopped \
  -p 8911:8911 \
  -e DEEPSEEK_API_KEY=sk-your-key-here \
  -v devagent_outputs:/app/outputs \
  -v devagent_data:/app/.devagent \
  devagent:3.4
```

### 3.3 进入容器执行 CLI 命令

```bash
# 全流程开发
docker exec devagent agent --mode full \
  -i /app/example/requirements.md \
  -o /app/outputs/calculator

# Bug 修复
docker exec devagent agent --mode repair \
  -w /app/outputs/calculator/run_task_xxx

# 查看产物
docker exec devagent ls -la /app/outputs/calculator/
```

---

## 4. 方式三: 源码构建

```bash
# 在项目目录下
docker build -t devagent:3.4 .

# 验证构建
docker images devagent

# 运行
docker run -d --name devagent -p 8911:8911 \
  -e DEEPSEEK_API_KEY=sk-xxx \
  devagent:3.4
```

**构建耗时**: 首次约 5-8 分钟（需下载基础镜像 + npm + pip），后续约 30 秒（利用 Docker 层缓存）。

---

## 5. 配置 API Key

DevAgent 支持三种 LLM 提供商：

| 提供商 | 环境变量 | 获取地址 |
|--------|---------|---------|
| DeepSeek | `DEEPSEEK_API_KEY` | https://platform.deepseek.com |
| OpenAI | `OPENAI_API_KEY` | https://platform.openai.com |
| 华为云 | `HUAWEI_API_KEY` | https://console.huaweicloud.com |

**至少配置一个**。推荐 DeepSeek（性价比高 + 中文好）。

```bash
# 方式 A: .env 文件 (docker compose 自动读取)
echo "DEEPSEEK_API_KEY=sk-your-key" > .env

# 方式 B: 命令行 -e
docker run -e DEEPSEEK_API_KEY=sk-your-key ... devagent:3.4

# 方式 C: 挂载配置文件
docker run -v ./config.yaml:/app/devagent/configs/config.yaml:ro ... devagent:3.4
```

---

## 6. 使用方法

### 6.1 CLI 命令 (容器内执行)

```bash
# ===== 全流程开发 =====
docker compose exec devagent agent --mode full \
  -i /app/example/requirements.md \
  -o /app/outputs/my_project

# ===== 仅需求分析 + 设计 =====
docker compose exec devagent agent --mode design \
  -i /app/example/requirements.md

# ===== Bug 修复 =====
docker compose exec devagent agent --mode repair \
  -w /app/outputs/my_project

# ===== 自主模式 =====
docker compose exec devagent agent --mode agentic \
  -w /app/outputs/my_project

# ===== 交互模式 (CLI 审核) =====
docker compose exec -it devagent agent --mode full \
  -i /app/example/requirements.md \
  --interactive full

# ===== 版本信息 =====
docker compose exec devagent agent --version
```

### 6.2 REST API 调用

```bash
# 提交全流程任务
curl -X POST http://localhost:8911/api/v2/tasks/full \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Build a calculator app",
    "input": "# Calculator\n\n## Requirements\n...",
    "output": "/app/outputs/calculator"
  }'

# → {"task_id":"task_abc123","status":"RUNNING"}

# 查询状态
curl http://localhost:8911/api/v1/tasks/task_abc123

# 查看产物
docker compose exec devagent ls /app/outputs/calculator/run_task_abc/
```

### 6.3 WebSocket 实时交互

```javascript
const ws = new WebSocket("ws://localhost:8911/api/v2/tasks/{task_id}/interactive?mode=controller");

ws.onmessage = (e) => {
  const event = JSON.parse(e.data);
  switch (event.type) {
    case "review.requested":
      // 用户审批 → 返回决策
      ws.send(JSON.stringify({
        type: "review.response",
        data: { review_id: event.data.review_id, decision: "approve", feedback: "OK" }
      }));
      break;
    case "progress.snapshot":
      console.log(`Progress: ${event.data.phase} ${event.data.progress_pct}%`);
      break;
  }
};
```

### 6.4 评测 Pipeline 输出质量

```bash
# 对已完成的任务输出打分 (代码质量 + 文档质量 + 覆盖率)
docker compose exec devagent python -m devagent.benchmarks.pipeline_evaluator \
  --output_dir /app/outputs/calculator/run_task_xxx \
  --case calculator

# 输出:
# 📊 Overall Score: 78.2/100
# 正确性: 80/100 | 完整性: 71/100 | 代码质量: 81/100 | 文档质量: 90/100 | 可维护性: 65/100
```

### 6.5 运行基准测试

```bash
# SWE-bench 风格基准测试
docker compose exec devagent python -m devagent.benchmarks.benchmark_runner \
  --suite default \
  --output /app/outputs/benchmark

# 查看报告
docker compose exec devagent cat /app/outputs/benchmark/summary.md
```

### 6.6 使用自定义需求文件

```bash
# 挂载本地目录到容器
docker compose exec -v $(pwd)/my_requirements.md:/app/inputs/req.md \
  devagent agent --mode full -i /app/inputs/req.md -o /app/outputs/my_project

# 或者在 docker-compose.yml 中配置 volumes:
# volumes:
#   - ./my_projects:/app/inputs:ro
```

---

## 7. 共享给别人

### 7.1 导出镜像 (推荐，接收方无需构建)

```bash
# 在你的机器上构建并导出
docker build -t devagent:3.4 .
docker save devagent:3.4 | gzip > devagent-3.4.tar.gz

# 文件大小: ~400MB (压缩后)
ls -lh devagent-3.4.tar.gz
```

接收方使用：

```bash
# 加载镜像
docker load -i devagent-3.4.tar.gz

# 运行
docker run -d --name devagent -p 8911:8911 \
  -e DEEPSEEK_API_KEY=sk-xxx \
  devagent:3.4

# 验证
curl http://localhost:8911/health
```

### 7.2 完整交付包

打包整个项目目录：

```bash
# 在项目根目录
tar czf devagent-v3.4-full.tar.gz \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='node_modules' \
  --exclude='.git' \
  --exclude='outputs' \
  --exclude='*.pyc' \
  .

# 文件大小: ~200KB (不含镜像)
ls -lh devagent-v3.4-full.tar.gz
```

接收方解压后：

```bash
tar xzf devagent-v3.4-full.tar.gz
cd DevAgent
cp .env.example .env   # 填入 API Key
docker compose up -d    # 自动构建
```

### 7.3 推送到容器仓库

```bash
# Docker Hub
docker tag devagent:3.4 your-username/devagent:3.4
docker push your-username/devagent:3.4

# 阿里云容器镜像服务 (国内更快)
docker tag devagent:3.4 registry.cn-hangzhou.aliyuncs.com/your-ns/devagent:3.4
docker push registry.cn-hangzhou.aliyuncs.com/your-ns/devagent:3.4

# 私有仓库
docker tag devagent:3.4 your-registry.example.com/devagent:3.4
docker push your-registry.example.com/devagent:3.4
```

---

## 8. 常见问题

### Q1: 容器启动失败 "API key not configured"

```bash
# 确保已设置 API Key
docker compose exec devagent env | grep API_KEY

# 重新创建容器
docker compose down && docker compose up -d
```

### Q2: 图表渲染不出来

```bash
# 验证 mermaid-cli 是否安装
docker compose exec devagent mmdc --version

# 如果未安装，进入容器手动安装
docker compose exec -u root devagent npm install -g @mermaid-js/mermaid-cli
```

### Q3: 端口 8911 被占用

```bash
# 修改 docker-compose.yml 中的端口映射
# 将 "8911:8911" 改为 "8912:8911"
# 访问 http://localhost:8912
```

### Q4: 容器内生成的文件如何取出来？

```bash
# 方式 A: docker cp
docker cp devagent:/app/outputs ./local_outputs

# 方式 B: 挂载卷 (docker-compose.yml 已默认配置)
# outputs 目录已挂载为 Docker volume
docker compose cp devagent:/app/outputs ./outputs_backup
```

### Q5: 如何查看生成的项目文件？

```bash
# 列出产物
docker compose exec devagent ls -la /app/outputs/

# 读取报告
docker compose exec devagent cat /app/outputs/calculator/run_task_xxx/06_reports/executive_report.md

# 查看图表
docker compose exec devagent ls /app/outputs/calculator/run_task_xxx/02_design/diagrams/
```

### Q6: 如何更换 LLM 提供商？

```bash
# DeepSeek → OpenAI
docker compose down
export OPENAI_API_KEY=sk-xxx
docker compose up -d

# 或在启动时指定
docker run -e OPENAI_API_KEY=sk-xxx -e DEVAGENT_DEFAULT_PROVIDER=openai ... devagent:3.4
```

### Q7: 内存/CPU 不够

```yaml
# docker-compose.yml 中限制资源
services:
  devagent:
    # ... 其他配置 ...
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
```

### Q8: Windows 换行符问题

Windows 用户克隆项目后可能出现 `\r\n` 问题：

```bash
# Git 配置自动转换
git config --global core.autocrlf input

# 或手动转换
dos2unix .env.example
```

---

> **更多文档**:
> - 系统架构: `docs/DevAgent_系统架构与实现细节完整文档.md`
> - 使用指南: `docs/DevAgent_使用指南.md`
> - 测试方案: `docs/DevAgent_测试方案.md`
> - 源代码: `devagent/`
