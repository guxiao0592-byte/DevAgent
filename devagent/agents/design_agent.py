"""Professional system design agent with C4-style architecture documentation.

Outputs engineering-grade design artifacts:
1. System context & container diagrams (C4 model via Mermaid)
2. Class diagram with full relationships
3. Database schema / ER diagram
4. API contract definitions (OpenAPI-style)
5. Sequence diagrams for key flows
6. Technology stack decisions with rationale
7. Module decomposition with interfaces
"""

import json
import os
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.diagram_validator import DiagramValidator
from ..tools.artifact_registry import ArtifactRegistry
from ..agent_core.schemas import Artifact as ArtifactModel


DESIGN_PROMPT = """You are a senior software architect. Given the requirements specification, produce a complete, professional system design.

=== DESIGN PROCESS ===

STEP 1 - System Context: Describe the system boundary, external actors, and integrations.

STEP 2 - Container / Module Architecture: Define the high-level modules/containers.

STEP 3 - Class Design: Design classes with attributes, methods, and full relationships (inheritance, composition, aggregation, dependency).

STEP 4 - Database Schema: Define entities, relationships, and key fields.

STEP 5 - API Contracts: Define public interfaces between modules or external-facing APIs.

STEP 6 - Key Flows: Describe critical interaction sequences.

STEP 7 - Technology Decisions: Recommend specific technologies with rationale.

=== MERMAID DIAGRAM RULES ===
- classDiagram: Use proper format — `class ClassName {`, `+method()`, `-attribute`
- erDiagram: Use `ENTITY { type field }` and `ENTITY1 ||--o{ ENTITY2 : "label"`
- sequenceDiagram: Use `Actor->>System: action`, `System-->>Actor: response`
- flowchart: Use `Start --> Process --> Decision --> End`
- Specify ONLY valid Mermaid syntax inside the code blocks.

Output ONLY valid JSON:
{
  "architecture_overview": {
    "pattern": "layered / hexagonal / microservices / etc.",
    "context_diagram_mermaid": "```mermaid\nflowchart LR\n    User-->System\n    System-->ExternalAPI\n```",
    "container_diagram_mermaid": "```mermaid\nflowchart TD\n    subgraph Presentation\n        WebApp\n    end\n    subgraph Backend\n        API\n        Service\n    end\n```",
    "key_design_decisions": ["decision 1 with rationale"]
  },
  "class_diagram_mermaid": "```mermaid\nclassDiagram\n    class User {\n        +int id\n        +string name\n        +login()\n    }\n    class Order {\n        +int id\n        +create()\n    }\n    User \"1\" --> \"*\" Order\n```",
  "er_diagram_mermaid": "```mermaid\nerDiagram\n    USER ||--o{ ORDER : places\n    USER {\n        int id PK\n        string name\n    }\n    ORDER {\n        int id PK\n        int user_id FK\n    }\n```",
  "sequence_diagrams": [
    {
      "name": "Login Flow",
      "diagram_mermaid": "```mermaid\nsequenceDiagram\n    User->>API: POST /login\n    API->>DB: verify credentials\n    DB-->>API: user object\n    API-->>User: JWT token\n```"
    }
  ],
  "module_division": [
    {
      "name": "module_name",
      "responsibility": "what this module does",
      "interfaces": ["IModuleName.method()"],
      "dependencies": ["other_module"],
      "key_classes": ["ClassName"]
    }
  ],
  "database_schema": [
    {
      "table": "table_name",
      "description": "...",
      "columns": [
        {"name": "id", "type": "INT", "constraints": "PRIMARY KEY AUTO_INCREMENT", "nullable": false}
      ],
      "indexes": ["idx_column"],
      "relationships": [
        {"type": "foreign_key", "column": "user_id", "references": "users(id)", "on_delete": "CASCADE"}
      ]
    }
  ],
  "api_contracts": [
    {
      "endpoint": "POST /api/v1/resource",
      "description": "...",
      "request_body": {"content_type": "application/json", "schema": {"field": "type"}},
      "responses": {
        "200": {"description": "success", "schema": {"field": "value"}},
        "400": {"description": "bad request"},
        "500": {"description": "server error"}
      },
      "auth_required": true
    }
  ],
  "technology_stack": {
    "language": "Python 3.11+",
    "framework": {"name": "...", "rationale": "why this framework"},
    "database": {"name": "...", "rationale": "why this database"},
    "cache": {"name": "...", "rationale": "..."},
    "message_queue": {"name": "...", "rationale": "..."},
    "deployment": {"name": "...", "rationale": "..."}
  },
  "key_interfaces": [
    {
      "name": "InterfaceName",
      "method_signature": "method(param: type) -> ReturnType",
      "description": "...",
      "module": "module_name"
    }
  ],
  "data_flow_diagrams": {
    "level_0_mermaid": "```mermaid\\nflowchart LR\\n    User([User]) -->|credentials, queries| System((System))\\n    System -->|results, tokens| User\\n    DB[(Database)] -->|data| System\\n    System -->|writes| DB\\n```",
    "level_1_mermaid": "```mermaid\\nflowchart TD\\n    User([User]) -->|input| P1[Auth Service]\\n    P1 -->|validated| P2[Business Logic]\\n    P2 -->|queries| DS1[(Database)]\\n    DS1 -->|data| P2\\n    P2 -->|results| User\\n```",
    "process_descriptions": [
      {"id": "P1", "name": "Auth Service", "description": "...", "inputs": ["credentials"], "outputs": ["token"], "data_stores": []}
    ]
  },
  "state_machine_diagrams": [
    {
      "entity": "Order",
      "diagram_mermaid": "```mermaid\\nstateDiagram-v2\\n    [*] --> Draft\\n    Draft --> Submitted: submit\\n    Submitted --> Processing: approve\\n    Processing --> Completed: ship\\n    Processing --> Cancelled: cancel\\n    Completed --> [*]\\n    Cancelled --> [*]\\n```"
    }
  ],
  "deployment_diagram_mermaid": "```mermaid\\nflowchart TD\\n    subgraph Cloud\\n        LB[Load Balancer]\\n        subgraph Cluster\\n            API1[API Pod]\\n            API2[API Pod]\\n        end\\n        DB[(Database)]\\n        Cache[(Redis)]\\n    end\\n    Client([Browser]) -->|HTTPS| LB\\n    LB --> API1\\n    LB --> API2\\n    API1 --> DB\\n    API1 --> Cache\\n```",
  "architecture_decisions": [
    {
      "id": "ADR-001",
      "title": "Use Repository Pattern for Data Access",
      "status": "Accepted",
      "context": "Need to isolate domain logic from database specifics",
      "decision": "All data access goes through repository interfaces",
      "consequences": "Easier testing via mock repositories; added abstraction layer",
      "alternatives": [
        {"name": "Direct ORM in services", "reason_rejected": "Tight coupling, hard to test"}
      ]
    }
  ],
  "threat_model": [
    {
      "component": "Auth Service",
      "spoofing": {"risk": "Medium", "mitigation": "JWT with expiration + refresh tokens"},
      "tampering": {"risk": "Low", "mitigation": "HTTPS + request signing"},
      "repudiation": {"risk": "Medium", "mitigation": "Audit log for all auth events"},
      "information_disclosure": {"risk": "High", "mitigation": "Hash passwords; never log tokens"},
      "denial_of_service": {"risk": "Medium", "mitigation": "Rate limiting on login endpoint"},
      "elevation_of_privilege": {"risk": "High", "mitigation": "Role-based access control"}
    }
  ],
  "activity_diagrams": [
    {
      "name": "User Registration Flow",
      "diagram_plantuml": "@startuml\\nstart\\n:User submits registration form;\\nif (Validation passes?) then (yes)\\n  :Create account in database;\\n  :Send verification email;\\n  :Display success page;\\nelse (no)\\n  :Show validation errors;\\n  :Return to form;\\nendif\\nstop\\n@enduml",
      "nodes": [
        {"id": "A1", "label": "User submits form", "type": "action"},
        {"id": "D1", "label": "Validation passes?", "type": "decision", "branches": [{"label": "yes", "to": "A2"}, {"label": "no", "to": "A3"}]},
        {"id": "A2", "label": "Create account", "type": "action"},
        {"id": "A3", "label": "Show errors", "type": "action"},
        {"id": "A4", "label": "Send email", "type": "action"}
      ]
    }
  ],
  "usecase_diagram_plantuml": "@startuml\\nleft to right direction\\nactor Customer\\nactor Admin\\nrectangle \\"System\\" {\\n  usecase \\"Place Order\\" as UC1\\n  usecase \\"Track Order\\" as UC2\\n  usecase \\"Manage Products\\" as UC3\\n}\\nCustomer --> UC1\\nCustomer --> UC2\\nAdmin --> UC3\\n@enduml"
}

===== IMPORTANT =====
Be specific and concrete. Use real class names, method signatures, and types.
Every diagram must contain valid Mermaid syntax only — no explanatory text inside mermaid blocks.
Every design decision must cite the requirement ID [FR-XX] or [NFR-XX] it addresses.
Generate ALL diagram types: context, container, class, ER, sequence, DFD Level 0+1, state machine, deployment.
For each key technology choice, generate an Architecture Decision Record (ADR).
Include a STRIDE threat model for security-critical components."""


class DesignAgent(BaseAgent):
    """Agent responsible for generating professional system design artifacts."""

    def __init__(self, llm_client, config=None):
        super().__init__(llm_client, config)
        self.validator = DiagramValidator()
        # artifact registry will be initialized per run when state is available
        self._artifact_registry = None

    def run(self, state: AgentState) -> AgentState:
        """Generate professional design models from requirements."""
        requirements = state.requirements

        if not requirements:
            state.add_error("design", "No requirements available for design generation")
            state.status = "DESIGN_DONE"
            return state

        result = self.llm.chat_structured(
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a complete professional system design based on these requirements:\n\n"
                    f"{json.dumps(requirements, indent=2, ensure_ascii=False)}"
                )
            }],
            system_prompt=DESIGN_PROMPT
        )

        # Validate diagrams with retry
        result = self._validate_diagrams(result, requirements, state)

        state.design_artifacts = result

        # Prefer central artifact registry mounted on state, otherwise create a local one
        reg = self.get_artifact_registry(state)
        if reg is None:
            out_root = state.output_root or state.input_path or "outputs"
            reg = ArtifactRegistry(out_root)
        self._artifact_registry = reg

        # Generate professional markdown document via reporting package
        from ..reporting.ieee1016 import render_sdd
        md_content = render_sdd(result, requirements=state.requirements,
                               task_id=state.task_id)
        art = ArtifactModel(
            id=f"design_md_{state.task_id}",
            type="design:spec",
            format="md",
            content=md_content,
            metadata={"generated_by": "DesignAgent", "provenance": [state.task_id]},
        )
        self._artifact_registry.register_from_state(state, "design", art)

        # Save individual diagrams via registry
        self._save_diagrams(result, state)

        # Save domain model / ER / API contracts separately as JSON artifacts
        db_art = ArtifactModel(
            id=f"db_schema_{state.task_id}",
            type="design:database_schema",
            format="json",
            content=json.dumps({"tables": result.get("database_schema", [])}, ensure_ascii=False, indent=2),
            metadata={"generated_by": "DesignAgent", "provenance": [state.task_id]},
        )
        self._artifact_registry.register_from_state(state, "design", db_art)

        api_art = ArtifactModel(
            id=f"api_contracts_{state.task_id}",
            type="design:api_contracts",
            format="json",
            content=json.dumps(result.get("api_contracts", []), ensure_ascii=False, indent=2),
            metadata={"generated_by": "DesignAgent", "provenance": [state.task_id]},
        )
        self._artifact_registry.register_from_state(state, "design", api_art)

        tech_art = ArtifactModel(
            id=f"tech_stack_{state.task_id}",
            type="design:technology_stack",
            format="json",
            content=json.dumps(result.get("technology_stack", {}), ensure_ascii=False, indent=2),
            metadata={"generated_by": "DesignAgent", "provenance": [state.task_id]},
        )
        self._artifact_registry.register_from_state(state, "design", tech_art)

        # === Render all diagrams to real PNG images via kroki.io ===
        design_output_dir = state.get_output_subdir("design")
        from ..reporting.renderer import render_design_diagrams as _render
        try:
            batch = _render(result, design_output_dir)
            if batch.success_count > 0:
                print(f"[DesignAgent] Rendered {batch.success_count}/{batch.total_count} "
                      f"diagrams to {design_output_dir}/diagrams/", flush=True)
                # PNGs are saved directly in diagrams/ — no ArtifactRegistry
                # registration needed (Artifact.content is str, cannot store binary)
            else:
                print(f"[DesignAgent] ⚠ No diagrams rendered", flush=True)
        except Exception as e:
            state.add_warning("design", f"Diagram rendering failed: {e}")

        # Save activity diagrams as PlantUML
        activity_diagrams = result.get("activity_diagrams", [])
        for i, ad in enumerate(activity_diagrams, 1):
            pu_content = ad.get("diagram_plantuml", "")
            if pu_content:
                art = ArtifactModel(
                    id=f"activity_diagram_{i}_{state.task_id}",
                    type="design:activity_diagram", format="plantuml",
                    content=pu_content,
                    metadata={"generated_by": "DesignAgent", "provenance": [state.task_id],
                              "filename": f"activity_diagram_{i}.puml"}
                )
                self._artifact_registry.register_from_state(state, "design", art)
            # Also save structured nodes
            nodes = ad.get("nodes", [])
            if nodes:
                from ..reporting.diagrams import activity_diagram as gen_activity
                from ..reporting.renderer import DiagramRenderer
                try:
                    gen_pu = gen_activity(ad.get("name", f"Activity {i}"), nodes)
                    art = ArtifactModel(
                        id=f"activity_gen_{i}_{state.task_id}",
                        type="design:activity_diagram", format="plantuml",
                        content=gen_pu,
                        metadata={"generated_by": "diagrams.activity_diagram",
                                  "provenance": [state.task_id],
                                  "filename": f"activity_diagram_{i}_gen.puml"}
                    )
                    self._artifact_registry.register_from_state(state, "design", art)
                    # Render to PNG
                    renderer = DiagramRenderer(design_output_dir)
                    renderer.render_plantuml(gen_pu, f"activity_{i}")
                except Exception:
                    pass

        # Save use case diagram
        uc_puml = result.get("usecase_diagram_plantuml", "")
        if uc_puml:
            art = ArtifactModel(
                id=f"usecase_diagram_{state.task_id}",
                type="design:usecase_diagram", format="plantuml",
                content=uc_puml,
                metadata={"generated_by": "DesignAgent", "provenance": [state.task_id],
                          "filename": "usecase_diagram.puml"}
            )
            self._artifact_registry.register_from_state(state, "design", art)
            try:
                renderer = DiagramRenderer(design_output_dir)
                renderer.render_plantuml(uc_puml, "usecase_diagram")
            except Exception:
                pass

        state.status = "DESIGN_DONE"
        diagram_fields = ["class_diagram_mermaid", "er_diagram_mermaid",
                         "activity_diagrams", "usecase_diagram_plantuml"]
        generated = [k for k in diagram_fields if result.get(k)]
        seq_count = len(result.get("sequence_diagrams", []))
        module_count = len(result.get("module_division", []))
        state.add_trace(
            "DesignAgent",
            "completed",
            {
                "diagrams_generated": generated,
                "sequence_diagrams": seq_count,
                "modules": module_count,
            },
        )

        return state

    def _validate_diagrams(self, result: dict, requirements: dict, state: AgentState) -> dict:
        """Validate Mermaid diagrams and retry if needed."""
        max_retry = self.config.get("diagram_retry", 2) if self.config else 2
        diagram_fields = ["class_diagram_mermaid", "er_diagram_mermaid"]
        sequence_diagrams = result.get("sequence_diagrams", [])

        for attempt in range(max_retry):
            all_valid = True
            errors = []

            for field in diagram_fields:
                content = result.get(field, "")
                if content:
                    clean = self._clean_mermaid(content)
                    valid, msg = self.validator.validate(clean, "mermaid")
                    if not valid:
                        all_valid = False
                        errors.append(f"- {field}: {msg}")

            for i, sd in enumerate(sequence_diagrams):
                content = sd.get("diagram_mermaid", "")
                if content:
                    clean = self._clean_mermaid(content)
                    valid, msg = self.validator.validate(clean, "mermaid")
                    if not valid:
                        all_valid = False
                        errors.append(f"- sequence_diagrams[{i}]: {msg}")

            if all_valid or attempt >= max_retry - 1:
                break

            error_feedback = "Diagram validation failed. Fix syntax:\n" + "\n".join(errors)
            result = self.llm.chat_structured(
                messages=[
                    {"role": "user", "content": f"Generate system design:\n\n{json.dumps(requirements, indent=2, ensure_ascii=False)}"},
                    {"role": "assistant", "content": f"Previous attempt had issues:\n{error_feedback}\nPlease regenerate with correct syntax."}
                ],
                system_prompt=DESIGN_PROMPT
            )
            sequence_diagrams = result.get("sequence_diagrams", [])

        return result

    def _save_diagrams(self, result: dict, state: AgentState):
        """Save individual diagram files."""
        diagram_map = [
            ("class_diagram_mermaid", "class_diagram.mmd"),
            ("er_diagram_mermaid", "er_diagram.mmd"),
        ]
        for field, filename in diagram_map:
            content = result.get(field, "")
            if content:
                clean = self._clean_mermaid(content)
                art = ArtifactModel(id=f"{field}_{state.task_id}", type=f"design:{field}", format="mermaid",
                                    content=clean,
                                    metadata={"generated_by": "DesignAgent", "provenance": [state.task_id], "filename": filename})
                try:
                    self._artifact_registry.register_from_state(state, "design", art)
                except Exception:
                    # Fallback to file writes
                    self._save_artifact(state, "design", filename, clean)

        for i, sd in enumerate(result.get("sequence_diagrams", []), 1):
            content = sd.get("diagram_mermaid", "")
            if content:
                clean = self._clean_mermaid(content)
                filename = f"sequence_diagram_{i}.mmd"
                art = ArtifactModel(id=f"sequence_{i}_{state.task_id}", type="design:sequence_diagram", format="mermaid",
                                    content=clean,
                                    metadata={"generated_by": "DesignAgent", "provenance": [state.task_id], "filename": filename})
                try:
                    self._artifact_registry.register_from_state(state, "design", art)
                except Exception:
                    self._save_artifact(state, "design", filename, clean)

    @staticmethod
    def _clean_mermaid(content: str) -> str:
        """Extract clean Mermaid content from markdown code blocks."""
        if "```mermaid" in content:
            content = content.split("```mermaid")[1]
            if "```" in content:
                content = content.split("```")[0]
        elif "```" in content:
            content = content.split("```")[1]
            if "```" in content:
                content = content.split("```")[0]
        return content.strip()

    @staticmethod
    def _generate_professional_markdown(design: dict) -> str:
        """Generate professional markdown architecture document."""
        lines = []
        lines.append("# Architecture Design Specification\n")

        # Architecture Overview
        arch = design.get("architecture_overview", {})
        lines.append("## 1. Architecture Overview\n")
        lines.append(f"**Pattern**: {arch.get('pattern', 'N/A')}\n")
        lines.append(f"{arch.get('context_diagram_mermaid', '')}\n")
        lines.append(f"{arch.get('container_diagram_mermaid', '')}\n")
        lines.append("**Key Design Decisions**:")
        for d in arch.get("key_design_decisions", []):
            lines.append(f"- {d}")

        # Module Division
        lines.append("\n---\n## 2. Module Decomposition\n")
        for module in design.get("module_division", []):
            lines.append(f"### {module.get('name', '')}")
            lines.append(f"- **Responsibility**: {module.get('responsibility', '')}")
            lines.append(f"- **Dependencies**: {', '.join(module.get('dependencies', []))}")
            ifaces = module.get("interfaces", [])
            if ifaces:
                lines.append(f"- **Interfaces**:")
                for iface in ifaces:
                    lines.append(f"  - `{iface}`")
            classes = module.get("key_classes", [])
            if classes:
                lines.append(f"- **Key Classes**: `{'`, `'.join(classes)}`")

        # Class Diagram
        lines.append("\n---\n## 3. Class Diagram\n")
        cd = design.get("class_diagram_mermaid", "")
        if cd:
            lines.append("```mermaid")
            lines.append(DesignAgent._clean_mermaid(cd))
            lines.append("```\n")

        # ER Diagram / Database Schema
        lines.append("---\n## 4. Database Design\n")
        erd = design.get("er_diagram_mermaid", "")
        if erd:
            lines.append("```mermaid")
            lines.append(DesignAgent._clean_mermaid(erd))
            lines.append("```\n")

        lines.append("### Tables\n")
        for table in design.get("database_schema", []):
            lines.append(f"#### {table.get('table', '')}")
            lines.append(f"- Description: {table.get('description', '')}")
            lines.append("\n  | Column | Type | Constraints | Nullable |")
            lines.append("  |--------|------|-------------|----------|")
            for col in table.get("columns", []):
                nullable = "YES" if col.get("nullable") else "NO"
                lines.append(f"  | {col.get('name', '')} | {col.get('type', '')} | {col.get('constraints', '')} | {nullable} |")
            if table.get("indexes"):
                lines.append(f"\n  Indexes: `{'`, `'.join(table['indexes'])}`")
            for rel in table.get("relationships", []):
                lines.append(f"\n  FK: {rel.get('column', '')} -> {rel.get('references', '')} (ON DELETE {rel.get('on_delete', '')})")

        # Sequence Diagrams
        seq_diagrams = design.get("sequence_diagrams", [])
        if seq_diagrams:
            lines.append("\n---\n## 5. Sequence Diagrams\n")
            for sd in seq_diagrams:
                lines.append(f"### {sd.get('name', '')}\n")
                content = sd.get("diagram_mermaid", "")
                if content:
                    lines.append("```mermaid")
                    lines.append(DesignAgent._clean_mermaid(content))
                    lines.append("```\n")

        # API Contracts
        lines.append("---\n## 6. API Contracts\n")
        for api in design.get("api_contracts", []):
            auth = "🔒 Auth Required" if api.get("auth_required") else "🔓 Public"
            lines.append(f"### {api.get('endpoint', '')} {auth}")
            lines.append(f"- Description: {api.get('description', '')}")
            req_body = api.get("request_body", {})
            if req_body:
                lines.append(f"\n  **Request**: `{req_body.get('content_type', '')}`")
                if req_body.get("schema"):
                    lines.append(f"  ```json\n  {json.dumps(req_body['schema'], indent=4)}\n  ```")
            lines.append("\n  **Responses**:")
            for code, resp in api.get("responses", {}).items():
                lines.append(f"  - **{code}**: {resp.get('description', '')}")

        # Technology Stack
        ts = design.get("technology_stack", {})
        if ts:
            lines.append("\n---\n## 7. Technology Stack\n")
            for category, value in ts.items():
                if isinstance(value, dict):
                    lines.append(f"- **{category}**: {value.get('name', '')}")
                    if value.get("rationale"):
                        lines.append(f"  - Rationale: {value['rationale']}")
                elif value:
                    lines.append(f"- **{category}**: {value}")

        # Key Interfaces
        lines.append("\n---\n## 8. Key Interfaces\n")
        for iface in design.get("key_interfaces", []):
            lines.append(f"- **{iface.get('name', '')}** (`{iface.get('module', '')}`)")
            lines.append(f"  - Signature: `{iface.get('method_signature', '')}`")
            lines.append(f"  - Description: {iface.get('description', '')}")

        return "\n".join(lines)
