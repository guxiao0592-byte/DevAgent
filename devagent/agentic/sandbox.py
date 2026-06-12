"""Docker Sandbox — containerized isolated execution for DevAgent V2.

Implements design doc 09: Docker/Podman/Local three-tier fallback,
resource limits, workspace sync, and SandboxedShellRun replacement.

Architecture:
  SandboxProvider → DockerSandbox | PodmanSandbox | LocalSandbox
  ContainerSpec   → resource limits + environment definition
  WorkspaceSync   → host ↔ container file synchronization
"""

import os
import re
import sys
import json
import asyncio
import subprocess
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    duration_ms: float = 0.0


@dataclass
class ContainerSpec:
    image: str = "python:3.12-slim"
    workspace_mount: str = ""
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    timeout: int = 600
    network: str = "none"
    environment: dict = field(default_factory=dict)
    packages: list[str] = field(default_factory=list)
    volumes: dict[str, str] = field(default_factory=dict)


@dataclass
class BuildResult:
    success: bool
    image_id: str = ""
    error: str = ""


# ============================================================================
# Sandbox Provider — auto-detect best available backend
# ============================================================================

class SandboxProvider:
    """Auto-selects Docker → Podman → Local sandbox."""

    @staticmethod
    def create(config: dict = None) -> "BaseSandbox":
        cfg = config or {}
        provider = cfg.get("sandbox", {}).get("provider", "auto")

        if provider == "docker" or (provider == "auto" and _docker_available()):
            return DockerSandbox(cfg.get("sandbox", {}).get("docker", {}))
        elif provider == "podman" or (provider == "auto" and _podman_available()):
            return PodmanSandbox(cfg.get("sandbox", {}).get("podman", {}))
        else:
            return LocalSandbox(cfg.get("sandbox", {}).get("local", {}))


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _podman_available() -> bool:
    try:
        result = subprocess.run(["podman", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ============================================================================
# Base Sandbox
# ============================================================================

class BaseSandbox:
    """Abstract sandbox interface."""

    async def create(self, task_id: str, spec: ContainerSpec) -> str:
        raise NotImplementedError

    async def exec(self, task_id: str, command: str,
                   timeout: int = 120, workdir: str = "/workspace") -> ExecResult:
        raise NotImplementedError

    async def destroy(self, task_id: str):
        raise NotImplementedError

    async def copy_to(self, task_id: str, host_path: str, container_path: str):
        raise NotImplementedError

    async def copy_from(self, task_id: str, container_path: str, host_path: str):
        raise NotImplementedError


# ============================================================================
# Docker Sandbox
# ============================================================================

class DockerSandbox(BaseSandbox):
    """Docker-based container sandbox."""

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.default_image = cfg.get("image", "python:3.12-slim")
        self.default_memory = cfg.get("memory_limit", "2g")
        self.default_cpu = cfg.get("cpu_limit", 2.0)
        self._containers: dict[str, str] = {}

    async def create(self, task_id: str, spec: ContainerSpec) -> str:
        image = spec.image or self.default_image
        await self._ensure_image(image)

        # Build docker run command
        cmd = [
            "docker", "run", "-d",
            "--name", f"devagent-{task_id}",
            "--memory", spec.memory_limit or self.default_memory,
            "--cpus", str(spec.cpu_limit or self.default_cpu),
            "--network", spec.network,
            "-v", f"{os.path.abspath(spec.workspace_mount)}:/workspace:rw",
            "-w", "/workspace",
            image,
            "tail", "-f", "/dev/null"
        ]

        for host_path, container_path in spec.volumes.items():
            cmd.insert(-4, "-v")
            cmd.insert(-4, f"{os.path.abspath(host_path)}:{container_path}:rw")

        for k, v in spec.environment.items():
            cmd.insert(-4, "-e")
            cmd.insert(-4, f"{k}={v}")

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        container_id = stdout.decode().strip()

        if proc.returncode != 0:
            raise RuntimeError(f"Docker run failed: {stderr.decode()[:500]}")

        self._containers[task_id] = container_id

        # Install packages
        if spec.packages:
            pkgs = " ".join(spec.packages)
            await self.exec(task_id, f"pip install --quiet {pkgs}", timeout=120)

        return container_id

    async def exec(self, task_id: str, command: str,
                   timeout: int = 120, workdir: str = "/workspace") -> ExecResult:
        container_id = self._containers.get(task_id)
        if not container_id:
            return ExecResult(-1, "", f"Container not found: {task_id}")

        start = time.time()
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-w", workdir, container_id,
            "/bin/sh", "-c", command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill_exec(container_id)
            return ExecResult(-1, "", f"Command timed out after {timeout}s",
                            duration_ms=timeout * 1000)

        return ExecResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace")[:5000],
            stderr=stderr.decode("utf-8", errors="replace")[:2000],
            duration_ms=(time.time() - start) * 1000
        )

    async def destroy(self, task_id: str):
        container_id = self._containers.pop(task_id, None)
        if container_id:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stop", container_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)

    async def copy_to(self, task_id: str, host_path: str, container_path: str):
        container_id = self._containers.get(task_id)
        if container_id:
            proc = await asyncio.create_subprocess_exec(
                "docker", "cp", host_path, f"{container_id}:{container_path}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)

    async def copy_from(self, task_id: str, container_path: str, host_path: str):
        container_id = self._containers.get(task_id)
        if container_id:
            proc = await asyncio.create_subprocess_exec(
                "docker", "cp", f"{container_id}:{container_path}", host_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)

    async def _ensure_image(self, image: str):
        """Pull image if not present."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:  # Not found
            proc = await asyncio.create_subprocess_exec(
                "docker", "pull", image,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)

    async def _kill_exec(self, container_id: str):
        """Kill running exec processes in container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "pkill", "-9", "-f", "sh",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            pass


# ============================================================================
# Podman Sandbox (API-compatible with Docker)
# ============================================================================

class PodmanSandbox(DockerSandbox):
    """Podman-based sandbox — same API as Docker, just replace 'docker' with 'podman'."""

    async def _run_podman(self, *args, timeout=30):
        cmd = ["podman"] + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()

    async def create(self, task_id: str, spec: ContainerSpec) -> str:
        cid = f"devagent-{task_id}"
        rc, _, err = await self._run_podman(
            "run", "-d", "--name", cid,
            "--memory", spec.memory_limit or "2g",
            "--cpus", str(spec.cpu_limit or 2.0),
            "-v", f"{os.path.abspath(spec.workspace_mount)}:/workspace:rw",
            "-w", "/workspace",
            spec.image or "python:3.12-slim",
            "tail", "-f", "/dev/null"
        )
        if rc != 0:
            raise RuntimeError(f"Podman run failed: {err[:500]}")
        self._containers[task_id] = cid
        return cid

    async def exec(self, task_id: str, command: str,
                   timeout: int = 120, workdir: str = "/workspace") -> ExecResult:
        cid = self._containers.get(task_id)
        if not cid:
            return ExecResult(-1, "", f"Container not found: {task_id}")
        start = time.time()
        rc, stdout, stderr = await self._run_podman(
            "exec", "-w", workdir, cid, "/bin/sh", "-c", command, timeout=timeout
        )
        return ExecResult(rc, stdout[:5000], stderr[:2000],
                         (time.time() - start) * 1000)

    async def destroy(self, task_id: str):
        cid = self._containers.pop(task_id, None)
        if cid:
            await self._run_podman("stop", cid, timeout=15)
            await self._run_podman("rm", "-f", cid, timeout=10)


# ============================================================================
# Local Sandbox (subprocess — current behavior, enhanced)
# ============================================================================

class LocalSandbox(BaseSandbox):
    """Local subprocess sandbox — enhanced version of current shell_run."""

    DANGEROUS = [r"rm\s+-rf\s+/", r"git\s+push\s+--force",
                 r"sudo\s+", r">\s*/dev/", r"mkfs\."]

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.default_timeout = cfg.get("timeout", 120)
        self.allowed_cmds = cfg.get("allowed_commands", [
            "python", "pytest", "ruff", "mypy", "pip", "git",
            "echo", "ls", "cat", "grep", "find", "curl", "wget"
        ])

    async def create(self, task_id: str, spec: ContainerSpec) -> str:
        return "local"

    async def exec(self, task_id: str, command: str,
                   timeout: int = 120, workdir: str = "/workspace") -> ExecResult:
        for pattern in self.DANGEROUS:
            if re.search(pattern, command):
                return ExecResult(-1, "", f"Blocked dangerous command: {pattern}")

        start = time.time()
        try:
            proc = await asyncio.create_subprocess_shell(
                command, cwd=workdir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=min(timeout, self.default_timeout)
            )
            return ExecResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace")[:5000],
                stderr=stderr.decode("utf-8", errors="replace")[:2000],
                duration_ms=(time.time() - start) * 1000
            )
        except asyncio.TimeoutError:
            return ExecResult(-1, "", f"Command timed out after {timeout}s",
                            duration_ms=timeout * 1000)

    async def destroy(self, task_id: str):
        pass

    async def copy_to(self, task_id: str, host_path: str, container_path: str):
        shutil.copy(host_path, container_path)

    async def copy_from(self, task_id: str, container_path: str, host_path: str):
        shutil.copy(container_path, host_path)


# ============================================================================
# Sandbox Manager — lifecycle + pool
# ============================================================================

class SandboxManager:
    """Manages sandbox lifecycle with task-to-sandbox mapping."""

    def __init__(self, config: dict = None):
        self.sandbox = SandboxProvider.create(config)
        self._task_sandbox_map: dict[str, str] = {}
        self._task_specs: dict[str, ContainerSpec] = {}

    async def create_for_task(self, task_id: str, workspace: str,
                              spec: ContainerSpec = None) -> str:
        if spec is None:
            spec = ContainerSpec(workspace_mount=workspace)

        sandbox_id = await self.sandbox.create(task_id, spec)
        self._task_sandbox_map[task_id] = sandbox_id
        self._task_specs[task_id] = spec
        return sandbox_id

    async def run(self, task_id: str, command: str,
                  timeout: int = 120) -> ExecResult:
        spec = self._task_specs.get(task_id)
        workdir = spec.workspace_mount if spec and spec.workspace_mount else os.getcwd()
        return await self.sandbox.exec(task_id, command, timeout, workdir=workdir)

    async def destroy_for_task(self, task_id: str):
        await self.sandbox.destroy(task_id)
        self._task_sandbox_map.pop(task_id, None)
        self._task_specs.pop(task_id, None)

    async def cleanup_all(self):
        for task_id in list(self._task_sandbox_map.keys()):
            await self.destroy_for_task(task_id)

    @property
    def backend_name(self) -> str:
        return type(self.sandbox).__name__
