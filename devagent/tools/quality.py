"""Quality wrapper: run formatters/linters and return unified report.

This is intentionally lightweight: when external tools are missing it will
return a structured report indicating that the tool wasn't executed.

Planned checks:
- black (format)
- ruff or flake8 (lint)
- mypy (optional type-check)
- pytest (for running tests - separate runner)

The report structure:
{
  "formatted": bool,
  "format_tool": "black" | null,
  "format_changes": int,
  "lint_tool": "ruff" | "flake8" | null,
  "lint_errors": int,
  "lint_messages": [str],
  "type_check_tool": "mypy" | null,
  "type_errors": int,
  "type_messages": [str]
}
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional


@dataclass
class QualityReport:
    formatted: bool = False
    format_tool: Optional[str] = None
    format_changes: int = 0

    lint_tool: Optional[str] = None
    lint_errors: int = 0
    lint_messages: List[str] = None

    type_check_tool: Optional[str] = None
    type_errors: int = 0
    type_messages: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Ensure lists are not None
        d["lint_messages"] = d.get("lint_messages") or []
        d["type_messages"] = d.get("type_messages") or []
        return d


def _which(tool: str) -> Optional[str]:
    path = shutil.which(tool)
    return path


def run_black(target: str) -> Dict[str, Any]:
    if not _which("black"):
        return {"executed": False, "tool": None, "changes": 0, "output": "black not found"}
    try:
        # --quiet and --check could be used; here we format in-place and count changed files
        res = subprocess.run([sys.executable, "-m", "black", target], capture_output=True, text=True, check=False)
        output = res.stdout + "\n" + res.stderr
        changes = 0
        # black prints 'reformatted <file>' for changes
        for line in output.splitlines():
            if line.strip().startswith("reformatted "):
                changes += 1
        return {"executed": True, "tool": "black", "changes": changes, "output": output}
    except Exception as e:
        return {"executed": False, "tool": "black", "changes": 0, "output": str(e)}


def run_ruff(target: str) -> Dict[str, Any]:
    if _which("ruff"):
        try:
            res = subprocess.run([sys.executable, "-m", "ruff", "check", target, "--format", "json"], capture_output=True, text=True, check=False)
            output = res.stdout or res.stderr
            # ruff json format is newline-delimited JSON per issue; parse conservatively
            messages = []
            try:
                # Attempt to parse as JSON array
                parsed = json.loads(output)
                for file, issues in parsed.items():
                    for issue in issues:
                        messages.append(f"{file}:{issue.get('code')}:{issue.get('text')}")
                errors = len(messages)
            except Exception:
                # Fall back to plain text splits
                for line in output.splitlines():
                    if line.strip():
                        messages.append(line.strip())
                errors = len(messages)
            return {"executed": True, "tool": "ruff", "errors": errors, "messages": messages, "output": output}
        except Exception as e:
            return {"executed": False, "tool": "ruff", "errors": 0, "messages": [], "output": str(e)}
    # Fall back to flake8 if ruff not available
    if _which("flake8"):
        try:
            res = subprocess.run([sys.executable, "-m", "flake8", target, "--format=default"], capture_output=True, text=True, check=False)
            output = res.stdout or res.stderr
            messages = [l.strip() for l in output.splitlines() if l.strip()]
            return {"executed": True, "tool": "flake8", "errors": len(messages), "messages": messages, "output": output}
        except Exception as e:
            return {"executed": False, "tool": "flake8", "errors": 0, "messages": [], "output": str(e)}
    return {"executed": False, "tool": None, "errors": 0, "messages": [], "output": "no linter found"}


def run_mypy(target: str) -> Dict[str, Any]:
    if not _which("mypy"):
        return {"executed": False, "tool": None, "errors": 0, "messages": [], "output": "mypy not found"}
    try:
        res = subprocess.run([sys.executable, "-m", "mypy", target, "--show-error-codes", "--no-color-output"], capture_output=True, text=True, check=False)
        output = res.stdout or res.stderr
        messages = [l.strip() for l in output.splitlines() if l.strip()]
        return {"executed": True, "tool": "mypy", "errors": len(messages), "messages": messages, "output": output}
    except Exception as e:
        return {"executed": False, "tool": "mypy", "errors": 0, "messages": [], "output": str(e)}


def run_quality(target: str) -> QualityReport:
    """Run configured quality tools against target path and return a QualityReport."""
    qr = QualityReport()

    # Format
    black_res = run_black(target)
    if black_res.get("executed"):
        qr.formatted = True
        qr.format_tool = black_res.get("tool")
        qr.format_changes = black_res.get("changes", 0)
    else:
        qr.formatted = False
        qr.format_tool = black_res.get("tool")
        qr.format_changes = 0

    # Lint
    lint_res = run_ruff(target)
    qr.lint_tool = lint_res.get("tool")
    qr.lint_errors = lint_res.get("errors", 0)
    qr.lint_messages = lint_res.get("messages", [])

    # Type check
    mypy_res = run_mypy(target)
    qr.type_check_tool = mypy_res.get("tool")
    qr.type_errors = mypy_res.get("errors", 0)
    qr.type_messages = mypy_res.get("messages", [])

    return qr


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("target", help="Target path to check")
    args = p.parse_args()
    report = run_quality(args.target)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
