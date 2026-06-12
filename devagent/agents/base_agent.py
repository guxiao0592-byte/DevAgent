"""Base agent class with self-reflection and output validation support."""

import os
import json
from abc import ABC, abstractmethod
from typing import Optional, Any

from ..agent_core.state import AgentState
from ..agent_core.llm_client import LLMClient
from ..tools.file_tool import FileTool

# Shared self-review prompt used by all agents
SELF_REVIEW_PROMPT = """You are a quality assurance reviewer. Review the following output from an AI agent.

Check for:
1. COMPLETENESS: Are all required fields present and non-empty?
2. CORRECTNESS: Does the content make logical sense?
3. CONSISTENCY: Are there internal contradictions?
4. SPECIFICITY: Are statements concrete and specific (not vague)?
5. ACTIONABILITY: Can each item be acted upon?

If the output meets ALL criteria, respond with: {"quality": "pass", "feedback": ""}

If there are issues, respond with: {"quality": "fail", "feedback": "Specific, actionable issues to fix:"}

Be strict. Vague or generic outputs should fail."""


class BaseAgent(ABC):
    """Abstract base class with self-reflection and validation."""

    def __init__(self, llm_client: LLMClient, config: Optional[dict] = None):
        self.llm = llm_client
        self.config = config or {}
        self.file_tool = FileTool()
        self._enable_self_review = self.config.get("enable_self_review", True)

    @abstractmethod
    def run(self, state: AgentState) -> AgentState:
        """Execute the agent's task and update the state."""
        pass

    def reflect_and_refine(self, output: dict, context: dict, max_iterations: int = 2) -> dict:
        """Self-reflection loop: validate output, get feedback, refine.

        Args:
            output: The generated output dict to review
            context: Context about what was being generated
            max_iterations: Max refinement rounds

        Returns:
            Refined output dict
        """
        if not self._enable_self_review:
            return output

        current = output
        for iteration in range(max_iterations):
            review = self._self_review(current, context)
            if review.get("quality") == "pass":
                break

            feedback = review.get("feedback", "Improve the output quality")
            current = self._refine(current, context, feedback, iteration + 1)

        return current

    def _self_review(self, output: dict, context: dict) -> dict:
        """Review own output for quality issues."""
        try:
            result = self.llm.chat_structured(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Review this output for quality issues.\n\n"
                        f"=== CONTEXT ===\n{json.dumps(context, indent=2, ensure_ascii=False)[:1500]}\n\n"
                        f"=== OUTPUT TO REVIEW ===\n{json.dumps(output, indent=2, ensure_ascii=False)[:3000]}"
                    )
                }],
                system_prompt=SELF_REVIEW_PROMPT
            )
            return result
        except Exception:
            return {"quality": "pass", "feedback": ""}

    def _refine(self, output: dict, context: dict, feedback: str, iteration: int) -> dict:
        """Refine output based on feedback."""
        refine_prompt = (
            f"Improve the previous output based on this feedback.\n\n"
            f"=== CONTEXT ===\n{json.dumps(context, indent=2, ensure_ascii=False)[:1500]}\n\n"
            f"=== PREVIOUS OUTPUT ===\n{json.dumps(output, indent=2, ensure_ascii=False)[:3000]}\n\n"
            f"=== FEEDBACK (Iteration {iteration}) ===\n{feedback}\n\n"
            f"Generate an improved version that addresses ALL feedback points."
        )

        try:
            sp = self.get_refine_prompt()
            return self.llm.chat_structured(
                messages=[{"role": "user", "content": refine_prompt}],
                system_prompt=sp
            )
        except Exception:
            return output

    def validate_required_fields(self, data: dict, required_fields: list) -> list:
        """Validate that required fields exist and are non-empty."""
        missing = []
        for field in required_fields:
            value = data.get(field)
            if value is None or value == "" or (isinstance(value, list) and len(value) == 0) or (isinstance(value, dict) and len(value) == 0):
                missing.append(field)
        return missing

    def _save_artifact(self, state: AgentState, phase: str, filename: str, content: str) -> str:
        """Save an artifact to the appropriate output directory."""
        # Prefer central ArtifactRegistry if available on state
        try:
            reg = self.get_artifact_registry(state)
            if reg is not None:
                from ..agent_core.schemas import Artifact as ArtifactModel
                art = ArtifactModel(id="", type=f"{phase}:file", format="md", content=content, metadata={"filename": filename})
                entry = reg.register_from_state(state, phase, art)
                return entry.get("path")
        except Exception:
            # Fall back to file system writes
            pass

        out_dir = state.get_output_subdir(phase)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, filename)
        self.file_tool.write_text(path, content)
        return path

    def _save_json_artifact(self, state: AgentState, phase: str, filename: str, data: dict) -> str:
        """Save a JSON artifact."""
        try:
            reg = self.get_artifact_registry(state)
            if reg is not None:
                from ..agent_core.schemas import Artifact as ArtifactModel
                art = ArtifactModel(id="", type=f"{phase}:json", format="json", content=json.dumps(data, ensure_ascii=False, indent=2), metadata={"filename": filename})
                entry = reg.register_from_state(state, phase, art)
                return entry.get("path")
        except Exception:
            pass

        out_dir = state.get_output_subdir(phase)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, filename)
        self.file_tool.write_json(path, data)
        return path

    def _truncate_text(self, text: str, max_chars: int = 4000) -> str:
        """Truncate text to fit within token limits."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n\n... [truncated, original {len(text)} chars]"

    def get_refine_prompt(self) -> str:
        """Override to provide a custom refinement prompt."""
        return ("You are a senior engineer improving your previous output. "
                "Address all feedback while maintaining the original JSON structure.")

    def get_artifact_registry(self, state: object):
        """Return the central ArtifactRegistry if available, else None.

        Agents should prefer using this registry via register_from_state(state, phase, artifact).
        """
        try:
            reg = getattr(state, "artifact_registry", None)
            return reg
        except Exception:
            return None
