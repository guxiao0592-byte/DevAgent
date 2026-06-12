# DevAgent V2 — 容器化沙箱 Docker 隔离执行设计

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | 容器化沙箱 — Docker 隔离执行详细设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-21 |
| 优先级 | P1 |
| 前置依赖 | [01_架构升级_Agentic_Loop设计方案](./01_架构升级_Agentic_Loop设计方案.md)、[02_工具生态扩展详细设计](./02_工具生态扩展详细设计.md) |

## 1. 问题陈述

当前 `shell_run` 工具直接在宿主机执行命令：

- **安全风险**：`rm -rf` 等危险命令虽有正则过滤，但不完备
- **环境污染**：全局 pip 安装影响宿主机
- **不可复现**：依赖宿主机的 Python 版本和已安装的包
- **无资源限制**：无 CPU/内存/磁盘限制

## 2. 业界参考

| 系统 | 沙箱方案 |
|------|---------|
| OpenHands | Docker 容器，每个 Agent Session 一个独立容器 |
| SWE-agent | Docker 容器 + 资源限制 |
| Claude Code | macOS 沙箱 + 权限系统 |
| GitHub Codespaces | 预配置 devcontainer |

## 3. 设计方案

### 3.1 核心架构

```
┌──────────────────────────────────────────────┐
│                 DevAgent Host                 │
│                                               │
│  ┌─────────┐    ┌────────────────────────┐   │
│  │Agent Core│───▶│   SandboxManager       │   │
│  └─────────┘    │                        │   │
│                 │ ┌────────────────────┐ │   │
│                 │ │ ContainerPool       │ │   │
│                 │ │ ┌──────┐ ┌──────┐  │ │   │
│                 │ │ │ C1   │ │ C2   │  │ │   │
│                 │ │ │(task1│ │(task2│  │ │   │
│                 │ │ │ .venv│ │ .venv│  │ │   │
│                 │ │ └──────┘ └──────┘  │ │   │
│                 │ └────────────────────┘ │   │
│                 └────────────────────────┘   │
└──────────────────────────────────────────────┘
```

### 3.2 ContainerSpec

```python
@dataclass
class ContainerSpec:
    """Docker 容器规格定义"""
    image: str = "python:3.12-slim"     # 基础镜像
    workspace_mount: str = ""            # 宿主机路径 → /workspace
    memory_limit: str = "2g"             # 内存限制
    cpu_limit: float = 2.0               # CPU 核心限制
    disk_limit: str = "10g"              # 磁盘限制
    timeout: int = 600                   # 容器最大运行时间（秒）
    network: str = "none"                # 网络模式：none | bridge
    environment: dict = field(default_factory=dict)
    packages: list[str] = field(default_factory=list)  # 额外 apt/pip 包
    volumes: dict[str, str] = field(default_factory=dict)  # 额外挂载
```

### 3.3 SandboxManager

```python
class SandboxManager:
    """管理 Docker 容器的生命周期"""

    def __init__(self, config: dict):
        self.config = config
        self._containers: dict[str, str] = {}  # task_id → container_id
        self._docker = DockerClient.from_env()

    async def create(self, task_id: str, spec: ContainerSpec) -> str:
        """创建并启动一个新容器"""
        # 1. 拉取镜像（带缓存）
        image = await self._ensure_image(spec.image)

        # 2. 创建容器
        container = self._docker.containers.run(
            image=image,
            command="tail -f /dev/null",  # 保持运行
            detach=True,
            mem_limit=spec.memory_limit,
            nano_cpus=int(spec.cpu_limit * 1e9),
            network_mode=spec.network,
            volumes={
                spec.workspace_mount: {"bind": "/workspace", "mode": "rw"},
                **spec.volumes,
            },
            environment=spec.environment,
            working_dir="/workspace",
        )

        # 3. 安装必要的包
        if spec.packages:
            await self._install_packages(container, spec.packages)

        # 4. 创建项目虚拟环境
        await self._exec(container, "python -m venv /workspace/.venv")

        self._containers[task_id] = container.id
        return container.id

    async def exec(self, task_id: str, command: str,
                   timeout: int = 120) -> ExecResult:
        """在容器中执行命令并返回结果"""
        container = self._get_container(task_id)
        exec_result = container.exec_run(
            cmd=f"/bin/sh -c '{command}'",
            workdir="/workspace",
            user="devagent",
        )
        return ExecResult(
            exit_code=exec_result.exit_code,
            stdout=exec_result.output.decode()[:5000],
            stderr=b"",  # docker-py combines stdout
            duration_ms=0  # docker-py doesn't provide timing
        )

    async def destroy(self, task_id: str):
        """销毁容器，释放资源"""
        container = self._get_container(task_id)
        container.stop(timeout=10)
        container.remove(force=True)
        self._containers.pop(task_id, None)

    async def cleanup_stale(self, max_age_seconds: int = 3600):
        """清理超过指定时间的遗留容器"""
        pass
```

### 3.4 SandboxedShellTool

```python
class SandboxedShellRun(BaseTool):
    """Docker 沙箱化的 shell_run 替代工具"""
    name = "shell_run"
    description = "Run a shell command in the isolated Docker sandbox."

    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        command = params["command"]
        timeout = params.get("timeout", 60)

        # 安全检查（即使在沙箱中，也保留基本防护）
        for pattern in self.DANGEROUS:
            if re.search(pattern, command):
                return ToolResult(False, error=f"Blocked: {pattern}")

        task_id = self._get_current_task_id()  # 从上下文获取
        result = await self.sandbox.exec(task_id, command, timeout)

        return ToolResult(
            success=result.exit_code == 0,
            output=f"$ {command}\n{result.stdout}",
            structured={"exit_code": result.exit_code}
        )
```

### 3.5 Host ↔ Container 文件同步

```python
class WorkspaceSync:
    """保持宿主机和容器工作区文件同步"""

    def __init__(self, host_path: str, container_id: str):
        self.host_path = host_path
        self.container_id = container_id

    # 文件变更由 Docker bind mount 自动同步（实时）
    # 仅在需要双向复制时使用 docker cp

    async def copy_to_host(self, container_path: str, host_path: str):
        """从容器复制文件到宿主机"""
        pass

    async def copy_to_container(self, host_path: str, container_path: str):
        """从宿主机复制文件到容器"""
        pass
```

### 3.6 资源限制配置

```dockerfile
# DevAgent 专用镜像 Dockerfile
FROM python:3.12-slim

# 安全用户
RUN useradd -m -s /bin/bash devagent && \
    mkdir -p /workspace && chown devagent:devagent /workspace

# 预装常用工具
RUN pip install --no-cache-dir \
    pytest>=7.0 ruff mypy coverage black

# 限制
RUN echo "devagent ALL=(ALL) NOPASSWD: /usr/bin/apt" >> /etc/sudoers

USER devagent
WORKDIR /workspace
```

### 3.7 降级策略

```python
class SandboxProvider:
    """自动选择最优沙箱方案：Docker → Podman → 本地子进程"""

    @staticmethod
    def create(config: dict) -> SandboxManager:
        if SandboxProvider._docker_available():
            return DockerSandbox(config)
        elif SandboxProvider._podman_available():
            return PodmanSandbox(config)
        else:
            return LocalSandbox(config)  # 当前 shell_run 的增强版
```

### 3.8 配置

```yaml
sandbox:
  provider: auto  # auto | docker | podman | local
  docker:
    image: "devagent/python-slim:latest"
    memory_limit: "2g"
    cpu_limit: 2.0
    timeout: 600
    network: "none"
    workspace_mount: "./"
    preinstall_packages:
      - pytest
      - ruff
  local:
    timeout: 120
    allowed_commands: ["python", "pytest", "ruff", "mypy", "pip", "git", "echo", "ls", "cat"]
```

## 4. 评估指标

| 指标 | 当前(无沙箱) | 目标 |
|------|------------|------|
| 危险命令阻止率 | 99%（正则） | 100%（容器隔离） |
| 环境一致性 | 依赖宿主机 | 镜像保证一致 |
| 任务可复现性 | 低 | 高（镜像+锁定依赖） |
| 启动延迟 | 0ms | < 3s（镜像缓存） |
