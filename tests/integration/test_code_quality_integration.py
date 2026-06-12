import json
import os

from devagent.agent_core.state import AgentState
from devagent.agents.code_agent import CodeAgent


class MockLLM:
    def __init__(self, response: dict):
        self._response = response

    def chat_structured(self, messages, system_prompt=None):
        return self._response


def setup_state(tmpdir):
    state = AgentState()
    state.task_id = "task_code_test"
    state.output_root = str(tmpdir)
    # provide minimal design artifact so CodeAgent uses it
    state.design_artifacts = {"class_diagram_mermaid": "```mermaid\nclassDiagram\nclass Todo {\n+id\n+title\n}\n```"}
    return state


def test_code_agent_quality_integration(tmp_path):
    tmpdir = tmp_path
    state = setup_state(tmpdir)

    # Mock LLM returns a simple Python file in 'files'
    mock_files = {
        "src/app.py": "def hello():\n    print('hello')\n",
    }
    mock_output = {"files": mock_files, "project_structure": {"src/app.py": "main app"}, "dependencies": {"production": [], "development": []}}

    llm = MockLLM(mock_output)
    agent = CodeAgent(llm, config={})
    agent.run(state)

    impl_dir = os.path.join(str(tmpdir), "03_implementation")
    quality_path = os.path.join(impl_dir, "quality_report.json")
    assert os.path.exists(quality_path), f"Expected quality report at {quality_path}"
    with open(quality_path, "r", encoding="utf-8") as f:
        rep = json.load(f)
    # report should have 'formatted' and 'lint_tool' keys (or at least exist)
    assert "formatted" in rep
    assert "lint_tool" in rep
