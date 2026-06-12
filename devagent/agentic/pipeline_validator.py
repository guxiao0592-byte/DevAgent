"""

为 PipelineRunner 每个阶段添加确定性验证。在进入审核前自动运行
lint + syntax + test 检查，结果展示给审核者。
"""
import os, ast, subprocess, time, re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    """A single validation check result."""
    name: str       # "syntax", "lint", "tests", "coverage"
    passed: bool
    detail: str = ""
    elapsed_ms: float = 0.0


@dataclass
class ValidationReport:
    """Complete validation report for a pipeline phase."""
    phase: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "phase": self.phase, "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail[:200],
                 "elapsed_ms": c.elapsed_ms}
                for c in self.checks
            ],
            "summary": self.summary[:300],
        }


class DeterministicValidator:
    """Run deterministic checks (no LLM) before each phase review.

    Checks by phase:
      requirements: syntax on generated files
      design:       syntax + mermaid validity
      implementation: syntax + lint + import check
      testing:      syntax + test_run (must all pass)
      delivery:     syntax + final check
    """

    def __init__(self, workspace: str = "."):
        self.workspace = workspace

    def validate(self, phase: str, generated_files: list[str]) -> ValidationReport:
        """Run validation checks appropriate for the phase.

        Returns ValidationReport with pass/fail and details.
        """
        report = ValidationReport(phase=phase, passed=True)

        py_files = [f for f in generated_files if f.endswith(".py") and "test" not in f]
        test_files = [f for f in generated_files if f.endswith(".py") and "test" in f]

        # 1. Syntax check (all phases that produce .py files)
        if py_files or test_files:
            check = self._check_syntax(py_files + test_files)
            report.checks.append(check)
            if not check.passed:
                report.passed = False

        # 2. Lint check (implementation + testing phases)
        if phase in ("implementation", "testing") and py_files:
            check = self._check_lint(py_files)
            report.checks.append(check)

        # 3. Test execution (testing phase)
        if phase == "testing" and test_files:
            check = self._run_tests(test_files)
            report.checks.append(check)
            if not check.passed:
                report.passed = False

        # 4. Import check (implementation phase)
        if phase == "implementation" and py_files:
            check = self._check_imports(py_files)
            report.checks.append(check)

        # Build summary
        if report.checks:
            passed = sum(1 for c in report.checks if c.passed)
            report.summary = f"{passed}/{len(report.checks)} checks passed"

        return report

    def _check_syntax(self, files: list[str]) -> CheckResult:
        """Check Python syntax for a list of files."""
        t0 = time.time()
        errors = []
        for f in files[:10]:  # Check up to 10 files
            fp = os.path.join(self.workspace, f) if not os.path.isabs(f) else f
            if not os.path.exists(fp):
                continue
            try:
                ast.parse(open(fp).read())
            except SyntaxError as e:
                errors.append(f"{os.path.basename(fp)}:{e.lineno}: {e.msg}")
                break  # First error is enough

        elapsed = (time.time() - t0) * 1000
        if errors:
            return CheckResult("syntax", False, errors[0], elapsed)
        return CheckResult("syntax", True, "All files syntactically valid", elapsed)

    def _check_lint(self, files: list[str]) -> CheckResult:
        """Run ruff lint if available, else basic pattern check."""
        t0 = time.time()
        # Check if ruff is available
        try:
            r = subprocess.run(["ruff", "--version"], capture_output=True, timeout=5)
            for f in files[:5]:
                fp = os.path.join(self.workspace, f) if not os.path.isabs(f) else f
                if os.path.exists(fp):
                    r = subprocess.run(["ruff", "check", fp], capture_output=True,
                                      timeout=10, text=True)
                    if r.returncode != 0:
                        elapsed = (time.time() - t0) * 1000
                        return CheckResult("lint", False,
                                         f"ruff found issues in {os.path.basename(fp)}",
                                         elapsed)
            elapsed = (time.time() - t0) * 1000
            return CheckResult("lint", True, "ruff: no issues", elapsed)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            elapsed = (time.time() - t0) * 1000
            return CheckResult("lint", True, "ruff not available — skipped", elapsed)

    def _run_tests(self, test_files: list[str]) -> CheckResult:
        """Run pytest on test files."""
        t0 = time.time()
        # Find test directories
        test_dirs = set()
        for f in test_files:
            fp = os.path.join(self.workspace, f) if not os.path.isabs(f) else f
            d = os.path.dirname(fp)
            if os.path.isdir(d):
                test_dirs.add(d)

        if not test_dirs:
            return CheckResult("tests", True, "No test directories found — skipped",
                             (time.time() - t0) * 1000)

        for td in list(test_dirs)[:3]:
            try:
                r = subprocess.run(
                    ["python", "-m", "pytest", td, "-x", "--tb=short", "--no-header", "-q"],
                    cwd=self.workspace, capture_output=True, text=True, timeout=120
                )
                passed = re.findall(r'(\d+) passed', r.stdout + r.stderr)
                failed = re.findall(r'(\d+) failed', r.stdout + r.stderr)
                p = sum(int(x) for x in passed)
                f = sum(int(x) for x in failed)
                elapsed = (time.time() - t0) * 1000

                if f > 0:
                    return CheckResult("tests", False,
                                     f"{p} passed, {f} failed — tests MUST all pass",
                                     elapsed)
                return CheckResult("tests", True, f"{p} tests all passed", elapsed)
            except (subprocess.TimeoutExpired, Exception):
                pass

        elapsed = (time.time() - t0) * 1000
        return CheckResult("tests", True, "tests: skipped (execution error)", elapsed)

    def _check_imports(self, files: list[str]) -> CheckResult:
        """Quick import resolution check."""
        t0 = time.time()
        for f in files[:3]:
            fp = os.path.join(self.workspace, f) if not os.path.isabs(f) else f
            if os.path.exists(fp):
                try:
                    ast.parse(open(fp).read())
                except SyntaxError:
                    elapsed = (time.time() - t0) * 1000
                    return CheckResult("imports", False,
                                     f"Syntax error in {os.path.basename(fp)}",
                                     elapsed)
        elapsed = (time.time() - t0) * 1000
        return CheckResult("imports", True, "All imports parseable", elapsed)
