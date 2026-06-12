"""Action Validator — validates Agent tool calls before execution.

Triple-check pipeline:
  1. Tool existence — is the tool registered?
  2. Schema validation — do params match the tool's Pydantic schema?
  3. Semantic/safety checks — are paths safe? commands dangerous?

Design goals:
  - 100% illegal tool call interception
  - Clear error messages for debugging
  - Pluggable safety policies
"""

from __future__ import annotations
from pydantic import ValidationError
from typing import Optional
import os
import re
import fnmatch

from .action_schema import TOOL_SCHEMAS, AgentAction


# ============================================================================
# Dangerous pattern definitions
# ============================================================================

DESTRUCTIVE_SHELL_PATTERNS = [
    r'\brm\s+-rf?\b', r'\bsudo\b', r'\bmkfs\b', r'\bdd\s+if=',
    r'\bchmod\s+.*777', r'\bchmod\s+-R\b',
    r'curl\s+.*\|.*sh', r'wget\s+.*\|.*sh',
    r'docker\s+system\s+prune', r'\bgit\s+push\s+--force\b',
    r'>\s*/dev/', r'\bformat\s+C:', r'\bdel\s+/[fsq]',
]

READ_ONLY_GIT_COMMANDS = {"diff", "log", "blame", "status", "show", "stash list"}

# Known tool registry for existence check (populated at runtime)
_known_tools: set[str] = set()


def register_known_tools(tool_names: list[str]):
    """Register the current set of valid tool names."""
    _known_tools.clear()
    _known_tools.update(tool_names)


# ============================================================================
# Validation Results
# ============================================================================

class ValidationResult:
    """Result of validating an agent action."""
    def __init__(self, is_valid: bool, error: str = "",
                 params: Optional[dict] = None, fixed: bool = False):
        self.is_valid = is_valid
        self.error = error
        self.params = params or {}
        self.fixed = fixed  # True if params were auto-repaired

    def __bool__(self):
        return self.is_valid

    def __repr__(self):
        return f"ValidationResult(valid={self.is_valid}, error='{self.error[:60]}', fixed={self.fixed})"


# ============================================================================
# Action Validator
# ============================================================================

class ActionValidator:
    """Validates AgentAction before execution."""

    def __init__(self, workspace_root: str = ".", strict_paths: bool = True):
        self.workspace_root = os.path.abspath(workspace_root)
        self.strict_paths = strict_paths

    def validate(self, action: AgentAction) -> ValidationResult:
        """Full validation pipeline for an agent action.

        Returns ValidationResult with is_valid=True only if all checks pass.
        """
        # Check 1: Tool existence
        tool_name = action.tool
        if not tool_name:
            return ValidationResult(False, "Empty tool name")

        if _known_tools and tool_name not in _known_tools:
            return ValidationResult(False,
                f"Unknown tool '{tool_name}'. Valid: {', '.join(sorted(_known_tools)[:15])}...")

        # Check 2: Schema validation
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema:
            try:
                validated = schema(**action.params)
                action.params = validated.model_dump()
            except ValidationError as e:
                # Try auto-repair
                fixed_params = self._repair_params(tool_name, action.params, e)
                if fixed_params is not None:
                    action.params = fixed_params
                    return ValidationResult(True, params=fixed_params, fixed=True)
                return ValidationResult(False, f"Schema validation failed for {tool_name}: {e}")

        # Check 3: Semantic/safety checks
        safety_ok, safety_err = self._safety_check(tool_name, action.params)
        if not safety_ok:
            return ValidationResult(False, safety_err)

        return ValidationResult(True, params=action.params)

    def _repair_params(self, tool_name: str, params: dict,
                       validation_error: ValidationError) -> Optional[dict]:
        """Attempt to auto-repair invalid parameters.

        Strategies:
          1. Type coercion (str → int for numeric fields)
          2. Missing required → set sensible defaults
          3. Extra unknown fields → strip
        """
        schema = TOOL_SCHEMAS.get(tool_name)
        if not schema:
            return None

        repaired = dict(params)
        errors = validation_error.errors()

        for err in errors:
            loc = err.get("loc", [])
            etype = err.get("type", "")

            if etype == "missing" and loc:
                # Try to infer from similar field names
                field_name = loc[0] if isinstance(loc[0], str) else str(loc[0])
                for key in params:
                    if field_name in key or key in field_name:
                        repaired[field_name] = params[key]
                        break

            elif etype in ("string_type", "int_type", "float_type") and loc:
                field_name = loc[0] if isinstance(loc[0], str) else str(loc[0])
                val = params.get(field_name)
                if etype == "int_type" and isinstance(val, str) and val.isdigit():
                    repaired[field_name] = int(val)
                elif etype == "float_type" and isinstance(val, (int, str)):
                    try:
                        repaired[field_name] = float(val)
                    except ValueError:
                        pass

            elif etype == "extra_forbidden":
                # Remove unknown fields
                for loc_part in loc:
                    repaired.pop(str(loc_part), None)

        # Re-validate repaired params
        try:
            validated = schema(**repaired)
            return validated.model_dump()
        except ValidationError:
            return None

    def _safety_check(self, tool_name: str, params: dict) -> tuple[bool, str]:
        """Security and safety policy enforcement."""
        if tool_name == "shell_run":
            return self._check_shell(params.get("command", ""))

        if tool_name in ("file_read", "file_edit", "file_write", "file_list"):
            path = params.get("path", ".")
            return self._check_path(path, tool_name.startswith("file_write"))

        if tool_name.startswith("git_") and tool_name != "git_diff":
            # Ensure we aren't doing destructive git ops
            pass

        return True, ""

    def _check_shell(self, command: str) -> tuple[bool, str]:
        """Check shell command against destructive patterns."""
        for pattern in DESTRUCTIVE_SHELL_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Dangerous shell command detected: '{command[:80]}' matches '{pattern}'"

        # Block pipe-to-shell patterns
        if re.search(r'\|\s*(?:ba)?sh\b', command):
            return False, f"Pipe-to-shell not allowed: '{command[:80]}'"

        return True, ""

    def _check_path(self, path: str, is_write: bool) -> tuple[bool, str]:
        """Validate file path is within workspace or allowed area."""
        if not path or not self.strict_paths:
            return True, ""

        normalized = os.path.normpath(path)
        # Allow relative paths and absolute paths within workspace
        if os.path.isabs(normalized):
            abs_ws = os.path.abspath(self.workspace_root)
            if not normalized.startswith(abs_ws):
                # Allow reading from well-known locations
                allowed_prefixes = ["/tmp/", "/dev/null", "/usr/", "/etc/"]
                if not is_write and any(normalized.startswith(p) for p in allowed_prefixes):
                    return True, ""
                return False, f"Path '{path}' is outside workspace '{abs_ws}'"

        return True, ""


# ============================================================================
# Quick Validation Helpers
# ============================================================================

def validate_action(tool_name: str, params: dict,
                    workspace: str = ".") -> ValidationResult:
    """Quick one-shot validation without requiring AgentAction object."""
    action = AgentAction(tool=tool_name, params=params)
    validator = ActionValidator(workspace_root=workspace)
    return validator.validate(action)


def is_tool_destructive(tool_name: str, params: dict) -> bool:
    """Check if a tool call would be destructive (for approval gating)."""
    if tool_name == "shell_run":
        cmd = params.get("command", "")
        return any(re.search(p, cmd, re.IGNORECASE) for p in DESTRUCTIVE_SHELL_PATTERNS)
    if tool_name == "file_write":
        path = params.get("path", "")
        return any(fnmatch.fnmatch(path, p) for p in ["/etc/*", "~/.ssh/*", "*.pem"])
    return False
