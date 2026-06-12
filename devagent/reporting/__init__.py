"""DevAgent Reporting Package — Standardized Software Engineering Report Generation.

This package takes structured JSON output from LLM agents and renders it into
properly formatted engineering documents following IEEE and industry standards.
The LLM generates structured data; this package generates the finished report.

Modules:
  - templates:     Common formatting utilities (document control, glossary, RTM)
  - diagrams:      Mermaid diagram generators from structured data
  - ieee830:       IEEE 830-1998 Software Requirements Specification (SRS)
  - ieee1016:      IEEE 1016-2009 Software Design Description (SDD)
  - executive:     Executive Report with metrics dashboard
  - builder:       Orchestrates full multi-document report generation
"""

from .templates import (
    document_control_table, revision_history, glossary_section,
    rtm_table, section, metric_table, quality_gate_table,
)
from .diagrams import (
    class_diagram, er_diagram, dfd_level_0, dfd_level_1,
    sequence_diagram, state_machine, deployment_diagram,
    component_diagram, mermaid_block,
)
from .renderer import (
    DiagramRenderer, RenderResult, RenderBatchResult, render_design_diagrams,
)
from .ieee830 import render_srs
from .ieee1016 import render_sdd
from .executive import render_executive_report

__all__ = [
    # Templates
    "document_control_table", "revision_history", "glossary_section",
    "rtm_table", "section", "metric_table", "quality_gate_table",
    # Diagrams (Mermaid generators)
    "class_diagram", "er_diagram", "dfd_level_0", "dfd_level_1",
    "sequence_diagram", "state_machine", "deployment_diagram",
    "component_diagram", "mermaid_block",
    # Diagram Renderer (→ real PNG via kroki.io)
    "DiagramRenderer", "RenderResult", "RenderBatchResult",
    "render_design_diagrams",
    # Reports
    "render_srs", "render_sdd", "render_executive_report",
]
