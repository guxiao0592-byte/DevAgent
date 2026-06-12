"""Planning agent — decomposes raw input into a structured execution plan.

This agent runs before the main workflow and produces:
1. Task decomposition into phases with clear inputs/outputs
2. Dependency graph between phases
3. Complexity estimation for each phase
4. Risk assessment specific to this project
5. Quality gates for each deliverable
"""

import json
import os
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.artifact_registry import ArtifactRegistry


PLANNER_PROMPT = """You are a senior technical project planner. Given a raw project description, decompose it into a structured execution plan.

=== ANALYSIS PROCESS ===

1. PROJECT SCOPE: Identify the core purpose, target users, and key deliverables.

2. PHASE DECOMPOSITION: Break the project into these standard phases (skip if not applicable):
   - requirements: Domain modeling, user stories, acceptance criteria
   - architecture: System design, module decomposition, technology choices
   - implementation: Code generation, project scaffold, configuration
   - testing: Unit tests, integration tests, coverage goals

3. DEPENDENCIES: Which phases depend on which? Map the critical path.

4. COMPLEXITY ESTIMATION: For each phase, estimate:
   - complexity: simple / moderate / complex
   - key challenges: what makes this phase hard
   - risk factors: what could go wrong

5. QUALITY GATES: For each phase output, define required quality criteria.

6. RISK ASSESSMENT: Overall project risks, mitigation strategies.

Output ONLY valid JSON:
{
  "project_overview": {
    "name": "project name",
    "type": "web_api / cli_tool / library / data_pipeline / other",
    "description": "one-paragraph summary",
    "key_deliverables": ["deliverable 1", "deliverable 2"],
    "estimated_complexity": "simple / moderate / complex"
  },
  "execution_plan": {
    "phases": [
      {
        "phase": "requirements",
        "enabled": true,
        "purpose": "what this phase achieves",
        "inputs": ["raw project description"],
        "outputs": ["structured requirements", "domain model", "use cases"],
        "dependencies": [],
        "complexity": "moderate",
        "key_challenges": ["challenge 1"],
        "quality_gates": ["gate 1: all entities identified", "gate 2: acceptance criteria measurable"]
      },
      {
        "phase": "architecture",
        "enabled": true,
        "purpose": "...",
        "inputs": ["structured requirements"],
        "outputs": ["architecture design", "class diagrams", "database schema"],
        "dependencies": ["requirements"],
        "complexity": "moderate",
        "key_challenges": [],
        "quality_gates": ["gate 1"]
      },
      {
        "phase": "implementation",
        "enabled": true,
        "purpose": "...",
        "inputs": ["architecture design"],
        "outputs": ["project source code", "configuration", "Dockerfile"],
        "dependencies": ["architecture"],
        "complexity": "complex",
        "key_challenges": [],
        "quality_gates": ["gate 1: all modules implemented"]
      },
      {
        "phase": "testing",
        "enabled": true,
        "purpose": "...",
        "inputs": ["project source code"],
        "outputs": ["test suite", "test report"],
        "dependencies": ["implementation"],
        "complexity": "complex",
        "key_challenges": [],
        "quality_gates": ["gate 1: >80% pass rate"]
      }
    ],
    "critical_path": ["requirements", "architecture", "implementation", "testing"],
    "estimated_total_phases": 4
  },
  "risk_assessment": [
    {
      "risk": "description",
      "probability": "low/medium/high",
      "impact": "low/medium/high",
      "affected_phase": "requirements/architecture/implementation/testing",
      "mitigation": "how to reduce risk"
    }
  ]
}

Be specific — use actual details from the project description, not generic templates."""


class PlannerAgent(BaseAgent):
    """Agent responsible for task decomposition and execution planning."""

    def __init__(self, llm_client, config=None):
        super().__init__(llm_client, config)
        self._enable_self_review = False  # Planner does not self-review

    def run(self, state: AgentState) -> AgentState:
      """Analyze input and produce a structured execution plan."""
      try:
        raw_input = self.file_tool.read_text(state.input_path)
      except FileNotFoundError:
        state.add_error("planning", f"Input file not found: {state.input_path}")
        state.status = "ANALYSIS_DONE"
        return state

      truncated = self._truncate_text(raw_input, max_chars=6000)

      result = self.llm.chat_structured(
        messages=[{
          "role": "user",
          "content": (
            f"Analyze this project description and produce a structured execution plan:\n\n"
            f"---INPUT---\n{truncated}\n---END---"
          )
        }],
        system_prompt=PLANNER_PROMPT
      )

      state.input_manifest = result

      # Save plan artifacts to registry
      md_content = self._generate_plan_markdown(result)
      out_root = state.output_root or state.input_path or "outputs"
      registry = ArtifactRegistry(out_root)
      from ..agent_core.schemas import Artifact as ArtifactModel

      md_artifact = ArtifactModel(
        id="",
        type="planning:plan",
        format="md",
        content=md_content,
        metadata={"filename": "execution_plan.md"},
      )
      registry.register_from_state(state, "requirements", md_artifact)

      json_artifact = ArtifactModel(
        id="",
        type="planning:plan",
        format="json",
        content=json.dumps(result, ensure_ascii=False, indent=2),
        metadata={"filename": "execution_plan.json"},
      )
      registry.register_from_state(state, "requirements", json_artifact)

      plan = result.get("execution_plan", {})
      phases = plan.get("phases", [])
      enabled_phases = [p["phase"] for p in phases if p.get("enabled")]

      state.status = "ANALYSIS_DONE"
      state.add_trace(
        "PlannerAgent",
        "completed",
        {
          "enabled_phases": enabled_phases,
          "estimated_complexity": result.get("project_overview", {}).get("estimated_complexity", "unknown"),
          "risks_identified": len(result.get("risk_assessment", [])),
        },
      )

      return state

    def _generate_plan_markdown(self, plan: dict) -> str:
        """Generate a markdown execution plan document."""
        lines = []
        overview = plan.get("project_overview", {})
        lines.append("# Execution Plan\n")
        lines.append("## Project Overview\n")
        lines.append(f"- **Name**: {overview.get('name', 'N/A')}")
        lines.append(f"- **Type**: {overview.get('type', 'N/A')}")
        lines.append(f"- **Complexity**: {overview.get('estimated_complexity', 'N/A')}")
        lines.append(f"- **Description**: {overview.get('description', 'N/A')}")
        lines.append("\n**Key Deliverables**:")
        for d in overview.get("key_deliverables", []):
            lines.append(f"- {d}")

        exec_plan = plan.get("execution_plan", {})
        lines.append("\n---\n## Execution Phases\n")
        for phase in exec_plan.get("phases", []):
            status = "✅ Enabled" if phase.get("enabled") else "⏭️ Skipped"
            lines.append(f"\n### {phase.get('phase', '')} {status}")
            lines.append(f"- **Purpose**: {phase.get('purpose', '')}")
            lines.append(f"- **Complexity**: {phase.get('complexity', '')}")
            deps = phase.get("dependencies", [])
            if deps:
                lines.append(f"- **Depends On**: {', '.join(deps)}")
            lines.append(f"- **Outputs**: {', '.join(phase.get('outputs', []))}")
            if phase.get("key_challenges"):
                lines.append("- **Key Challenges**:")
                for c in phase["key_challenges"]:
                    lines.append(f"  - {c}")
            if phase.get("quality_gates"):
                lines.append("- **Quality Gates**:")
                for g in phase["quality_gates"]:
                    lines.append(f"  - {g}")

        lines.append(f"\n**Critical Path**: {' -> '.join(exec_plan.get('critical_path', []))}")

        lines.append("\n---\n## Risk Assessment\n")
        lines.append("| Risk | Probability | Impact | Phase | Mitigation |")
        lines.append("|------|-------------|--------|-------|------------|")
        for risk in plan.get("risk_assessment", []):
            lines.append(f"| {risk.get('risk', '')} | {risk.get('probability', '')} | {risk.get('impact', '')} | {risk.get('affected_phase', '')} | {risk.get('mitigation', '')} |")

        return "\n".join(lines)
