"""Diagram IR (Intermediate Representation) — structured diagram data model.

Instead of LLM generating raw Mermaid text (fragile), LLM outputs structured JSON
that maps to this IR schema. The compiler then produces valid Mermaid/PlantUML/Graphviz.

Supported diagram types and their IR schemas:
  - component_diagram  → Mermaid flowchart
  - sequence_diagram   → Mermaid sequenceDiagram
  - class_diagram      → Mermaid classDiagram
  - er_diagram         → Mermaid erDiagram
  - state_diagram      → Mermaid stateDiagram
  - flowchart          → Mermaid flowchart
  - deployment_diagram → Mermaid flowchart
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


# ============================================================================
# IR Node & Edge Primitives
# ============================================================================

class IRNode(BaseModel):
    """A node in any diagram type."""
    id: str = Field(..., description="Unique node identifier")
    label: str = Field(..., description="Display label")
    kind: Optional[str] = Field(default=None, description="Node kind: service, datastore, actor, etc.")
    description: str = Field(default="", description="Optional description")


class IREdge(BaseModel):
    """An edge/relationship between two nodes."""
    from_node: str = Field(..., alias="from", description="Source node ID")
    to_node: str = Field(..., alias="to", description="Target node ID")
    label: str = Field(default="", description="Edge label")
    type: str = Field(default="solid", description="Line type: solid, dashed, dotted")


# ============================================================================
# Diagram IR Schemas
# ============================================================================

class ComponentDiagramIR(BaseModel):
    """IR for component/architecture diagrams."""
    type: Literal["component_diagram"] = "component_diagram"
    title: str = ""
    description: str = ""
    nodes: list[IRNode] = Field(default_factory=list)
    edges: list[IREdge] = Field(default_factory=list)
    target_dsl: Literal["mermaid", "plantuml"] = "mermaid"


class SequenceDiagramIR(BaseModel):
    """IR for sequence/interaction diagrams."""
    type: Literal["sequence_diagram"] = "sequence_diagram"
    title: str = ""
    participants: list[str] = Field(default_factory=list)
    steps: list[dict] = Field(default_factory=list, description="""
        Each step: { from, to, action, type: request|response|note }
    """)
    target_dsl: Literal["mermaid"] = "mermaid"


class ClassDiagramIR(BaseModel):
    """IR for class/entity relationship diagrams."""
    type: Literal["class_diagram"] = "class_diagram"
    title: str = ""
    modules: list[dict] = Field(default_factory=list, description="""
        [{ name, classes: [{ name, attributes: [{name,type}], methods: [{name,returns}] }] }]
    """)
    relationships: list[dict] = Field(default_factory=list, description="""
        [{ from, to, type: inheritance|composition|aggregation|dependency|one-to-many|...,
           label: optional }]
    """)
    target_dsl: Literal["mermaid", "plantuml"] = "mermaid"


class ERDiagramIR(BaseModel):
    """IR for entity-relationship / database schema diagrams."""
    type: Literal["er_diagram"] = "er_diagram"
    title: str = ""
    tables: list[dict] = Field(default_factory=list, description="""
        [{ table, columns: [{ name, type, constraints }],
           relationships: [{ type, column, references, cardinality }] }]
    """)
    target_dsl: Literal["mermaid"] = "mermaid"


class StateDiagramIR(BaseModel):
    """IR for state machine diagrams."""
    type: Literal["state_diagram"] = "state_diagram"
    title: str = ""
    entity_name: str = ""
    transitions: list[dict] = Field(default_factory=list, description="""
        [{ from, to, trigger, guard }]
    """)
    target_dsl: Literal["mermaid"] = "mermaid"


class FlowchartIR(BaseModel):
    """IR for general flowchart / activity diagrams."""
    type: Literal["flowchart"] = "flowchart"
    title: str = ""
    nodes: list[dict] = Field(default_factory=list, description="""
        [{ id, label, type: action|decision|fork|start|end }]
    """)
    edges: list[IREdge] = Field(default_factory=list)
    target_dsl: Literal["mermaid"] = "mermaid"


class DeploymentDiagramIR(BaseModel):
    """IR for deployment / infrastructure topology diagrams."""
    type: Literal["deployment_diagram"] = "deployment_diagram"
    title: str = ""
    nodes: list[dict] = Field(default_factory=list, description="""
        [{ name, type: infra|service|datastore|external, contains: [names], connects_to: [{target, protocol}] }]
    """)
    target_dsl: Literal["mermaid"] = "mermaid"


# Union of all IR types
DiagramIR = (
    ComponentDiagramIR | SequenceDiagramIR | ClassDiagramIR |
    ERDiagramIR | StateDiagramIR | FlowchartIR | DeploymentDiagramIR
)
