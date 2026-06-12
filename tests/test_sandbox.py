"""Tests for Docker Sandbox — containerized execution."""

import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.sandbox import (
    ContainerSpec, ExecResult,
    SandboxProvider, LocalSandbox, SandboxManager,
)


class TestContainerSpec:
    def test_defaults(self):
        spec = ContainerSpec()
        assert spec.image == "python:3.12-slim"
        assert spec.memory_limit == "2g"
        assert spec.cpu_limit == 2.0

    def test_custom(self):
        spec = ContainerSpec(image="python:3.11", memory_limit="4g",
                            workspace_mount="/tmp/test")
        assert spec.workspace_mount == "/tmp/test"


class TestLocalSandbox:
    def test_create_returns_local(self):
        sandbox = LocalSandbox()
        cid = asyncio.run(sandbox.create("test", ContainerSpec()))
        assert cid == "local"

    def test_exec_echo(self):
        sandbox = LocalSandbox()
        result = asyncio.run(sandbox.exec("test", "echo hello", workdir="/tmp"))
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_exec_dangerous_blocked(self):
        sandbox = LocalSandbox()
        result = asyncio.run(sandbox.exec("test", "sudo rm -rf /"))
        assert "Blocked" in result.stderr

    def test_destroy_noop(self):
        sandbox = LocalSandbox()
        asyncio.run(sandbox.destroy("test"))  # Should not raise


class TestSandboxProvider:
    def test_create_local(self):
        sandbox = SandboxProvider.create({"sandbox": {"provider": "local"}})
        assert isinstance(sandbox, LocalSandbox)

    def test_local_has_backend(self):
        sandbox = SandboxProvider.create({"sandbox": {"provider": "local"}})
        assert hasattr(sandbox, 'exec')


class TestSandboxManager:
    def test_create_and_destroy(self):
        mgr = SandboxManager({"sandbox": {"provider": "local"}})
        assert mgr.backend_name == "LocalSandbox"

    def test_run_command(self):
        mgr = SandboxManager({"sandbox": {"provider": "local"}})
        asyncio.run(mgr.create_for_task("t1", "/tmp"))
        result = asyncio.run(mgr.run("t1", "echo test"))
        assert "test" in result.stdout
        asyncio.run(mgr.destroy_for_task("t1"))
