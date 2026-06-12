"""Task Router - determines the task type and routes to the appropriate workflow."""

import os
from .schemas import TaskSpec


class TaskRouter:
    """Routes incoming tasks based on type and validates parameters."""

    @staticmethod
    def parse_args(args: dict) -> TaskSpec:
        """Parse a dictionary of arguments into a TaskSpec."""
        spec = TaskSpec(
            task_type=args.get("task", ""),
            input_path=args.get("input", ""),
            code_path=args.get("code", ""),
            tests_path=args.get("tests", ""),
            output_path=args.get("output", "./outputs"),
            max_retry=int(args.get("max_retry", 2)),
            verbose=args.get("verbose", False),
            language=args.get("language", "python")
        )
        return spec

    @staticmethod
    def validate_task(spec: TaskSpec) -> tuple[bool, str]:
        """Validate task specification."""
        return spec.validate()

    @staticmethod
    def resolve_input_path(spec: TaskSpec) -> str:
        """Resolve and validate input file path."""
        if spec.input_path:
            if not os.path.exists(spec.input_path):
                return ""
            return spec.input_path
        return ""

    @staticmethod
    def resolve_code_path(spec: TaskSpec) -> str:
        """Resolve and validate code directory path."""
        if spec.code_path:
            if not os.path.exists(spec.code_path):
                return ""
            return spec.code_path
        return ""

    @staticmethod
    def detect_task_type(input_text: str) -> str:
        """Auto-detect task type from input content (heuristic)."""
        # If it starts with "fix", "debug", "repair" → agentic repair
        repair_keywords = ["fix ", "debug ", "repair ", "bug ", "error ", "failing "]
        design_keywords = ["class diagram", "architecture", "module", "interface", "design"]
        code_keywords = ["def ", "class ", "import ", "implementation"]

        input_lower = input_text.lower()

        if any(kw in input_lower for kw in repair_keywords):
            return "agentic"

        design_score = sum(1 for kw in design_keywords if kw in input_lower)
        code_score = sum(1 for kw in code_keywords if kw in input_lower)

        if code_score > design_score:
            return "implement"
        return "design"
