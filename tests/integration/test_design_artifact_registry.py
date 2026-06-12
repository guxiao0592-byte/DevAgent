import json
import os
import shutil

from devagent.agent_core.schemas import RequirementInput
from devagent.agent_core.state import AgentState
from devagent.agents.design_agent import DesignAgent


class MockLLM:
    def __init__(self, response: dict):
        self._response = response

    def chat_structured(self, messages, system_prompt=None):
        return self._response


def setup_tmp_output(tmp_dir: str) -> AgentState:
    state = AgentState()
    state.task_id = "task_test"
    state.output_root = tmp_dir
    # minimal requirements structure expected by design agent
    state.requirements = {"title": "Todo API", "entities": ["Todo", "User"]}
    return state


def test_design_agent_registry(tmp_path):
    tmpdir = str(tmp_path)
    state = setup_tmp_output(tmpdir)

    # Minimal deterministic design output from mock llm
    mock_output = {
        "architecture_overview": {"pattern": "layered", "context_diagram_mermaid": "```mermaid\nflowchart LR\nA-->B\n```", "container_diagram_mermaid": ""},
        "class_diagram_mermaid": "```mermaid\nclassDiagram\nclass Todo {\n+int id\n+string title\n}\n```",
        "er_diagram_mermaid": "```mermaid\nerDiagram\nUSER ||--o{ TODO : places\n```",
        "sequence_diagrams": [],
        "database_schema": [],
        "api_contracts": [],
        "technology_stack": {},
    }

    llm = MockLLM(mock_output)
    agent = DesignAgent(llm, config={})
    agent.run(state)

    # Check outputs directory for index
    design_dir = os.path.join(tmpdir, "02_design")
    assert os.path.exists(design_dir)
    index_path = os.path.join(design_dir, "index.json")
    assert os.path.exists(index_path)
    with open(index_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    assert "artifacts" in idx
    # Expect at least class_diagram and er_diagram and md spec
    names = [a['filename'] for a in idx['artifacts']]
    assert any('class_diagram' in n for n in names)
    assert any('er_diagram' in n for n in names)
    assert any('design_md' in n or n.endswith('.md') for n in names)
