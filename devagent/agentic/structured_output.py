"""Structured Output Module — unified LLM output handling.

Three-tier strategy:
  1. Native function calling (when model supports it)
  2. JSON mode with Schema enforcement
  3. Text parsing with repair + retry

Design:
  - StructuredOutputProcessor is the main entry point.
  - On parse failure: repair_prompt → re-parse → if still fail → degrade gracefully.
  - ModelAdapter selects the right strategy per model capability.
"""

from __future__ import annotations
from typing import Optional
import json
import re

from .action_schema import (
    AgentAction, TOOL_SCHEMAS, ModelCapabilities,
    CAPABILITY_PROFILES,
)
from .action_validator import ActionValidator, ValidationResult


# ============================================================================
# Model Adapter
# ============================================================================

class ModelAdapter:
    """Adapts output strategy based on model capabilities."""

    def __init__(self, model_id: str = "", capabilities: Optional[ModelCapabilities] = None):
        self.model_id = model_id
        # Auto-detect from profiles
        if capabilities:
            self.capabilities = capabilities
        else:
            self.capabilities = CAPABILITY_PROFILES.get(model_id)
            if not self.capabilities:
                # Default: conservative (text parse)
                self.capabilities = ModelCapabilities(
                    supports_function_calling=False,
                    supports_json_mode=False,
                    reliable_schema_output=False,
                    preferred_strategy="text_parse",
                )

    @property
    def uses_function_calling(self) -> bool:
        return self.capabilities.supports_function_calling and \
               self.capabilities.preferred_strategy in ("function_calling", "auto")

    @property
    def uses_json_mode(self) -> bool:
        return self.capabilities.supports_json_mode and \
               self.capabilities.preferred_strategy in ("json_mode", "auto")

    def get_system_prompt_addition(self) -> str:
        """Returns additional system prompt text based on output strategy."""
        if self.uses_function_calling:
            return ""  # Function calling handled by API layer

        if self.uses_json_mode:
            return """
OUTPUT FORMAT (REQUIRED):
You MUST respond with exactly one JSON object, no other text:
{
  "thought": "your reasoning here",
  "tool": "tool_name",
  "params": { "param1": "value1" }
}
"""

        # Text parse mode — require specific format
        return """
OUTPUT FORMAT (REQUIRED):
You MUST respond using this exact format:
THOUGHT: <your reasoning>
ACTION: <tool_name>
PARAMS: {"param1": "value1"}
"""


# ============================================================================
# Structured Output Processor
# ============================================================================

class StructuredOutputProcessor:
    """Processes LLM output into validated AgentAction.

    Three-tier parse strategy with automatic repair.
    """

    def __init__(self, adapter: Optional[ModelAdapter] = None,
                 validator: Optional[ActionValidator] = None,
                 max_repair_retries: int = 2):
        self.adapter = adapter or ModelAdapter()
        self.validator = validator or ActionValidator()
        self.max_repair_retries = max_repair_retries

    def parse(self, raw_output: str, known_tools: Optional[list[str]] = None) -> tuple[Optional[AgentAction], Optional[str]]:
        """Parse raw LLM output into AgentAction.

        Returns (action, error). If action is None, error contains the reason.
        """
        if not raw_output or not raw_output.strip():
            return None, "Empty LLM output"

        if known_tools:
            from .action_validator import register_known_tools
            register_known_tools(known_tools)

        # Strategy 1: JSON mode (model outputs JSON directly)
        action = self._parse_json(raw_output)
        if action:
            return action, None

        # Strategy 2: Text parse (THOUGHT/ACTION/PARAMS format)
        action = self._parse_text(raw_output)
        if action:
            return action, None

        # Strategy 3: Repair attempt — try to fix common issues
        action = self._parse_with_repair(raw_output)
        if action:
            return action, None

        return None, "Failed to parse LLM output after all strategies"

    def parse_and_validate(self, raw_output: str,
                           known_tools: Optional[list[str]] = None) -> tuple[Optional[AgentAction], Optional[ValidationResult]]:
        """Parse AND validate in one call."""
        action, error = self.parse(raw_output, known_tools)
        if error or not action:
            return None, ValidationResult(False, error or "Parse failed")

        result = self.validator.validate(action)
        if not result.is_valid:
            # Try one more time with repaired params
            if action.params and result.params:
                action.params = result.params
                result = self.validator.validate(action)

        return action, result

    def build_repair_prompt(self, raw_output: str, error: str) -> str:
        """Build a prompt to ask the model to fix its output format."""
        return f"""Your previous output could not be parsed. Error: {error}

Original output:
```
{raw_output[:800]}
```

Please reformat your output using this exact format:
THOUGHT: <your reasoning here>
ACTION: <tool_name>
PARAMS: {{"param1": "value1"}}

Output ONLY the formatted action, no other text."""

    # =========== Private Parsers ===========

    def _parse_json(self, text: str) -> Optional[AgentAction]:
        """Try to extract and parse a JSON object from the text."""
        # Try direct parse first
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict) and "tool" in data:
                return AgentAction(
                    thought=data.get("thought", ""),
                    tool=data.get("tool", ""),
                    params=data.get("params", data.get("parameters", {})),
                )
        except json.JSONDecodeError:
            pass

        # Try to find JSON block in text
        # Pattern 1: JSON code block ```
        m = re.search(r'```(?:json)?\s*\n(\{[\s\S]*?\})\s*\n```', text)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and "tool" in data:
                    return AgentAction(tool=data["tool"],
                        thought=data.get("thought", ""),
                        params=data.get("params", data.get("parameters", {})))
            except json.JSONDecodeError:
                pass

        # Pattern 2: Any { } JSON object
        for m in re.finditer(r'\{[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', text):
            try:
                data = json.loads(m.group())
                if isinstance(data, dict) and "tool" in data:
                    return AgentAction(
                        thought=data.get("thought", ""),
                        tool=data["tool"],
                        params=data.get("params", data.get("parameters", {})),
                    )
            except json.JSONDecodeError:
                continue

        # Pattern 3: Nested JSON with tool/params
        m = re.search(r'\{[\s\S]*?"tool"[\s\S]*?"params"[\s\S]*?\}', text)
        if m:
            try:
                data = json.loads(m.group())
                if isinstance(data, dict) and "tool" in data:
                    return AgentAction(
                        thought=data.get("thought", ""),
                        tool=data["tool"],
                        params=data.get("params", {}),
                    )
            except json.JSONDecodeError:
                pass

        return None

    def _parse_text(self, text: str) -> Optional[AgentAction]:
        """Parse THOUGHT/ACTION/PARAMS text format."""
        # Pattern 1: Full format with JSON params
        m = re.search(
            r'THOUGHT:\s*(.+?)\s*\n\s*ACTION:\s*(\w+)\s*\n\s*PARAMS:\s*(\{.+?\})\s*$',
            text, re.DOTALL | re.IGNORECASE
        )
        if m:
            try:
                params = json.loads(m.group(3))
            except json.JSONDecodeError:
                params = self._extract_params_fallback(m.group(3))
            return AgentAction(
                thought=m.group(1).strip(),
                tool=m.group(2).strip(),
                params=params,
            )

        # Pattern 2: Just ACTION found
        action_m = re.search(r'ACTION:\s*(\w+)', text, re.IGNORECASE)
        if action_m:
            return AgentAction(
                thought=text[:200].strip(),
                tool=action_m.group(1),
                params=self._extract_params_fallback(text),
            )

        # Pattern 3: tool_name(params) function-call-like format
        m = re.search(r'(\w+)\((\{.*?\})\)', text)
        if m:
            try:
                params = json.loads(m.group(2))
            except json.JSONDecodeError:
                params = {}
            return AgentAction(tool=m.group(1), params=params)

        return None

    def _parse_with_repair(self, text: str) -> Optional[AgentAction]:
        """Aggressive repair: fix common LLM output formatting errors."""
        text_clean = text.strip()

        # Fix 1: Missing THOUGHT prefix
        if not re.search(r'THOUGHT:', text_clean, re.IGNORECASE):
            # Try to find action and extract everything before as thought
            action_m = re.search(r'ACTION:\s*(\w+)', text_clean, re.IGNORECASE)
            if action_m:
                thought_part = text_clean[:action_m.start()].strip()
                if not thought_part:
                    thought_part = text_clean[:200].strip()
                params = self._extract_params_fallback(text_clean)
                return AgentAction(
                    thought=thought_part,
                    tool=action_m.group(1),
                    params=params,
                )

        # Fix 2: Unclosed JSON braces
        m = re.search(r'PARAMS:\s*(\{.*)', text_clean, re.DOTALL | re.IGNORECASE)
        if m:
            params_str = m.group(1)
            # Add missing closing brace
            open_count = params_str.count('{')
            close_count = params_str.count('}')
            if open_count > close_count:
                params_str += '}' * (open_count - close_count)
            try:
                params = json.loads(params_str)
                action_m = re.search(r'ACTION:\s*(\w+)', text_clean, re.IGNORECASE)
                thought_m = re.search(r'THOUGHT:\s*(.+?)(?:\n|ACTION:)', text_clean, re.DOTALL | re.IGNORECASE)
                return AgentAction(
                    thought=thought_m.group(1).strip() if thought_m else text_clean[:100],
                    tool=action_m.group(1) if action_m else "submit",
                    params=params,
                )
            except json.JSONDecodeError:
                pass

        # Fix 3: Python dict format → JSON
        m = re.search(r"PARAMS:\s*(\{['\"].+['\"]\s*:\s*.+\})", text_clean, re.DOTALL | re.IGNORECASE)
        if m:
            try:
                # ast.literal_eval for Python dict
                import ast
                params = ast.literal_eval(m.group(1))
                if isinstance(params, dict):
                    action_m = re.search(r'ACTION:\s*(\w+)', text_clean, re.IGNORECASE)
                    return AgentAction(
                        thought=text_clean[:200],
                        tool=action_m.group(1) if action_m else "submit",
                        params=params,
                    )
            except (ValueError, SyntaxError):
                pass

        return None

    @staticmethod
    def _extract_params_fallback(text: str) -> dict:
        """Extract tool parameters from free-form text."""
        params = {}

        # JSON object containing "path"
        json_match = re.search(r'\{[^{}]*"path"[^{}]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # key: "value" pairs
        for m in re.finditer(r'(\w+)\s*[:=]\s*"([^"]*)"', text):
            params[m.group(1)] = m.group(2)

        # key: number
        for m in re.finditer(r'(\w+)\s*[:=]\s*(\d+)', text):
            if m.group(1) not in params:
                params[m.group(1)] = int(m.group(2))

        return params


# ============================================================================
# Tool call formatting (for LLM prompts)
# ============================================================================

def format_tool_for_llm(tool_name: str, description: str,
                        params_schema: Optional[type] = None) -> str:
    """Format a tool definition for the LLM system prompt."""
    schema = params_schema or TOOL_SCHEMAS.get(tool_name)
    if not schema:
        return f"- {tool_name}: {description}"

    # Generate parameter documentation from Pydantic schema
    fields = []
    for name, field in schema.model_fields.items():
        required = "required" if field.is_required() else "optional"
        default = f" (default: {field.default})" if field.default is not None and not field.is_required() else ""
        type_name = field.annotation.__name__ if hasattr(field.annotation, '__name__') else str(field.annotation)
        desc = field.description or ""
        fields.append(f"    {name}: {type_name} [{required}]{default} — {desc}")

    params_text = "\n".join(fields)
    return f"- {tool_name}:\n  {description}\n  Parameters:\n{params_text}"


def format_all_tools_for_llm(tool_registry=None) -> str:
    """Generate the complete tool list section for the system prompt."""
    lines = ["Available tools:"]
    if tool_registry:
        for name in sorted(tool_registry.list_tools()):
            tool = tool_registry.get(name)
            desc = tool.description_text().split('\n')[0] if tool else ""
            lines.append(f"  - {name}: {desc}")
    else:
        for name, schema in sorted(TOOL_SCHEMAS.items()):
            lines.append(f"  - {name}")
    return "\n".join(lines)
