"""Task specification and artifact data models.

This module contains both the lightweight dataclass TaskSpec used by CLI
and Pydantic models used as runtime contracts for agent inputs/outputs.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - provide lightweight fallback if pydantic not installed
    class BaseModel:  # very small fallback for attribute storage only
        def __init__(self, **data):
            for k, v in data.items():
                setattr(self, k, v)

        def dict(self):
            return {k: getattr(self, k) for k in self.__dict__.keys()}

    def Field(default=None, **kwargs):
        return default


@dataclass
class TaskSpec:
    """Standardized task specification parsed from CLI or API input."""
    task_type: str  # design / implement / repair / full / agentic
    input_path: str = ""
    code_path: str = ""
    tests_path: str = ""
    output_path: str = "./outputs"
    max_retry: int = 2
    verbose: bool = False
    language: str = "python"
    task_description: str = ""  # Used by agentic mode

    def validate(self) -> tuple[bool, str]:
        """Validate task spec completeness."""
        valid_types = ("design", "implement", "test", "debug", "repair", "full", "agentic")
        if self.task_type not in valid_types:
            return False, f"Invalid task type: {self.task_type}. Must be one of: {', '.join(valid_types)}."

        if self.task_type == "design" and not self.input_path:
            return False, "Design mode requires --input."

        if self.task_type == "implement" and not self.input_path:
            return False, "Implement mode requires --input."

        if self.task_type == "test" and not self.code_path:
            return False, "Test mode requires --code."

        if self.task_type == "debug" and not self.code_path:
            return False, "Debug mode requires --code."

        if self.task_type == "repair" and not self.code_path:
            return False, "Repair mode requires --code."

        if self.task_type == "full" and not self.input_path:
            return False, "Full mode requires --input."

        if self.task_type == "agentic" and not self.task_description and not self.input_path:
            return False, "Agentic mode requires --task-description or --input."

        return True, ""


class RequirementInput(BaseModel):
    """Standardized requirement input contract for agents."""
    id: str
    source: str = Field(..., description="natural_language|prd|user_story|uml|code_snippet|error_log")
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    """Artifact produced by agents and stored under outputs/."""
    id: str
    type: str = Field(..., description="e.g. design:class_diagram, code:python, test:pytest")
    format: str = Field(..., description="e.g. mermaid, plantuml, py, diff, text")
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TestReport(BaseModel):
    """Basic test report schema used to summarize pytest runs."""
    run_id: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    details: List[Dict[str, Any]] = Field(default_factory=list)

