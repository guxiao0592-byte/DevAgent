"""V1 Agent → V2 Tool Adapters — unified architecture bridge.

Wraps each V1 professional agent as a V2 BaseTool callable within the ReAct loop.
This merges V1's deterministic pipeline capabilities into V2's autonomous reasoning.

Architecture:
  V1 RequirementAgent  → AnalyzeRequirementsTool
  V1 DesignAgent       → DesignArchitectureTool
  V1 CodeAgent         → GenerateCodeTool
  V1 TestAgent         → GenerateTestsTool
  V1 DebugAgent        → DebugIssueTool
  V1 RepairAgent       → RepairCodeTool
  V1 ReviewAgent       → GenerateReportTool
  V1 PlannerAgent      → PlanTaskTool
"""

import os
import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .tools import BaseTool, ToolResult, ToolRegistry


# ============================================================================
# Adapter — wraps V1 BaseAgent.run() as a V2 tool
# ============================================================================

class _AgentToolAdapter(BaseTool):
    """Generic adapter: V1 agent → V2 tool."""

    def __init__(self, agent_class, llm_client, config: dict = None,
                 name: str = "", description: str = "", parameters: dict = None):
        self._name = name
        self._description = description
        self._parameters = parameters or {}
        self._agent_class = agent_class
        self._llm = llm_client
        self._config = config or {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        """Execute the wrapped V1 agent."""
        from ..agent_core.state import AgentState

        # Build a temporary AgentState for the V1 agent
        state = AgentState(
            task_type="agentic",
            input_path=params.get("input_path", ""),
            output_root=workspace,
            max_retry=2,
        )

        # Pre-populate state with any existing artifacts
        reqs = params.get("requirements")
        if reqs:
            state.requirements = reqs
        design = params.get("design_artifacts")
        if design:
            state.design_artifacts = design
        code_files = params.get("code_files")
        if code_files:
            state.code_files = [os.path.join(workspace, f) if not os.path.isabs(f) else f for f in code_files]
        language = params.get("language", "python")
        if hasattr(state, 'language'):
            state.language = language
        test_results = params.get("test_results")
        if test_results:
            state.test_results = test_results

        # Auto-detect: if no explicit artifact key, check if params themselves look like an artifact
        if not reqs and not design:
            if any(k in params for k in ("project_summary", "functional_requirements", "domain_model")):
                state.requirements = params
            elif any(k in params for k in ("architecture_overview", "class_diagram_mermaid", "module_division", "er_diagram_mermaid")):
                state.design_artifacts = params

        # Write temporary input file if content provided
        input_content = params.get("input_content", "")
        if input_content and not state.input_path:
            tmp_dir = os.path.join(workspace, ".devagent", "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_input = os.path.join(tmp_dir, f"agent_input_{self._name}.md")
            Path(tmp_input).write_text(input_content, encoding="utf-8")
            state.input_path = tmp_input

        # Fallback: if no input path set, try requirements.md in workspace
        if not state.input_path or not os.path.exists(state.input_path):
            candidates = ["requirements.md", "README.md", "input.md"]
            for cand in candidates:
                cand_path = os.path.join(workspace, cand)
                if os.path.exists(cand_path):
                    state.input_path = cand_path
                    break

        # Instantiate and run the V1 agent
        try:
            agent = self._agent_class(self._llm, self._config)
            state = agent.run(state)

            # Extract structured results from state
            result_data = {}
            if self._name == "analyze_requirements":
                result_data = {
                    "requirements": state.requirements,
                    "status": state.status,
                }
            elif self._name == "design_architecture":
                result_data = {
                    "design_artifacts": state.design_artifacts,
                    "status": state.status,
                }
            elif self._name == "generate_code":
                result_data = {
                    "code_files": state.code_files,
                    "file_count": len(state.code_files),
                }
            elif self._name == "generate_tests":
                result_data = {
                    "test_files": state.test_files,
                    "test_results": state.test_results,
                    "passed": state.test_results.get("passed", 0) if state.test_results else 0,
                    "failed": state.test_results.get("failed", 0) if state.test_results else 0,
                }
            elif self._name == "debug_issue":
                result_data = {
                    "debug_analysis": state.debug_analysis,
                    "bug_location": state.debug_analysis.get("bug_location", {}) if state.debug_analysis else {},
                }
            elif self._name == "repair_code":
                result_data = {
                    "repair_patch": state.repair_patch,
                    "modified_files": state.repair_patch.get("modified_files", []) if state.repair_patch else [],
                }
            elif self._name == "generate_report":
                result_data = {
                    "final_report": state.final_report,
                    "status": state.status,
                }
            elif self._name == "plan_task":
                result_data = {
                    "execution_plan": state.input_manifest,
                    "status": state.status,
                }

            # Build a helpful output that guides to the next step
            errors = [e.get("message", "") for e in state.errors] if state.errors else []

            next_step_hints = {
                "analyze_requirements": "\nNEXT: Pass this entire output as 'requirements' to design_architecture.",
                "design_architecture": "\nNEXT: Pass this entire output as 'design_artifacts' to generate_code.",
                "generate_code": "\nNEXT: Call test_run to verify the generated tests pass.",
                "generate_tests": "\nNEXT: If tests pass, call request_review. If tests fail, call debug_issue.",
                "debug_issue": "\nNEXT: Pass this output as 'debug_analysis' to repair_code.",
                "repair_code": "\nNEXT: Call test_run for regression verification.",
                "plan_task": "\nNEXT: Call analyze_requirements with the requirements text as 'input_content'.",
                "generate_report": "\nNEXT: Call request_review for final delivery approval.",
            }
            hint = next_step_hints.get(self._name, "")
            result_json = json.dumps(result_data, indent=2, ensure_ascii=False)
            output_text = f"{result_json[:4000]}{hint}"

            return ToolResult(
                success=len(errors) == 0,
                output=output_text,
                error="; ".join(errors) if errors else "",
                structured=result_data,
            )

        except Exception as e:
            return ToolResult(
                False,
                output="",
                error=f"{self._name} failed: {str(e)}",
                structured={"error": str(e)},
            )


# ============================================================================
# Concrete Tool Definitions
# ============================================================================

REQUIREMENTS_PARAMS = {
    "input_content": {
        "type": "string",
        "description": "Project description, requirements document, or PRD content to analyze. Be thorough — include all known requirements, constraints, and goals."
    },
    "requirements": {
        "type": "object",
        "description": "Optional: existing partial requirements to refine or extend"
    }
}

DESIGN_PARAMS = {
    "requirements": {
        "type": "object",
        "description": "Structured requirements from analyze_requirements (REQUIRED)"
    },
    "input_content": {
        "type": "string",
        "description": "Additional design constraints or architecture preferences"
    }
}

CODE_PARAMS = {
    "requirements": {
        "type": "object",
        "description": "Structured requirements (optional if design_artifacts provided)"
    },
    "design_artifacts": {
        "type": "object",
        "description": "Design artifacts from design_architecture (preferred input)"
    },
    "language": {
        "type": "string",
        "description": "Target programming language (default: python)",
        "default": "python"
    },
    "output_dir": {
        "type": "string",
        "description": "Directory for generated code (default: workspace/src)",
        "default": "src"
    }
}

TEST_PARAMS = {
    "code_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "List of source code file paths to test (REQUIRED)"
    },
    "test_focus": {
        "type": "string",
        "description": "Specific areas to focus testing on: 'all', 'unit', 'integration', 'edge_cases'"
    }
}

DEBUG_PARAMS = {
    "test_results": {
        "type": "object",
        "description": "Test execution results from generate_tests or test_run"
    },
    "code_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Source code files to analyze for bugs"
    },
    "error_description": {
        "type": "string",
        "description": "Description of the bug or failure symptoms"
    }
}

REPAIR_PARAMS = {
    "debug_analysis": {
        "type": "object",
        "description": "Debug analysis from debug_issue (REQUIRED)"
    },
    "code_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Source code files to repair"
    }
}

REPORT_PARAMS = {
    "requirements": {
        "type": "object",
        "description": "Requirements from analyze_requirements"
    },
    "design_artifacts": {
        "type": "object",
        "description": "Design from design_architecture"
    },
    "code_files": {
        "type": "array",
        "items": {"type": "string"},
        "description": "List of generated source files"
    },
    "test_results": {
        "type": "object",
        "description": "Test execution results"
    },
    "repair_patch": {
        "type": "object",
        "description": "Repair patch info from repair_code"
    },
    "report_type": {
        "type": "string",
        "description": "Report type: 'summary', 'executive', or 'full'"
    }
}

PLAN_PARAMS = {
    "task_description": {
        "type": "string",
        "description": "Complete task description including requirements and goals"
    }
}


def register_pipeline_tools(registry: ToolRegistry, llm_client,
                            config: dict = None) -> ToolRegistry:
    """Register all V1→V2 pipeline adapter tools on a ToolRegistry.

    Args:
        registry: Existing ToolRegistry to extend
        llm_client: LLMClient instance for V1 agents
        config: Tool configuration dict

    Returns:
        Same registry with pipeline tools added
    """
    from ..agents.requirement_agent import RequirementAgent
    from ..agents.design_agent import DesignAgent
    from ..agents.code_agent import CodeAgent
    from ..agents.test_agent import TestAgent
    from ..agents.debug_agent import DebugAgent
    from ..agents.repair_agent import RepairAgent
    from ..agents.review_agent import ReviewAgent
    from ..agents.planner_agent import PlannerAgent

    cfg = config or {}

    registry.register(_AgentToolAdapter(
        PlannerAgent, llm_client, cfg.get("planning", {}),
        name="plan_task",
        description=(
            "Create a structured execution plan from a task description. "
            "Decomposes the task into ordered phases with dependencies, "
            "complexity estimates, and quality criteria. "
            "CALL THIS FIRST when starting any multi-step development task."
        ),
        parameters=PLAN_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        RequirementAgent, llm_client, cfg.get("requirements", {}),
        name="analyze_requirements",
        description=(
            "Perform professional requirements analysis on a project description. "
            "Produces a structured requirements specification with domain model, "
            "functional/non-functional requirements, use cases, and risk assessment. "
            "CALL THIS after plan_task when in a full development pipeline."
        ),
        parameters=REQUIREMENTS_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        DesignAgent, llm_client, cfg.get("design", {}),
        name="design_architecture",
        description=(
            "Generate a professional system architecture design from requirements. "
            "Produces C4-model diagrams (Mermaid), class diagrams, ER diagrams, "
            "sequence diagrams, module decomposition, API contracts, and technology stack. "
            "CALL THIS after analyze_requirements to create the system blueprint."
        ),
        parameters=DESIGN_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        CodeAgent, llm_client, cfg.get("code", {}),
        name="generate_code",
        description=(
            "Generate production-grade source code from design specifications. "
            "Produces a complete project scaffold with type hints, docstrings, "
            "error handling, logging, config management, and entry points. "
            "CALL THIS after design_architecture to implement the system."
        ),
        parameters=CODE_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        TestAgent, llm_client, cfg.get("test", {}),
        name="generate_tests",
        description=(
            "Generate comprehensive pytest test suites for existing source code. "
            "Produces unit tests, integration tests, parametrized tests, fixtures, "
            "and edge case coverage. Then executes the tests and reports results. "
            "CALL THIS after generate_code to verify correctness."
        ),
        parameters=TEST_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        DebugAgent, llm_client, cfg.get("debug", {}),
        name="debug_issue",
        description=(
            "Perform deep root cause analysis on test failures or bugs. "
            "Classifies bug type (null_pointer, off_by_one, logic_error, etc.), "
            "traces execution flow, identifies exact file:line location, "
            "and proposes fix hypotheses with pros/cons. "
            "CALL THIS when tests fail to understand what went wrong."
        ),
        parameters=DEBUG_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        RepairAgent, llm_client, cfg.get("repair", {}),
        name="repair_code",
        description=(
            "Apply minimal, safe code fixes based on debug analysis. "
            "Generates unified diff patches, applies fixes, and runs regression tests. "
            "Follows minimal-change principle: only fix what's broken. "
            "CALL THIS after debug_issue to fix identified bugs."
        ),
        parameters=REPAIR_PARAMS,
    ))

    registry.register(_AgentToolAdapter(
        ReviewAgent, llm_client, cfg.get("review", {}),
        name="generate_report",
        description=(
            "Generate a comprehensive executive report summarizing all phases. "
            "Includes quality dashboard, phase-by-phase metrics, execution timeline, "
            "and actionable recommendations. "
            "CALL THIS as the final step before submit."
        ),
        parameters=REPORT_PARAMS,
    ))

    return registry
