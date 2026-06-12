"""Static analysis tools wrapper for Ruff and mypy."""

import subprocess
import json
from typing import Optional


class RuffChecker:
    """Wrapper for Ruff linter."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def check(self, target_path: str) -> dict:
        """Run Ruff check on target path."""
        try:
            result = subprocess.run(
                ["ruff", "check", target_path, "--output-format", "json"],
                capture_output=True, text=True, timeout=self.timeout
            )
            issues = []
            if result.stdout.strip():
                try:
                    issues = json.loads(result.stdout)
                except json.JSONDecodeError:
                    pass

            return {
                "success": result.returncode == 0,
                "issues_count": len(issues),
                "issues": issues[:20],
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:500]
            }
        except FileNotFoundError:
            return {"success": False, "issues_count": 0, "issues": [], "error": "Ruff not installed"}
        except subprocess.TimeoutExpired:
            return {"success": False, "issues_count": 0, "issues": [], "error": "Timeout"}
        except Exception as e:
            return {"success": False, "issues_count": 0, "issues": [], "error": str(e)}

    @staticmethod
    def install_check() -> bool:
        """Check if Ruff is available."""
        try:
            subprocess.run(["ruff", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


class MypyChecker:
    """Wrapper for mypy type checker."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def check(self, target_path: str) -> dict:
        """Run mypy check on target path."""
        try:
            result = subprocess.run(
                ["mypy", target_path, "--show-error-codes"],
                capture_output=True, text=True, timeout=self.timeout
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:500],
                "returncode": result.returncode
            }
        except FileNotFoundError:
            return {"success": False, "error": "mypy not installed"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def install_check() -> bool:
        """Check if mypy is available."""
        try:
            subprocess.run(["mypy", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


class StaticAnalyzer:
    """Unified static analysis interface for IDE integration.

    Combines Ruff (linting) and built-in pattern checks.
    Does NOT require external tools to be installed - provides basic
    pattern-based analysis as fallback.
    """

    def __init__(self):
        self.ruff = RuffChecker()

    def analyze_code(self, code: str, language: str = "python", filepath: str = "") -> list[dict]:
        """Analyze code and return a list of issues as dicts.

        Each issue dict has: line, col, message, level, code
        """
        issues = []

        if language == "python":
            issues.extend(self._analyze_python(code, filepath))
        elif language in ("javascript", "typescript"):
            issues.extend(self._analyze_js_ts(code, language))
        elif language == "java":
            issues.extend(self._analyze_java(code))
        else:
            issues.extend(self._analyze_generic(code, filepath))

        return issues

    def _analyze_python(self, code: str, filepath: str) -> list[dict]:
        """Analyze Python code for common issues."""
        issues = []

        # Try Ruff if available
        if filepath and RuffChecker.install_check():
            import tempfile
            import os
            tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w")
            tmp.write(code)
            tmp.close()
            result = self.ruff.check(tmp.name)
            os.unlink(tmp.name)
            for r_issue in result.get("issues", []):
                issues.append({
                    "line": r_issue.get("location", {}).get("row", 1),
                    "col": r_issue.get("location", {}).get("column", 1),
                    "message": r_issue.get("message", "Lint issue"),
                    "level": "warning",
                    "code": r_issue.get("code", "RUFF"),
                    "source": "ruff",
                })
            return issues

        # Fallback: built-in pattern checks
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Check for bare except
            if stripped == "except:":
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": "Bare except clause - use 'except Exception:' instead",
                    "level": "warning",
                    "code": "DA001",
                })

            # Check for print statements
            if stripped.startswith("print(") and "def " not in code[:code.find(stripped)]:
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": "Print statement found - consider using logging",
                    "level": "info",
                    "code": "DA002",
                })

            # Check for TODO/FIXME
            if "TODO" in stripped or "FIXME" in stripped:
                issues.append({
                    "line": i,
                    "col": stripped.find("TODO") if "TODO" in stripped else stripped.find("FIXME"),
                    "message": f"Found {'TODO' if 'TODO' in stripped else 'FIXME'} comment",
                    "level": "info",
                    "code": "DA003",
                })

            # Check for too-long lines
            if len(stripped) > 100:
                issues.append({
                    "line": i,
                    "col": 80,
                    "message": f"Line too long ({len(stripped)} > 100 characters)",
                    "level": "warning",
                    "code": "DA004",
                })

        return issues

    def _analyze_js_ts(self, code: str, language: str) -> list[dict]:
        """Analyze JavaScript/TypeScript code."""
        issues = []
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "console.log(" in stripped:
                issues.append({
                    "line": i,
                    "col": stripped.find("console.log(") + 1,
                    "message": "Console log statement found",
                    "level": "info",
                    "code": "DA010",
                })
            if "TODO" in stripped or "FIXME" in stripped:
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": f"Found {'TODO' if 'TODO' in stripped else 'FIXME'}",
                    "level": "info",
                    "code": "DA003",
                })
        return issues

    def _analyze_java(self, code: str) -> list[dict]:
        """Analyze Java code."""
        issues = []
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "System.out.println" in stripped:
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": "System.out.println found - consider using a logger",
                    "level": "info",
                    "code": "DA020",
                })
            if "TODO" in stripped or "FIXME" in stripped:
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": f"Found {'TODO' if 'TODO' in stripped else 'FIXME'}",
                    "level": "info",
                    "code": "DA003",
                })
        return issues

    def _analyze_generic(self, code: str, filepath: str) -> list[dict]:
        """Generic analysis for unknown languages."""
        issues = []
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "TODO" in stripped or "FIXME" in stripped:
                issues.append({
                    "line": i,
                    "col": 1,
                    "message": f"Found {'TODO' if 'TODO' in stripped else 'FIXME'}",
                    "level": "info",
                    "code": "DA003",
                })
        return issues
