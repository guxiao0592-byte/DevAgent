"""Shared state object for the DevAgent workflow."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from datetime import datetime


@dataclass
class AgentState:
    """Unified state object that flows through the entire workflow."""
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    task_type: str = ""  # design / implement / repair / full
    input_path: str = ""
    output_root: str = ""
    language: str = "python"  # python / java

    # Task input manifest
    input_manifest: dict = field(default_factory=dict)

    # Phase outputs
    requirements: dict = field(default_factory=dict)       # structured requirements
    design_artifacts: dict = field(default_factory=dict)    # design docs & diagrams
    code_files: list = field(default_factory=list)          # generated source paths
    test_files: list = field(default_factory=list)          # test file paths
    test_results: dict = field(default_factory=dict)        # pytest results
    debug_analysis: dict = field(default_factory=dict)      # bug location & analysis
    repair_patch: dict = field(default_factory=dict)        # patch info

    # Control fields
    retry_count: int = 0
    max_retry: int = 2
    status: str = "INIT"  # INIT -> ANALYSIS_DONE -> DESIGN_DONE -> IMPLEMENT_DONE -> TEST_DONE -> REPAIR_DONE -> FINISHED
    execution_trace: list = field(default_factory=list)
    final_report: str = ""

    # Error handling
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # Optional central artifact registry instance (set by orchestrator)
    artifact_registry: Any = field(default=None, repr=False)

    def add_trace(self, node: str, status: str, details: Optional[dict] = None):
        self.execution_trace.append({
            "node": node,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details or {}
        })

    def add_error(self, phase: str, message: str, detail: str = ""):
        self.errors.append({
            "phase": phase,
            "message": message,
            "detail": detail,
            "timestamp": datetime.now().isoformat()
        })

    def add_warning(self, phase: str, message: str):
        self.warnings.append({
            "phase": phase,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def get_output_subdir(self, phase: str) -> str:
        """Get the numbered subdirectory for a phase output."""
        prefix_map = {
            "requirements": "01_requirements",
            "design": "02_design",
            "implementation": "03_implementation",
            "tests": "04_tests",
            "repair": "05_repair",
            "reports": "06_reports",
        }
        subdir = prefix_map.get(phase, phase)
        import os
        return os.path.join(self.output_root, subdir)
