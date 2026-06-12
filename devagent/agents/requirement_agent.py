"""Professional requirement analysis agent with multi-pass analysis.

Produces engineering-grade requirements documentation:
1. Domain model & entity extraction with data dictionary
2. Structured functional / non-functional requirements
3. Use case specifications with full flow definitions
4. User story mapping with acceptance criteria
5. Risk assessment and assumptions
"""

import json
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.artifact_registry import ArtifactRegistry


REQUIREMENT_PROMPT = """You are a senior requirements engineer delivering a professional requirements specification for a small to medium software project.

Perform a thorough multi-pass analysis:

=== PASS 1: DOMAIN MODEL ===
Identify the core business domain, key entities, their attributes (with types and constraints), and relationships.

=== PASS 2: FUNCTIONAL REQUIREMENTS ===
Extract all functional requirements. Each must have:
- ID (FR-01, FR-02, ...), Name, Description
- Priority (critical/high/medium/low)
- Dependencies (references to other FRs)
- Actor(s) involved
- Acceptance criteria (measurable and testable)

=== PASS 3: NON-FUNCTIONAL REQUIREMENTS ===
Cover: performance, security, scalability, usability, reliability, maintainability.

=== PASS 4: USE CASES ===
For each major feature, provide:
- Name, Actor(s), Preconditions, Main flow (numbered steps)
- Alternative flows / extensions, Postconditions

=== PASS 5: RISK ASSESSMENT ===
Identify technical risks, mitigation strategies, and assumptions.

Output ONLY valid JSON:
{
  "project_summary": {
    "name": "project name",
    "description": "concise paragraph describing the project",
    "target_users": "who will use this system",
    "business_goals": ["list of business objectives"]
  },
  "domain_model": {
    "entities": [
      {
        "name": "EntityName",
        "description": "...",
        "attributes": [
          {"name": "id", "type": "int", "description": "unique identifier", "constraints": "PK, auto-increment"}
        ],
        "relationships": [
          {"target": "OtherEntity", "type": "one-to-many", "description": "..."}
        ]
      }
    ]
  },
  "actors": [
    {
      "id": "ACT-01",
      "name": "Role Name",
      "description": "...",
      "goals": ["goal1"]
    }
  ],
  "functional_requirements": [
    {
      "id": "FR-01",
      "name": "Requirement Name",
      "description": "...",
      "priority": "high",
      "actor_ids": ["ACT-01"],
      "dependencies": [],
      "acceptance_criteria": ["AC1: ..."]
    }
  ],
  "nonfunctional_requirements": [
    {
      "id": "NFR-01",
      "category": "performance|security|usability|reliability|maintainability",
      "name": "...",
      "description": "...",
      "target_metric": "measurable target if applicable"
    }
  ],
  "use_cases": [
    {
      "id": "UC-01",
      "name": "Use Case Name",
      "actors": ["ACT-01"],
      "preconditions": ["..."],
      "main_flow": ["step 1", "step 2"],
      "alternative_flows": [
        {"condition": "if error", "flow": ["step a", "step b"]}
      ],
      "postconditions": ["..."],
      "business_rules": ["rule 1"]
    }
  ],
  "constraints": [
    {"type": "technical|business|regulatory", "description": "..."}
  ],
  "risk_assessment": [
    {
      "risk": "description",
      "probability": "high/medium/low",
      "impact": "high/medium/low",
      "mitigation": "...",
      "contingency": "..."
    }
  ],
  "assumptions": ["assumption 1"],
  "security_requirements": [
    {
      "id": "NFR-SEC-01",
      "category": "authentication",
      "description": "All API endpoints must require valid JWT authentication",
      "target_metric": "0 unauthenticated requests succeed",
      "owasp_category": "A2: Broken Authentication"
    },
    {
      "id": "NFR-SEC-02",
      "category": "data_protection",
      "description": "All passwords hashed with bcrypt (work factor >= 12)",
      "target_metric": "No plaintext passwords in storage or logs",
      "owasp_category": "A3: Sensitive Data Exposure"
    },
    {
      "id": "NFR-SEC-03",
      "category": "input_validation",
      "description": "Validate and sanitize all user inputs; use parameterized queries",
      "target_metric": "0 SQL injection or XSS vulnerabilities",
      "owasp_category": "A1: Injection"
    }
  ],
  "observability_requirements": [
    {
      "id": "NFR-OBS-01",
      "category": "logging",
      "description": "Structured JSON logging with request_id propagation",
      "target_metric": "Every request has traceable log entries from entry to exit"
    },
    {
      "id": "NFR-OBS-02",
      "category": "health_check",
      "description": "/health endpoint returning service + dependency status",
      "target_metric": "Response within 1 second, includes DB/cache/queue status"
    },
    {
      "id": "NFR-OBS-03",
      "category": "metrics",
      "description": "Prometheus metrics: request count, latency (p50/p95/p99), error rate",
      "target_metric": "Metrics available at /metrics endpoint"
    }
  ],
  "glossary": [
    {"term": "JWT", "definition": "JSON Web Token — compact, URL-safe token for authentication"},
    {"term": "FR", "definition": "Functional Requirement"}
  ]
}

IMPORTANT: Be thorough and specific. Each requirement must be actionable, measurable, and testable. Every FR MUST have acceptance criteria. Every NFR MUST have a target metric. Include security (OWASP-aligned) and observability requirements. Use professional language suitable for a real project specification document."""


class RequirementAgent(BaseAgent):
  """Agent responsible for professional requirements analysis."""

  def run(self, state: AgentState) -> AgentState:
    """Extract structured, engineering-grade requirements from input."""
    try:
      raw_input = self.file_tool.read_text(state.input_path)
    except FileNotFoundError:
      state.add_error("requirements", f"Input file not found: {state.input_path}")
      state.status = "ANALYSIS_DONE"
      return state

    truncated = self._truncate_text(raw_input, max_chars=8000)
    result = self.llm.chat_structured(
      messages=[{
        "role": "user",
        "content": (
          f"Analyze the following project input and produce a comprehensive requirements specification "
          f"going through all analysis passes. Be thorough and specific.\n\n"
          f"---INPUT---\n{truncated}\n---END---"
        )
      }],
      system_prompt=REQUIREMENT_PROMPT
    )

    state.requirements = result

    # Generate professional markdown document via reporting package
    from ..reporting.ieee830 import render_srs
    md_content = render_srs(result, task_id=state.task_id)

    # Ensure registry and register artifacts
    out_root = state.output_root or state.input_path or "outputs"
    registry = ArtifactRegistry(out_root)
    from ..agent_core.schemas import Artifact as ArtifactModel

    md_artifact = ArtifactModel(
      id="",
      type="requirements:spec",
      format="md",
      content=md_content,
      metadata={"filename": "requirement_specification.md"},
    )
    registry.register_from_state(state, "requirements", md_artifact)

    json_artifact = ArtifactModel(
      id="",
      type="requirements:structured",
      format="json",
      content=json.dumps(result, ensure_ascii=False, indent=2),
      metadata={"filename": "structured_requirements.json"},
    )
    registry.register_from_state(state, "requirements", json_artifact)

    fr_count = len(result.get("functional_requirements", []))
    uc_count = len(result.get("use_cases", []))
    entity_count = len(result.get("domain_model", {}).get("entities", []))

    state.status = "ANALYSIS_DONE"
    state.add_trace(
      "RequirementAgent",
      "completed",
      {
        "functional_requirements": fr_count,
        "use_cases": uc_count,
        "domain_entities": entity_count,
      },
    )

    return state

  def _generate_professional_markdown(self, req: dict) -> str:
    """Generate a professional requirements specification document."""
    lines = []
    lines.append("# Requirements Specification\n")
    lines.append("## 1. Project Overview\n")
    summary = req.get("project_summary", {})
    lines.append(f"**Project**: {summary.get('name', 'N/A')}")
    lines.append(f"\n**Description**: {summary.get('description', 'N/A')}")
    lines.append(f"\n**Target Users**: {summary.get('target_users', 'N/A')}")
    lines.append("\n**Business Goals**:")
    for g in summary.get("business_goals", []):
        lines.append(f"- {g}")

    # Domain Model
    lines.append("\n---\n## 2. Domain Model\n")
    for ent in req.get("domain_model", {}).get("entities", []):
        lines.append(f"### {ent.get('name', '')}")
        lines.append(f"- Description: {ent.get('description', '')}")
        lines.append("\n  Attributes:")
        for attr in ent.get("attributes", []):
            nullable = "nullable" if attr.get("nullable") else "required"
            lines.append(f"  - `{attr.get('name')}` (`{attr.get('type')}`, {nullable}): {attr.get('description', '')}")
        lines.append("\n  Relationships:")
        for rel in ent.get("relationships", []):
            lines.append(f"  - {rel.get('type', '')} -> **{rel.get('target', '')}**: {rel.get('description', '')}")

    # Actors
    lines.append("\n---\n## 3. Actors\n")
    for actor in req.get("actors", []):
        lines.append(f"### {actor.get('id', '')}: {actor.get('name', '')}")
        lines.append(f"- Description: {actor.get('description', '')}")
        lines.append("- Goals:")
        for g in actor.get("goals", []):
            lines.append(f"  - {g}")

    # Functional Requirements
    lines.append("\n---\n## 4. Functional Requirements\n")
    for fr in req.get("functional_requirements", []):
        priority_tag = {"critical": "[CRITICAL]", "high": "[HIGH]", "medium": "[MEDIUM]", "low": "[LOW]"}
        tag = priority_tag.get(fr.get("priority", "medium"), "[MEDIUM]")
        lines.append(f"\n### {fr.get('id', '')} {tag} {fr.get('name', '')}")
        lines.append(f"- **Description**: {fr.get('description', '')}")
        lines.append(f"- **Priority**: {fr.get('priority', 'medium')}")
        lines.append(f"- **Actors**: {', '.join(fr.get('actor_ids', []))}")
        deps = fr.get("dependencies", [])
        if deps:
            lines.append(f"- **Dependencies**: {', '.join(deps)}")
        lines.append("\n  **Acceptance Criteria**:")
        for ac in fr.get("acceptance_criteria", []):
            lines.append(f"  - [ ] {ac}")

    # Non-functional Requirements
    lines.append("\n---\n## 5. Non-Functional Requirements\n")
    for nfr in req.get("nonfunctional_requirements", []):
        lines.append(f"\n### {nfr.get('id', '')}: {nfr.get('name', '')}")
        lines.append(f"- Category: {nfr.get('category', '')}")
        lines.append(f"- Description: {nfr.get('description', '')}")
        if nfr.get("target_metric"):
            lines.append(f"- Target: {nfr['target_metric']}")

    # Use Cases
    lines.append("\n---\n## 6. Use Cases\n")
    for uc in req.get("use_cases", []):
        lines.append(f"\n### {uc.get('id', '')}: {uc.get('name', '')}")
        lines.append(f"- Actors: {', '.join(uc.get('actors', []))}")
        lines.append("\n  **Preconditions**:")
        for pc in uc.get("preconditions", []):
            lines.append(f"  - {pc}")
        lines.append("\n  **Main Flow**:")
        for i, step in enumerate(uc.get("main_flow", []), 1):
            lines.append(f"  {i}. {step}")
        if uc.get("alternative_flows"):
            lines.append("\n  **Alternative Flows**:")
            for af in uc.get("alternative_flows", []):
                lines.append(f"  - When {af.get('condition', '')}:")
                for s in af.get("flow", []):
                    lines.append(f"    - {s}")
        lines.append("\n  **Postconditions**:")
        for pc in uc.get("postconditions", []):
            lines.append(f"  - {pc}")
        if uc.get("business_rules"):
            lines.append("\n  **Business Rules**:")
            for br in uc["business_rules"]:
                lines.append(f"  - {br}")

    # Constraints
    lines.append("\n---\n## 7. Constraints\n")
    for c in req.get("constraints", []):
        lines.append(f"- **{c.get('type', '')}**: {c.get('description', '')}")

    # Risk Assessment
    lines.append("\n---\n## 8. Risk Assessment\n")
    lines.append("| Risk | Probability | Impact | Mitigation | Contingency |")
    lines.append("|------|-------------|--------|------------|-------------|")
    for risk in req.get("risk_assessment", []):
        lines.append(f"| {risk.get('risk', '')} | {risk.get('probability', '')} | {risk.get('impact', '')} | {risk.get('mitigation', '')} | {risk.get('contingency', '')} |")

    # Assumptions
    lines.append("\n---\n## 9. Assumptions\n")
    for a in req.get("assumptions", []):
        lines.append(f"- {a}")

    return "\n".join(lines)
