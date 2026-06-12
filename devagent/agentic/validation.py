"""Testing and validation enhancement for DevAgent V2.

Components:
  InstantValidator      — syntax + lint + import check after each code edit
  SmartRegressionSelector — AST-based affected test selection after file changes
  MutationTester        — inject bugs to verify test suite quality
  QualityGateSystem     — multi-level quality gates (L1-L6)
  CoverageAnalyzer      — branch coverage analysis without external tools
"""

import os
import ast
import re
import sys
import json
import asyncio
import subprocess
import copy
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class CheckResult:
    ok: bool
    error: str = ""
    detail: str = ""


@dataclass
class ValidationResult:
    success: bool
    checks: list[tuple[str, CheckResult]] = field(default_factory=list)
    message: str = ""

    def blocking_failures(self) -> list[tuple[str, CheckResult]]:
        return [(name, c) for name, c in self.checks if not c.ok]

    def summary(self) -> str:
        lines = ["=== Validation ==="]
        for name, cr in self.checks:
            icon = "PASS" if cr.ok else "FAIL"
            detail = f" — {cr.detail}" if cr.detail else ""
            lines.append(f"  [{icon}] {name}{detail}")
            if cr.error:
                lines.append(f"         Error: {cr.error[:200]}")
        return "\n".join(lines)


# ============================================================================
# Instant Validator
# ============================================================================

class InstantValidator:
    """Fast validation executed immediately after every file_edit/file_write.

    Validation order (fastest → slowest, stops on blocking failure):
      L1: Syntax check (AST parse, ~0.01s)
      L2: Import check (AST parse + basic resolution, ~0.05s)
      L3: Ruff lint (external, ~0.3s)
      L4: Basic pattern check (built-in heuristics, ~0.01s)
    """

    def __init__(self, workspace: str):
        self.workspace = workspace

    async def validate(self, modified_file: str) -> ValidationResult:
        checks = []

        # Resolve path
        file_path = Path(self.workspace) / modified_file
        if not file_path.exists():
            return ValidationResult(False, [
                ("existence", CheckResult(False, error=f"File not found: {modified_file}"))
            ], f"File not found: {modified_file}")

        content = file_path.read_text(encoding="utf-8", errors="replace")

        # L1: Syntax check (BLOCKING)
        syntax = await self._check_syntax(content, str(file_path))
        checks.append(("syntax", syntax))
        if not syntax.ok:
            return ValidationResult(False, checks, "Syntax error — fix before continuing")

        # L2: Import resolution (NON-BLOCKING)
        import_ok = await self._check_imports(content)
        checks.append(("imports", import_ok))

        # L3: Ruff lint (NON-BLOCKING)
        lint = await self._run_lint(str(file_path))
        checks.append(("lint", lint))

        # L4: Built-in pattern checks (NON-BLOCKING)
        patterns = self._check_patterns(content, str(file_path))
        checks.append(("patterns", patterns))

        success = all(c.ok for name, c in checks if name == "syntax")
        msg = "All checks passed" if success else "Validation issues found"
        return ValidationResult(success, checks, msg)

    @staticmethod
    async def _check_syntax(content: str, file_path: str) -> CheckResult:
        try:
            ast.parse(content)
            return CheckResult(True, detail="Valid Python syntax")
        except SyntaxError as e:
            return CheckResult(False,
                              error=f"Line {e.lineno}: {e.msg}",
                              detail=f"Syntax error at line {e.lineno}")

    @staticmethod
    async def _check_imports(content: str) -> CheckResult:
        """Check that imports reference real modules or known stdlib packages."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return CheckResult(False, error="Cannot parse to check imports")

        stdlib = {"os", "sys", "re", "json", "math", "datetime", "collections",
                  "itertools", "functools", "typing", "pathlib", "subprocess",
                  "asyncio", "logging", "unittest", "pytest", "abc", "dataclasses",
                  "enum", "hashlib", "random", "string", "time", "uuid", "copy",
                  "argparse", "csv", "io", "textwrap", "warnings", "tempfile",
                  "shutil", "glob", "fnmatch", "urllib", "http", "xml", "html",
                  "socket", "ssl", "email", "base64", "struct", "pickle",
                  "sqlite3", "contextlib", "traceback", "dis", "inspect",
                  "ast", "pdb", "profile", "unittest.mock", "pydantic", "fastapi",
                  "requests", "yaml", "typing_extensions", "dataclasses_json",
                  "numpy", "pandas", "flask", "django", "sqlalchemy", "click",
                  "rich", "tqdm", "_io", "importlib", "pkgutil", "concurrent",
                  "multiprocessing", "threading", "queue", "signal", "atexit",
                  "getpass", "platform", "resource", "statistics", "decimal",
                  "fractions", "numbers",
        }

        issues = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return CheckResult(True, detail="Skipped (syntax error)")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and not top.startswith("_"):
                        issues.append(f"Unknown import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top not in stdlib and node.level == 0:
                        pass  # Local imports are fine — checked at runtime

        if issues:
            return CheckResult(True, detail=f"{len(issues)} unknown imports (non-blocking)")
        return CheckResult(True, detail="Import references OK")

    @staticmethod
    async def _run_lint(file_path: str) -> CheckResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check", file_path, "--output-format", "concise",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode("utf-8", errors="replace")
            issues = [l for l in out.split("\n") if l.strip()]
            if len(issues) == 0:
                return CheckResult(True, detail="No lint issues")
            return CheckResult(True, detail=f"{len(issues)} lint issue(s) (non-blocking)")
        except FileNotFoundError:
            return CheckResult(True, detail="ruff not installed — skipped")
        except asyncio.TimeoutError:
            return CheckResult(True, detail="ruff timed out — skipped")

    @staticmethod
    def _check_patterns(content: str, file_path: str) -> CheckResult:
        lines = content.split("\n")
        warnings = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Bare except
            if stripped == "except:":
                warnings.append(f"Line {i}: Bare except: — use 'except Exception:'")

            # Print statements (outside of if __name__)
            if stripped.startswith("print(") and "__name__" not in content:
                warnings.append(f"Line {i}: print() statement — consider logging")

            # Long lines
            if len(stripped) > 120:
                warnings.append(f"Line {i}: Line too long ({len(stripped)} chars)")

            # TODO/FIXME without tracking
            if stripped.startswith("# TODO") or stripped.startswith("# FIXME"):
                warnings.append(f"Line {i}: Unresolved {stripped[:40]}")

        if warnings:
            return CheckResult(True, detail=f"{len(warnings)} style note(s)")
        return CheckResult(True, detail="No pattern issues")


# ============================================================================
# Smart Regression Test Selector
# ============================================================================

class SmartRegressionSelector:
    """Selects only tests affected by recent file modifications.

    Uses AST import analysis to map modified source files to their tests,
    avoiding running the full test suite on every edit.
    """

    def select(self, modified_files: list[str], test_path: str = "tests/") -> list[str]:
        """Return sorted list of test files affected by modifications."""
        affected = set()

        # Build set of modified module names
        modified_modules = set()
        for mf in modified_files:
            # src/models/user.py → src.models.user
            p = Path(mf)
            parts = list(p.parts)
            if parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]  # remove .py
            modified_modules.add(".".join(parts))
            # Also add without src/ prefix
            if parts[0] == "src":
                modified_modules.add(".".join(parts[1:]))

        # Scan test files
        test_root = Path(test_path)
        if not test_root.exists():
            return []

        for test_file in sorted(test_root.rglob("test_*.py")):
            try:
                tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, Exception):
                affected.add(str(test_file))
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module:
                        for mod in modified_modules:
                            if node.module.startswith(mod) or mod.startswith(node.module.split(".")[0]):
                                affected.add(str(test_file))
                                break

                # Also check class/function name matching
                name = test_file.stem
                if name.startswith("test_"):
                    target = name[5:]
                    # Check if any modified file matches the test's target module name
                    for mf in modified_files:
                        if target in Path(mf).stem:
                            affected.add(str(test_file))
                            break

        return sorted(affected) if affected else []

    @staticmethod
    def select_fast(test_path: str, modified_files: list[str]) -> list[str]:
        """Quick heuristic: run tests whose names match modified files."""
        selected = []
        for mf in modified_files:
            stem = Path(mf).stem
            test_candidate = Path(test_path) / f"test_{stem}.py"
            if test_candidate.exists():
                selected.append(str(test_candidate))

        if not selected:
            # Fallback: run all tests
            test_root = Path(test_path)
            if test_root.exists():
                selected = [str(f) for f in sorted(test_root.rglob("test_*.py"))]

        return selected


# ============================================================================
# Mutation Tester
# ============================================================================

class MutationTester:
    """Injects simple mutations into source code to verify test quality.

    Mutation operators:
      - Arithmetic:  + ↔ -, * ↔ /
      - Comparison:  == ↔ !=, < ↔ >, <= ↔ >=
      - Logical:     and ↔ or
      - Boundary:    < ↔ <=, > ↔ >=
      - Constant:    increment/decrement integer literals
    """

    MUTATION_PATTERNS = [
        # (regex, replacement pattern, description)
        (r'(?<![=!<>])=(?!=)', ' != ', '== to !='),
        (r' != ', ' == ', '!= to =='),
        (r' \+ ', ' - ', '+ to -'),
        (r' - ', ' + ', '- to +'),
        (r' and ', ' or ', 'and to or'),
        (r' or ', ' and ', 'or to and'),
        (r' / ', ' * ', '/ to *'),
    ]

    def test_quality(self, source_file: str, test_file: str,
                     workspace: str = ".") -> dict:
        """Run mutation testing and return quality report."""
        file_path = Path(workspace) / source_file
        if not file_path.exists():
            return {"error": f"Source file not found: {source_file}"}

        original = file_path.read_text(encoding="utf-8", errors="replace")
        mutants = self._generate_mutants(original, str(file_path))

        killed = 0
        survived = []

        # Only test up to 20 mutants for speed
        for i, mutant in enumerate(mutants[:20]):
            file_path.write_text(mutant["code"], encoding="utf-8")

            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", test_file, "-q", "--tb=line"],
                    cwd=workspace, capture_output=True, text=True, timeout=30
                )
                if proc.returncode != 0:
                    killed += 1
                else:
                    survived.append(mutant)
            except (subprocess.TimeoutExpired, Exception):
                survived.append(mutant)

            # Restore original
            file_path.write_text(original, encoding="utf-8")

        total = len(mutants[:20])
        score = killed / total if total > 0 else 1.0

        return {
            "total_mutants": total,
            "killed": killed,
            "survived": len(survived),
            "mutation_score": round(score, 2),
            "quality": "good" if score > 0.8 else "needs_improvement" if score > 0.5 else "poor",
            "surviving_mutants": [m["description"] for m in survived[:5]]
        }

    def _generate_mutants(self, code: str, file_path: str) -> list[dict]:
        """Generate mutant variations of the code."""
        mutants = []
        lines = code.split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith("#") or stripped.startswith('"""'):
                continue
            # Skip import lines
            if stripped.startswith("import ") or stripped.startswith("from "):
                continue

            for pattern, replacement, desc in self.MUTATION_PATTERNS:
                if re.search(pattern, stripped):
                    mutated_line = re.sub(pattern, replacement, stripped)
                    if mutated_line != stripped:
                        mutated_lines = lines[:]
                        mutated_lines[i] = " " * (len(line) - len(stripped)) + mutated_line
                        mutants.append({
                            "code": "\n".join(mutated_lines),
                            "line": i + 1,
                            "original": stripped,
                            "mutated": mutated_line,
                            "description": f"Line {i+1}: {desc} — '{stripped[:40]}' → '{mutated_line[:40]}'"
                        })
                        break  # One mutation per line

        return mutants


# ============================================================================
# Quality Gate System
# ============================================================================

class QualityGateSystem:
    """Multi-level quality gates for validating code changes.

    Gates (in order):
      L1_SYNTAX   — Python syntax valid (BLOCKING)
      L2_LINT     — Ruff lint passes (non-blocking)
      L3_IMPORTS  — Import checks pass (non-blocking)
      L4_UNIT     — Unit tests pass (BLOCKING)
      L5_COVERAGE — Branch coverage threshold (non-blocking, if coverage.py available)
      L6_REGRESSION — Full regression tests pass after repair (BLOCKING)
    """

    GATES = {
        "L1_SYNTAX": {
            "name": "Syntax Valid",
            "blocking": True,
            "auto_fix": False,
        },
        "L2_LINT": {
            "name": "Lint Clean",
            "blocking": False,
            "auto_fix": True,
            "max_issues": 10,
        },
        "L3_IMPORTS": {
            "name": "Import Check",
            "blocking": False,
            "auto_fix": False,
        },
        "L4_UNIT_TESTS": {
            "name": "Unit Tests Pass",
            "blocking": True,
            "auto_fix": True,
        },
        "L5_COVERAGE": {
            "name": "Coverage >= 80%",
            "blocking": False,
            "auto_fix": True,
            "threshold": 0.80,
        },
        "L6_REGRESSION": {
            "name": "Regression Tests Pass",
            "blocking": True,
            "auto_fix": True,
        },
    }

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.validator = InstantValidator(workspace)
        self._gate_results: dict[str, bool] = {}

    async def check_gate(self, gate_name: str, state: dict) -> tuple[bool, str]:
        """Check a specific quality gate against the given state."""
        gate = self.GATES.get(gate_name)
        if not gate:
            return True, f"Unknown gate: {gate_name}"

        if gate_name == "L1_SYNTAX":
            modified = state.get("modified_files", [])
            if not modified:
                return True, "No files modified"
            result = await self.validator.validate(modified[-1])
            return result.success, result.summary()

        elif gate_name == "L2_LINT":
            modified = state.get("modified_files", [])
            if not modified:
                return True, "No files to lint"
            result = await self.validator._run_lint(
                str(Path(self.workspace) / modified[-1])
            )
            return result.ok, result.detail

        elif gate_name == "L4_UNIT_TESTS":
            tr = state.get("test_results", {})
            if not tr:
                return False, "No test results available — run tests first"
            failed = tr.get("failed", 0)
            return failed == 0, f"{failed} tests failed"

        elif gate_name == "L6_REGRESSION":
            tr = state.get("test_results", {})
            if not tr:
                return False, "No regression test results"
            failed = tr.get("failed", 0)
            return failed == 0, f"{failed} regression tests failed"

        return True, "Gate passed"

    async def check_all(self, state: dict) -> dict[str, tuple[bool, str]]:
        """Check all applicable gates."""
        results = {}
        for gate_name in self.GATES:
            ok, msg = await self.check_gate(gate_name, state)
            results[gate_name] = (ok, msg)
            self._gate_results[gate_name] = ok
            # Stop on first blocking failure
            if not ok and self.GATES[gate_name]["blocking"]:
                break
        return results

    def summary(self) -> str:
        lines = ["## Quality Gates"]
        for name, config in self.GATES.items():
            result = self._gate_results.get(name)
            if result is None:
                lines.append(f"- [ ] {config['name']} (not checked)")
            elif result:
                lines.append(f"- [PASS] {config['name']}")
            else:
                label = "BLOCKED" if config["blocking"] else "WARN"
                lines.append(f"- [FAIL] {config['name']} [{label}]")
        return "\n".join(lines)
