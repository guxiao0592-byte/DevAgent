"""Unified Action & Tool Parameter Schemas.

Defines Pydantic models for every tool parameter and Agent action output.
Used by ActionValidator and StructuredOutput to ensure stable, validated execution.

Design:
  - Every tool must have a ToolParams subclass with type-safe fields.
  - AgentAction is the top-level output: thought + tool + params.
  - ModelCapabilities declares what a model can do (for adapter selection).
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


# ============================================================================
# Model Capabilities
# ============================================================================

class ModelCapabilities(BaseModel):
    """Declare what a specific model provider/model supports."""
    supports_function_calling: bool = False
    supports_json_mode: bool = False
    supports_vision: bool = False
    max_context_tokens: int = 8192
    reliable_schema_output: bool = False

    # Which strategy to use for structured output
    preferred_strategy: Literal["function_calling", "json_mode", "text_parse", "auto"] = "auto"


# Pre-defined capability profiles
CAPABILITY_PROFILES = {
    "gpt-4o": ModelCapabilities(
        supports_function_calling=True, supports_json_mode=True,
        supports_vision=True, max_context_tokens=128000,
        reliable_schema_output=True, preferred_strategy="function_calling",
    ),
    "gpt-4": ModelCapabilities(
        supports_function_calling=True, supports_json_mode=True,
        supports_vision=False, max_context_tokens=8192,
        reliable_schema_output=True, preferred_strategy="function_calling",
    ),
    "claude-sonnet-4-6": ModelCapabilities(
        supports_function_calling=True, supports_json_mode=True,
        supports_vision=True, max_context_tokens=200000,
        reliable_schema_output=True, preferred_strategy="function_calling",
    ),
    "deepseek-chat": ModelCapabilities(
        supports_function_calling=False, supports_json_mode=True,
        supports_vision=False, max_context_tokens=65536,
        reliable_schema_output=False, preferred_strategy="text_parse",
    ),
    "deepseek-reasoner": ModelCapabilities(
        supports_function_calling=False, supports_json_mode=False,
        supports_vision=False, max_context_tokens=65536,
        reliable_schema_output=False, preferred_strategy="text_parse",
    ),
}


# ============================================================================
# Agent Action — the top-level structured output
# ============================================================================

class AgentAction(BaseModel):
    """A single agent decision: think, then act."""
    thought: str = Field(default="", description="Agent's reasoning")
    tool: str = Field(default="", description="Tool name to invoke")
    params: dict = Field(default_factory=dict, description="Tool parameters")

    @field_validator("tool")
    @classmethod
    def tool_must_not_be_empty_if_present(cls, v: str) -> str:
        if v and not v.strip():
            raise ValueError("Tool name cannot be empty string")
        return v.strip()


# ============================================================================
# Tool Parameter Schemas
# ============================================================================

# --- File Operations ---

class FileReadParams(BaseModel):
    path: str = Field(..., description="Absolute or relative file path")
    offset: int = Field(default=0, ge=0, description="Start line (0-indexed)")
    limit: int = Field(default=2000, ge=1, le=5000, description="Max lines to read")

class FileEditParams(BaseModel):
    path: str = Field(...)
    old_string: str = Field(..., min_length=1)
    new_string: str = Field(default="")
    replace_all: bool = Field(default=False)

class FileWriteParams(BaseModel):
    path: str = Field(...)
    content: str = Field(...)

class FileListParams(BaseModel):
    path: str = Field(default=".")
    depth: int = Field(default=3, ge=1, le=10)
    pattern: str = Field(default="*")

# --- Search ---

class GrepTextParams(BaseModel):
    pattern: str = Field(..., min_length=1, description="Regex pattern")
    path: str = Field(default=".")
    include: str = Field(default="", description="File glob filter")
    context_lines: int = Field(default=2, ge=0, le=10)

class GrepASTParams(BaseModel):
    query_type: Literal["functions", "classes", "imports", "calls", "assignments"] = Field(default="functions")
    path: str = Field(default=".")

class FindSymbolParams(BaseModel):
    symbol: str = Field(..., min_length=1)
    path: str = Field(default=".")

# --- Execution ---

class ShellRunParams(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000)
    timeout: int = Field(default=120000, ge=1000, le=600000)
    workdir: str = Field(default=".")

class TestRunParams(BaseModel):
    path: str = Field(default=".")
    pytest_args: str = Field(default="")
    timeout: int = Field(default=300000, ge=5000, le=600000)

class LintCheckParams(BaseModel):
    path: str = Field(default=".")
    fix: bool = Field(default=False)

# --- Git ---

class GitDiffParams(BaseModel):
    path: str = Field(default=".")
    staged: bool = Field(default=False)

class GitLogParams(BaseModel):
    path: str = Field(default=".")
    max_count: int = Field(default=20, ge=1, le=100)

class GitBlameParams(BaseModel):
    path: str = Field(..., min_length=1)

# --- Information ---

class WebSearchParams(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)

class ReadDocsParams(BaseModel):
    path: str = Field(...)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1)

# --- GitHub ---

class GitHubIssueReadParams(BaseModel):
    owner: str = Field(...)
    repo: str = Field(...)
    issue_number: int = Field(..., ge=1)

class GitHubPRCreateParams(BaseModel):
    title: str = Field(..., min_length=1)
    body: str = Field(default="")
    base: str = Field(default="main")
    head: str = Field(...)

class GitHubPRCommentParams(BaseModel):
    pr_number: int = Field(..., ge=1)
    body: str = Field(..., min_length=1)

# --- Pipeline ---

class AnalyzeRequirementsParams(BaseModel):
    input_text: str = Field(default="")
    input_file: str = Field(default="")

class DesignArchitectureParams(BaseModel):
    requirements_text: str = Field(default="")
    requirements_file: str = Field(default="")

class GenerateCodeParams(BaseModel):
    design_text: str = Field(default="")
    output_dir: str = Field(default="./outputs")

class GenerateTestsParams(BaseModel):
    code_path: str = Field(default=".")
    output_dir: str = Field(default="./outputs")

class DebugIssueParams(BaseModel):
    error_text: str = Field(default="")
    code_path: str = Field(default=".")

class RepairCodeParams(BaseModel):
    code_path: str = Field(default=".")
    max_attempts: int = Field(default=2, ge=1, le=5)

class GenerateReportParams(BaseModel):
    output_dir: str = Field(default="./outputs")

class PlanTaskParams(BaseModel):
    task_description: str = Field(..., min_length=1)

# --- Interaction ---

class AskUserParams(BaseModel):
    question: str = Field(..., min_length=1)
    context: str = Field(default="")

class RequestReviewParams(BaseModel):
    phase: str = Field(...)
    title: str = Field(default="")
    summary: str = Field(default="")

class SubmitParams(BaseModel):
    summary: str = Field(default="")

# --- Diagram ---

class DiagramRenderParams(BaseModel):
    diagram_code: str = Field(..., min_length=1)
    diagram_type: Literal["mermaid", "plantuml"] = Field(default="mermaid")
    output_format: Literal["svg", "png"] = Field(default="svg")

# --- Multimodal ---

class ImageReadParams(BaseModel):
    path: str = Field(...)


# ============================================================================
# Master Schema Registry — maps tool names to their param schemas
# ============================================================================

TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    # File ops
    "file_read": FileReadParams,
    "file_edit": FileEditParams,
    "file_write": FileWriteParams,
    "file_list": FileListParams,
    # Search
    "grep_text": GrepTextParams,
    "grep_ast": GrepASTParams,
    "find_symbol": FindSymbolParams,
    # Execution
    "shell_run": ShellRunParams,
    "test_run": TestRunParams,
    "lint_check": LintCheckParams,
    # Git
    "git_diff": GitDiffParams,
    "git_log": GitLogParams,
    "git_blame": GitBlameParams,
    # Info
    "web_search": WebSearchParams,
    "read_docs": ReadDocsParams,
    # GitHub
    "gh_issue_read": GitHubIssueReadParams,
    "gh_pr_create": GitHubPRCreateParams,
    "gh_pr_comment": GitHubPRCommentParams,
    # Pipeline
    "analyze_requirements": AnalyzeRequirementsParams,
    "design_architecture": DesignArchitectureParams,
    "generate_code": GenerateCodeParams,
    "generate_tests": GenerateTestsParams,
    "debug_issue": DebugIssueParams,
    "repair_code": RepairCodeParams,
    "generate_report": GenerateReportParams,
    "plan_task": PlanTaskParams,
    # Interaction
    "ask_user": AskUserParams,
    "request_review": RequestReviewParams,
    "submit": SubmitParams,
    # Diagram
    "diagram_render": DiagramRenderParams,
    # Multimodal
    "image_read": ImageReadParams,
}
