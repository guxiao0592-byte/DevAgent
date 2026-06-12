"""Tests for AgentState and TaskSpec."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agent_core.state import AgentState
from devagent.agent_core.schemas import TaskSpec


class TestAgentState:
    def test_initial_state(self):
        state = AgentState(task_type="design", input_path="test.md", output_root="./outputs")
        assert state.task_type == "design"
        assert state.input_path == "test.md"
        assert state.status == "INIT"
        assert state.retry_count == 0
        assert len(state.errors) == 0

    def test_add_trace(self):
        state = AgentState()
        state.add_trace("TestAgent", "completed", {"key": "value"})
        assert len(state.execution_trace) == 1
        assert state.execution_trace[0]["node"] == "TestAgent"
        assert state.execution_trace[0]["status"] == "completed"

    def test_add_error(self):
        state = AgentState()
        state.add_error("test", "Something went wrong")
        assert len(state.errors) == 1
        assert state.errors[0]["phase"] == "test"
        assert state.errors[0]["message"] == "Something went wrong"

    def test_add_warning(self):
        state = AgentState()
        state.add_warning("test", "Warning message")
        assert len(state.warnings) == 1

    def test_to_dict(self):
        state = AgentState(task_id="test-123")
        d = state.to_dict()
        assert d["task_id"] == "test-123"
        assert "status" in d

    def test_output_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = AgentState(output_root=tmp)
            req_dir = state.get_output_subdir("requirements")
            assert req_dir.endswith("01_requirements")
            design_dir = state.get_output_subdir("design")
            assert design_dir.endswith("02_design")


class TestTaskSpec:
    def test_valid_design(self):
        spec = TaskSpec(task_type="design", input_path="req.md")
        valid, msg = spec.validate()
        assert valid is True

    def test_design_missing_input(self):
        spec = TaskSpec(task_type="design")
        valid, msg = spec.validate()
        assert valid is False
        assert "input" in msg

    def test_valid_implement(self):
        spec = TaskSpec(task_type="implement", input_path="design.md")
        valid, msg = spec.validate()
        assert valid is True

    def test_valid_repair(self):
        spec = TaskSpec(task_type="repair", code_path="./src")
        valid, msg = spec.validate()
        assert valid is True

    def test_repair_missing_code(self):
        spec = TaskSpec(task_type="repair")
        valid, msg = spec.validate()
        assert valid is False

    def test_valid_full(self):
        spec = TaskSpec(task_type="full", input_path="req.md")
        valid, msg = spec.validate()
        assert valid is True

    def test_invalid_type(self):
        spec = TaskSpec(task_type="invalid")
        valid, msg = spec.validate()
        assert valid is False
