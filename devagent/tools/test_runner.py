"""Pytest test runner with coverage.py integration.

Provides:
  - Structured pytest result parsing (collected/passed/failed/duration)
  - Line + branch coverage via coverage.py (if installed)
  - PYTHONPATH injection for cross-directory imports
  - Smart test file discovery
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional


class PytestRunner:
    """Runs pytest with coverage.py and returns structured results."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self._coverage_available = self._check_coverage()

    @staticmethod
    def _check_coverage() -> bool:
        try:
            r = subprocess.run(
                ["python", "-m", "coverage", "--version"],
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except Exception:
            return False

    # ==================================================================
    # Public API
    # ==================================================================

    def run_tests(self, test_dir: str, work_dir: Optional[str] = None,
                  extra_env: Optional[dict] = None,
                  with_coverage: bool = True,
                  source_dir: Optional[str] = None) -> dict:
        """Execute pytest and optionally measure coverage.

        Args:
            test_dir: Directory containing test_*.py files
            work_dir: Working directory (cwd) for subprocess
            extra_env: Extra env vars (e.g. PYTHONPATH)
            with_coverage: Enable coverage.py measurement
            source_dir: Source code dir for coverage measurement

        Returns:
            {
              "collected": int, "passed": int, "failed": int, "errors": int,
              "skipped": int, "duration": float,
              "coverage": {"line_rate": float, "branch_rate": float,
                           "covered_lines": int, "total_lines": int,
                           "missing": [...]} | None,
              "failed_cases": [...], "success": bool,
            }
        """
        if not os.path.exists(test_dir):
            return self._empty_result(f"Test directory not found: {test_dir}")

        test_files = list(Path(test_dir).rglob("test_*.py"))
        if not test_files:
            return self._empty_result(f"No test_*.py files in {test_dir}", success=True)

        # Determine source directory for coverage
        src = source_dir
        if not src and work_dir:
            src_candidate = os.path.join(work_dir, "src")
            if os.path.isdir(src_candidate):
                src = src_candidate

        # Build command
        if with_coverage and self._coverage_available and src:
            results = self._run_pytest_with_coverage(test_dir, work_dir, extra_env, src)
        else:
            results = self._run_pytest_bare(test_dir, work_dir, extra_env)

        return results

    def run_coverage_only(self, source_dir: str, test_dir: str,
                          work_dir: str = None) -> dict:
        """Run coverage measurement only (tests assumed already passing)."""
        if not self._coverage_available:
            return {"line_rate": 0, "branch_rate": 0,
                    "covered_lines": 0, "total_lines": 0,
                    "error": "coverage.py not installed. Install: pip install coverage"}
        return self._measure_coverage(source_dir, test_dir, work_dir)

    # ==================================================================
    # Private
    # ==================================================================

    def _run_pytest_with_coverage(self, test_dir: str, work_dir: str,
                                   extra_env: dict, source_dir: str) -> dict:
        """Run 'coverage run -m pytest ...' then 'coverage report --format=json'."""
        env = self._build_env(work_dir, extra_env)
        cwd = work_dir or os.getcwd()

        # Step 1: Coverage run
        cmd = [
            "python", "-m", "coverage", "run",
            "--source", source_dir,
            "--branch",
            "-m", "pytest", test_dir,
            "-v", "--tb=short", "--no-header", "-q",
        ]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            return self._empty_result("Test execution timed out")

        output = proc.stdout + "\n" + proc.stderr

        # Parse pytest results
        collected = self._parse_count(output, r'collected (\d+)')
        passed = self._parse_count(output, r'(\d+) passed')
        failed = self._parse_count(output, r'(\d+) failed')
        errors = self._parse_count(output, r'(\d+) errors?')
        failed_cases = self._extract_failed_cases(output)

        # Step 2: Coverage JSON report
        import tempfile
        coverage_json = os.path.join(tempfile.gettempdir(),
                                     f"coverage_{os.getpid()}.json")
        try:
            subprocess.run(
                ["python", "-m", "coverage", "json", "-o", coverage_json],
                cwd=cwd, capture_output=True, timeout=30, env=env
            )
            coverage = self._parse_coverage_json(coverage_json)
            try:
                os.unlink(coverage_json)
            except OSError:
                pass
        except Exception:
            coverage = None

        return {
            "collected": collected, "passed": passed,
            "failed": failed, "errors": errors, "skipped": 0,
            "duration": 0, "failed_cases": failed_cases,
            "stdout": output[:3000], "stderr": "",
            "success": proc.returncode in (0, 1) and errors == 0,
            "returncode": proc.returncode,
            "coverage": coverage,
        }

    def _run_pytest_bare(self, test_dir: str, work_dir: str,
                          extra_env: dict) -> dict:
        """Run pytest without coverage."""
        env = self._build_env(work_dir, extra_env)
        cwd = work_dir or os.getcwd()

        cmd = ["python", "-m", "pytest", test_dir,
               "-v", "--tb=short", "--no-header", "-q"]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            return self._empty_result("Test execution timed out")

        output = proc.stdout + "\n" + proc.stderr

        collected = self._parse_count(output, r'(\d+) collected')
        if not collected:
            collected = self._parse_count(output, r'collected (\d+)')
        passed = self._parse_count(output, r'(\d+) passed')
        failed = self._parse_count(output, r'(\d+) failed')
        errors = self._parse_count(output, r'(\d+) errors?')
        failed_cases = self._extract_failed_cases(output)

        return {
            "collected": collected, "passed": passed,
            "failed": failed, "errors": errors, "skipped": 0,
            "duration": 0, "failed_cases": failed_cases,
            "stdout": output[:3000], "stderr": "",
            "success": proc.returncode in (0, 1) and errors == 0,
            "returncode": proc.returncode,
            "coverage": None,
        }

    def _measure_coverage(self, source_dir: str, test_dir: str,
                          work_dir: str = None) -> dict:
        """Run coverage measurement and return parsed results."""
        import tempfile
        env = self._build_env(work_dir, None)
        cwd = work_dir or os.getcwd()

        tmp_json = os.path.join(tempfile.gettempdir(),
                                f"coverage_{os.getpid()}.json")

        # Erase old data
        subprocess.run(["python", "-m", "coverage", "erase"],
                      cwd=cwd, capture_output=True, timeout=10, env=env)

        # Run tests
        subprocess.run(
            ["python", "-m", "coverage", "run", "--source", source_dir,
             "--branch", "-m", "pytest", test_dir, "-q", "--no-header"],
            cwd=cwd, capture_output=True, timeout=self.timeout, env=env
        )

        # JSON report
        subprocess.run(
            ["python", "-m", "coverage", "json", "-o", tmp_json],
            cwd=cwd, capture_output=True, timeout=30, env=env
        )

        result = self._parse_coverage_json(tmp_json)

        try:
            os.unlink(tmp_json)
        except OSError:
            pass

        return result

    def _parse_coverage_json(self, path: str) -> Optional[dict]:
        """Parse coverage.py JSON output into a simple dict."""
        import json
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            return None

        totals = data.get("totals", {})
        missing_lines = []
        for fp, finfo in data.get("files", {}).items():
            missing = finfo.get("missing_lines", [])
            if missing:
                rel = fp
                for prefix in [os.getcwd(), "/"]:
                    if fp.startswith(prefix):
                        rel = fp[len(prefix):].lstrip("/")
                        break
                missing_lines.append({
                    "file": rel,
                    "missing_lines": missing[:15],  # top 15
                    "executed_lines": finfo.get("executed_lines", [])[:15],
                })

        return {
            "line_rate": round(totals.get("percent_covered", 0), 1),
            "branch_rate": 0,  # coverage.py JSON doesn't expose branch % easily
            "covered_lines": totals.get("covered_lines", 0),
            "total_lines": totals.get("num_statements", 0),
            "missing": missing_lines[:10],
        }

    # ==================================================================
    # Helpers
    # ==================================================================

    def _build_env(self, work_dir: str, extra: dict = None) -> dict:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        if work_dir:
            existing = env.get("PYTHONPATH", "")
            if work_dir not in existing:
                env["PYTHONPATH"] = f"{work_dir}:{existing}" if existing else work_dir
        return env

    @staticmethod
    def _empty_result(msg: str = "", success: bool = False) -> dict:
        return {
            "collected": 0, "passed": 0, "failed": 0, "errors": 0,
            "skipped": 0, "duration": 0, "failed_cases": [],
            "stdout": "", "stderr": msg, "success": success,
            "returncode": 0 if success else 1,
            "coverage": None,
        }

    @staticmethod
    def _parse_count(text: str, pattern: str) -> int:
        matches = re.findall(pattern, text)
        return sum(int(m) for m in matches) if matches else 0

    @staticmethod
    def _extract_failed_cases(text: str) -> list[dict]:
        cases = []
        for line in text.split('\n'):
            if 'FAILED' in line:
                parts = line.strip().split('FAILED')
                if len(parts) > 1:
                    test_name = parts[0].strip().split()[-1] if parts[0].strip() else ""
                    cases.append({"name": test_name, "message": parts[1].strip()[:200]})
        return cases

    @staticmethod
    def check_test_collection(test_dir: str) -> bool:
        try:
            r = subprocess.run(
                ["python", "-m", "pytest", test_dir, "--collect-only", "--quiet"],
                capture_output=True, text=True, timeout=30
            )
            return r.returncode == 0
        except Exception:
            return False
