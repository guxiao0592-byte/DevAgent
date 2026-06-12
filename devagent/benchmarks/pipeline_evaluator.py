"""Pipeline Evaluator — 5-dimension quality scoring across all 7 pipeline phases.

方案二: Custom Pipeline End-to-End Evaluation Suite

Scoring dimensions (0-100 each, weighted sum = final score):
  Correctness (40%): tests pass rate + edge-case coverage
  Completeness (25%): all artifacts present
  Code Quality (20%): ruff+mypy+bandit+complexity
  Doc Quality (10%): IEEE standards compliance
  Maintainability (5%): architecture hygiene

Usage:
  python -m devagent.benchmarks.pipeline_evaluator \
      --output_dir ./outputs/run_task_xxx \
      --case calculator

  python -m devagent.benchmarks.pipeline_evaluator --suite quick
"""

import os
import re
import ast
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class DimensionScore:
    name: str
    weight: float
    score: int = 0          # 0-100
    details: list[str] = field(default_factory=list)
    checks_passed: int = 0
    checks_total: int = 0

    @property
    def weighted(self) -> float:
        return self.score * self.weight


@dataclass
class PhaseResult:
    """Per-phase evaluation result."""
    phase: str
    success: bool
    artifacts_found: list[str] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)


@dataclass
class EvalReport:
    """Complete evaluation report."""
    case_name: str
    output_dir: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat()[:19])
    overall_score: float = 0
    dimensions: list[DimensionScore] = field(default_factory=list)
    phases: list[PhaseResult] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_name": self.case_name, "output_dir": self.output_dir,
            "timestamp": self.timestamp, "overall_score": round(self.overall_score, 1),
            "dimensions": [
                {"name": d.name, "weight": d.weight, "score": d.score,
                 "weighted": round(d.weighted, 1),
                 "checks_passed": d.checks_passed, "checks_total": d.checks_total,
                 "details": d.details}
                for d in self.dimensions
            ],
            "phases": [
                {"phase": p.phase, "success": p.success,
                 "artifacts_found": len(p.artifacts_found),
                 "artifacts_missing": p.artifacts_missing,
                 "checks": p.checks}
                for p in self.phases
            ],
            "recommendations": self.recommendations,
        }


# ============================================================================
# Pipeline Evaluator
# ============================================================================

class PipelineEvaluator:
    """Score a completed pipeline output directory across 5 quality dimensions.

    Usage:
        evaluator = PipelineEvaluator()
        report = evaluator.evaluate("./outputs/run_task_xxx", "calculator")
        # report.overall_score = 78.5
    """

    # Phase directory mapping
    PHASE_DIRS = {
        "requirements": "01_requirements",
        "design": "02_design",
        "implementation": "03_implementation",
        "testing": "04_tests",
        "repair": "05_repair",
        "delivery": "06_reports",
        "revision": "07_revision",
    }

    # Expected artifacts per phase
    EXPECTED_ARTIFACTS = {
        "requirements": [
            ("01_requirements/requirement_specification.md", "SRS document"),
            ("01_requirements/structured_requirements.json", "Structured reqs"),
        ],
        "design": [
            ("02_design/architecture_design_spec.md", "SDD document"),
            ("02_design/design_artifacts.json", "Design JSON"),
            ("02_design/class_diagram.mmd", "Class diagram"),
            ("02_design/er_diagram.mmd", "ER diagram"),
        ],
        "implementation": [
            ("03_implementation/src/__init__.py", "src package"),
            ("03_implementation/requirements.txt", "Dependencies"),
            ("03_implementation/.env.example", "Env template"),
            ("03_implementation/pyproject.toml", "Project metadata"),
        ],
        "testing": [
            ("04_tests/pytest_result.json", "Test results"),
            ("04_tests/test_execution_report.md", "Test report"),
        ],
        "delivery": [
            ("06_reports/executive_report.md", "Executive report"),
            ("06_reports/result_summary.json", "Result summary"),
        ],
        "repair": [
            ("05_repair/patch.diff", "Fix patch"),
            ("05_repair/debug_analysis.json", "Debug analysis"),
        ],
    }

    def __init__(self):
        self._ruff_available = self._check_tool("ruff --version")
        self._mypy_available = self._check_tool("mypy --version")
        self._bandit_available = self._check_tool("bandit --version")

    @staticmethod
    def _check_tool(cmd: str) -> bool:
        try:
            r = subprocess.run(cmd.split(), capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    # ==================================================================
    # Public API
    # ==================================================================

    def evaluate(self, output_dir: str, case_name: str = "") -> EvalReport:
        """Evaluate a completed pipeline output directory.

        Args:
            output_dir: Path to the pipeline output (e.g. ./outputs/run_task_xxx)
            case_name: Human-readable case name

        Returns:
            EvalReport with scores, details, recommendations
        """
        if not os.path.isdir(output_dir):
            raise FileNotFoundError(f"Output directory not found: {output_dir}")

        report = EvalReport(
            case_name=case_name or os.path.basename(output_dir),
            output_dir=os.path.abspath(output_dir),
        )

        # === Phase discovery ===
        report.phases = self._discover_phases(output_dir)

        # === Dimension 1: Correctness (40%) ===
        d1 = self._eval_correctness(output_dir, report.phases)
        report.dimensions.append(d1)

        # === Dimension 2: Completeness (25%) ===
        d2 = self._eval_completeness(output_dir, report.phases)
        report.dimensions.append(d2)

        # === Dimension 3: Code Quality (20%) ===
        d3 = self._eval_code_quality(output_dir)
        report.dimensions.append(d3)

        # === Dimension 4: Doc Quality (10%) ===
        d4 = self._eval_doc_quality(output_dir)
        report.dimensions.append(d4)

        # === Dimension 5: Maintainability (5%) ===
        d5 = self._eval_maintainability(output_dir)
        report.dimensions.append(d5)

        # Compute weighted total
        report.overall_score = sum(d.weighted for d in report.dimensions)

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    def evaluate_quick(self, output_dir: str, case_name: str = "") -> EvalReport:
        """Quick evaluation: only correctness + completeness (no external tools)."""
        report = EvalReport(case_name=case_name or os.path.basename(output_dir),
                           output_dir=os.path.abspath(output_dir))
        report.phases = self._discover_phases(output_dir)

        d1 = self._eval_correctness(output_dir, report.phases)
        d1.weight = 0.55
        report.dimensions.append(d1)

        d2 = self._eval_completeness(output_dir, report.phases)
        d2.weight = 0.45
        report.dimensions.append(d2)

        report.overall_score = sum(d.weighted for d in report.dimensions)
        report.recommendations = self._generate_recommendations(report)
        return report

    # ==================================================================
    # Phase discovery
    # ==================================================================

    def _discover_phases(self, output_dir: str) -> list[PhaseResult]:
        """Scan output dir and identify which phases executed."""
        phases = []
        for phase_name, dir_name in self.PHASE_DIRS.items():
            phase_dir = os.path.join(output_dir, dir_name)
            if not os.path.isdir(phase_dir):
                continue

            phase = PhaseResult(phase=phase_name, success=True)
            expected = self.EXPECTED_ARTIFACTS.get(phase_name, [])
            for rel_path, desc in expected:
                full = os.path.join(output_dir, rel_path)
                if os.path.exists(full):
                    phase.artifacts_found.append(desc)
                else:
                    phase.artifacts_missing.append(desc)

            phase.success = len(phase.artifacts_missing) == 0
            phases.append(phase)

        return phases

    # ==================================================================
    # Dimension 1: Correctness (40%)
    # ==================================================================

    def _eval_correctness(self, out_dir: str,
                           phases: list[PhaseResult]) -> DimensionScore:
        d = DimensionScore(name="正确性 (Correctness)", weight=0.40,
                           checks_total=5)

        # 1. Test pass rate (40 pts)
        test_json = os.path.join(out_dir, "04_tests", "pytest_result.json")
        if os.path.exists(test_json):
            try:
                tr = json.load(open(test_json))
            except Exception:
                tr = {}
            collected = tr.get("collected", 0)
            passed = tr.get("passed", 0)
            failed = tr.get("failed", 0)

            if collected > 0:
                rate = passed / max(collected, 1) * 100 if collected > 0 else 0
                if rate >= 100:
                    d.score += 40
                    d.details.append(f"✅ All {passed}/{collected} tests passed")
                    d.checks_passed += 1
                elif rate >= 80:
                    d.score += 30
                    d.details.append(f"⚠ {passed}/{collected} passed ({rate:.0f}%)")
                    d.checks_passed += 1
                elif rate >= 50:
                    d.score += 15
                    d.details.append(f"❌ {passed}/{collected} passed ({rate:.0f}%) — needs work")
                else:
                    d.details.append(f"❌ Only {passed}/{collected} passed ({rate:.0f}%)")
            else:
                d.details.append("⚠ No tests collected")
        else:
            d.details.append("❌ No test results found (04_tests/pytest_result.json missing)")

        # 2. Edge-case coverage (20 pts) — check for error-path test functions
        test_dir = os.path.join(out_dir, "04_tests", "tests")
        edge_count = self._count_edge_tests(test_dir)
        if edge_count >= 3:
            d.score += 20
            d.details.append(f"✅ {edge_count} error-path tests found")
            d.checks_passed += 1
        elif edge_count >= 1:
            d.score += 10
            d.details.append(f"⚠ Only {edge_count} error-path test(s)")
        else:
            d.details.append("⚠ No error-path tests detected")

        # 3. Coverage measurement (20 pts)
        try:
            cov = tr.get("coverage", {})
        except Exception:
            cov = {}
        line_cov = cov.get("line_rate", 0) or cov.get("line_rate", 0) or 0
        if isinstance(line_cov, str):
            try: line_cov = float(line_cov)
            except Exception: line_cov = 0
        if line_cov >= 85:
            d.score += 20
            d.details.append(f"✅ Coverage {line_cov:.0f}% ≥ 85%")
            d.checks_passed += 1
        elif line_cov >= 70:
            d.score += 10
            d.details.append(f"⚠ Coverage {line_cov:.0f}% — target 85%")
        elif line_cov > 0:
            d.score += 5
            d.details.append(f"❌ Low coverage: {line_cov:.0f}%")

        # 4. Regression consistency (10 pts) — run tests twice
        if os.path.exists(test_json):
            cons = self._check_consistency(test_dir, out_dir)
            if cons:
                d.score += 10
                d.details.append("✅ Tests consistent across runs")
                d.checks_passed += 1
            else:
                d.details.append("⚠ Tests flaky or failed on second run")

        # 5. Requirements traceability (10 pts) — FR→code→test links
        rtm_score = self._check_traceability(out_dir)
        if rtm_score >= 7:
            d.score += 10
            d.details.append(f"✅ Traceability: {rtm_score}/10")
            d.checks_passed += 1
        elif rtm_score >= 3:
            d.score += 5
            d.details.append(f"⚠ Traceability: {rtm_score}/10")

        return d

    # ==================================================================
    # Dimension 2: Completeness (25%)
    # ==================================================================

    def _eval_completeness(self, out_dir: str,
                            phases: list[PhaseResult]) -> DimensionScore:
        d = DimensionScore(name="完整性 (Completeness)", weight=0.25)
        phase_names = {p.phase for p in phases}

        # SRS completeness (30 pts)
        d.checks_total += 1
        if "requirements" in phase_names:
            srs_score = self._check_ieee_830(out_dir)
            d.score += srs_score
            d.details.append(f"SRS (IEEE 830): {srs_score}/30")
            if srs_score >= 20:
                d.checks_passed += 1

        # SDD completeness (25 pts)
        d.checks_total += 1
        if "design" in phase_names:
            sdd_score = self._check_ieee_1016(out_dir)
            d.score += sdd_score
            d.details.append(f"SDD (IEEE 1016): {sdd_score}/25")
            if sdd_score >= 15:
                d.checks_passed += 1

        # Code completeness (20 pts)
        d.checks_total += 1
        code_score = self._check_code_completeness(out_dir)
        d.score += code_score
        d.details.append(f"Code: {code_score}/20")
        if code_score >= 12:
            d.checks_passed += 1

        # Test completeness (15 pts)
        d.checks_total += 1
        test_score = self._check_test_completeness(out_dir)
        d.score += test_score
        d.details.append(f"Tests: {test_score}/15")
        if test_score >= 8:
            d.checks_passed += 1

        # Report completeness (10 pts)
        d.checks_total += 1
        rep_score = self._check_report_completeness(out_dir)
        d.score += rep_score
        d.details.append(f"Reports: {rep_score}/10")
        if rep_score >= 6:
            d.checks_passed += 1

        return d

    # ==================================================================
    # Dimension 3: Code Quality (20%)
    # ==================================================================

    def _eval_code_quality(self, out_dir: str) -> DimensionScore:
        d = DimensionScore(name="代码质量 (Code Quality)", weight=0.20)

        impl_src = os.path.join(out_dir, "03_implementation", "src")
        if not os.path.isdir(impl_src):
            impl_src = os.path.join(out_dir, "03_implementation")
        if not os.path.isdir(impl_src):
            d.details.append("❌ No source code found")
            return d

        py_files = list(Path(impl_src).rglob("*.py"))
        if not py_files:
            d.details.append("❌ No .py files found")
            return d

        # 1. ruff lint (30 pts)
        d.checks_total += 1
        if self._ruff_available:
            try:
                r = subprocess.run(
                    ["ruff", "check", impl_src, "--output-format", "concise"],
                    capture_output=True, text=True, timeout=30
                )
                issues = len([l for l in (r.stdout + r.stderr).split("\n")
                             if l.strip()])
                if issues == 0:
                    d.score += 30
                    d.details.append("✅ ruff: 0 issues")
                    d.checks_passed += 1
                elif issues <= 3:
                    d.score += 20
                    d.details.append(f"⚠ ruff: {issues} issues")
                elif issues <= 10:
                    d.score += 10
                    d.details.append(f"❌ ruff: {issues} issues")
                else:
                    d.details.append(f"❌ ruff: {issues} issues — needs major fix")
            except Exception:
                d.details.append("⚠ ruff check failed")
        else:
            d.score += 15  # give partial credit
            d.details.append("⚠ ruff not installed — partial score")

        # 2. mypy type check (25 pts)
        d.checks_total += 1
        if self._mypy_available:
            try:
                r = subprocess.run(
                    ["mypy", impl_src, "--ignore-missing-imports",
                     "--show-error-codes", "--no-error-summary"],
                    capture_output=True, text=True, timeout=60
                )
                type_issues = len([l for l in r.stdout.split("\n")
                                   if ": error:" in l])
                if type_issues == 0:
                    d.score += 25
                    d.details.append("✅ mypy: 0 type errors")
                    d.checks_passed += 1
                elif type_issues <= 5:
                    d.score += 15
                    d.details.append(f"⚠ mypy: {type_issues} type errors")
                elif type_issues <= 15:
                    d.score += 5
                    d.details.append(f"❌ mypy: {type_issues} type errors")
                else:
                    d.details.append(f"❌ mypy: {type_issues} type errors")
            except Exception:
                d.details.append("⚠ mypy check failed")
        else:
            d.score += 10
            d.details.append("⚠ mypy not installed — partial score")

        # 3. bandit security scan (20 pts)
        d.checks_total += 1
        if self._bandit_available:
            try:
                r = subprocess.run(
                    ["bandit", "-r", impl_src, "-f", "json", "-q"],
                    capture_output=True, text=True, timeout=30
                )
                try:
                    results = json.loads(r.stdout)
                    high = sum(1 for i in results.get("results", [])
                              if i.get("issue_severity") in ("HIGH", "MEDIUM"))
                except Exception:
                    high = 0
                if high == 0:
                    d.score += 20
                    d.details.append("✅ bandit: 0 security issues")
                    d.checks_passed += 1
                elif high <= 2:
                    d.score += 10
                    d.details.append(f"⚠ bandit: {high} security warning(s)")
                else:
                    d.details.append(f"❌ bandit: {high} security issues")
            except Exception:
                d.details.append("⚠ bandit check failed")
        else:
            d.score += 10
            d.details.append("⚠ bandit not installed — partial score")

        # 4. Cyclomatic complexity (15 pts)
        d.checks_total += 1
        complex_count = self._count_complex_functions(py_files)
        if complex_count == 0:
            d.score += 15
            d.details.append("✅ All functions ≤ 10 CC")
            d.checks_passed += 1
        elif complex_count <= 2:
            d.score += 8
            d.details.append(f"⚠ {complex_count} function(s) > 10 CC")

        # 5. Docstring coverage (10 pts)
        d.checks_total += 1
        doc_score = self._check_docstrings(py_files)
        d.score += doc_score
        d.details.append(f"Docstrings: {doc_score}/10")
        if doc_score >= 8:
            d.checks_passed += 1

        return d

    # ==================================================================
    # Dimension 4: Doc Quality (10%)
    # ==================================================================

    def _eval_doc_quality(self, out_dir: str) -> DimensionScore:
        d = DimensionScore(name="文档质量 (Doc Quality)", weight=0.10)

        # 1. Document control headers (25 pts)
        d.checks_total += 1
        dc_count = 0
        for doc in ["01_requirements/requirement_specification.md",
                    "02_design/architecture_design_spec.md",
                    "06_reports/executive_report.md"]:
            fp = os.path.join(out_dir, doc)
            if os.path.exists(fp):
                content = open(fp).read(4096)
                if any(kw in content for kw in ["文档控制", "Document Control"]):
                    dc_count += 1
        if dc_count >= 3:
            d.score += 25; d.checks_passed += 1
            d.details.append(f"✅ Document control in {dc_count}/3 reports")
        elif dc_count >= 1:
            d.score += 10
            d.details.append(f"⚠ Document control in {dc_count}/3")

        # 2. Diagram quality (35 pts)
        d.checks_total += 1
        diag_dir = os.path.join(out_dir, "02_design", "diagrams")
        diagram_count = 0
        valid_count = 0
        if os.path.isdir(diag_dir):
            for fn in os.listdir(diag_dir):
                if fn.endswith((".png", ".svg")):
                    diagram_count += 1
                    fp = os.path.join(diag_dir, fn)
                    if os.path.getsize(fp) > 200:
                        valid_count += 1
        if valid_count >= 5:
            d.score += 35; d.checks_passed += 1
            d.details.append(f"✅ {valid_count} valid diagram images")
        elif valid_count >= 2:
            d.score += 20
            d.details.append(f"⚠ {valid_count}/{diagram_count} valid diagrams")
        elif diagram_count > 0:
            d.score += 5
            d.details.append(f"❌ All {diagram_count} diagrams invalid (0-byte?)")

        # Also check .mmd files
        mmd_count = 0
        design_dir = os.path.join(out_dir, "02_design")
        if os.path.isdir(design_dir):
            mmd_count = len([f for f in os.listdir(design_dir) if f.endswith(".mmd")])
        if mmd_count >= 2: d.score += 5  # bonus

        # 3. Glossary (20 pts)
        d.checks_total += 1
        glossary_found = self._check_glossary(out_dir)
        if glossary_found:
            d.score += 20; d.checks_passed += 1
            d.details.append("✅ Glossary/术语表 present")
        else:
            d.details.append("⚠ No glossary found")

        # 4. RTM completeness (20 pts)
        d.checks_total += 1
        rtm = self._check_rtm(out_dir)
        if rtm >= 15:
            d.score += 20; d.checks_passed += 1
            d.details.append(f"✅ RTM {rtm}/20")
        elif rtm >= 8:
            d.score += 10
            d.details.append(f"⚠ RTM {rtm}/20")

        return d

    # ==================================================================
    # Dimension 5: Maintainability (5%)
    # ==================================================================

    def _eval_maintainability(self, out_dir: str) -> DimensionScore:
        d = DimensionScore(name="可维护性 (Maintainability)", weight=0.05)

        impl = os.path.join(out_dir, "03_implementation")
        if not os.path.isdir(impl):
            d.details.append("❌ No implementation dir")
            return d

        # 1. No circular imports (30 pts)
        d.checks_total += 1
        src = os.path.join(impl, "src")
        cycles = self._check_circular_imports(src) if os.path.isdir(src) else 0
        if cycles == 0:
            d.score += 30; d.checks_passed += 1
            d.details.append("✅ No circular imports")
        elif cycles <= 1:
            d.details.append(f"⚠ {cycles} circular import(s)")

        # 2. Interface isolation (35 pts)
        d.checks_total += 1
        abc_count = self._count_abcs(src) if os.path.isdir(src) else 0
        if abc_count >= 1:
            d.score += 35; d.checks_passed += 1
            d.details.append(f"✅ {abc_count} ABC/Protocol definitions")
        elif abc_count > 0:
            d.score += 15
        else:
            d.details.append("⚠ No abstract interfaces found")

        # 3. Config externalization (35 pts)
        d.checks_total += 1
        env_example = os.path.join(impl, ".env.example")
        config_py = os.path.join(impl, "src", "config.py")
        has_env = os.path.exists(env_example)
        has_config = os.path.exists(config_py)
        if has_env and has_config:
            d.score += 35; d.checks_passed += 1
            d.details.append("✅ .env.example + config.py present")
        elif has_env:
            d.score += 20
            d.details.append("⚠ .env.example present, no config.py")
        elif has_config:
            d.score += 20
            d.details.append("⚠ config.py present, no .env.example")
        else:
            d.details.append("⚠ No config externalization")

        return d

    # ==================================================================
    # Sub-checks
    # ==================================================================

    def _count_edge_tests(self, test_dir: str) -> int:
        if not os.path.isdir(test_dir):
            return 0
        count = 0
        edge_keywords = {"error", "invalid", "boundary", "edge", "exception",
                         "fail", "zero", "empty", "none", "null", "overflow",
                         "divide", "raise", "raises", "pytest"}
        for fn in os.listdir(test_dir):
            if not fn.endswith(".py"):
                continue
            try:
                content = open(os.path.join(test_dir, fn)).read()
                for kw in edge_keywords:
                    if kw in content.lower():
                        count += 1
                        break
            except Exception:
                pass
        return count

    def _check_consistency(self, test_dir: str, out_dir: str) -> bool:
        """Run tests twice and check same results."""
        if not test_dir or not os.path.isdir(test_dir):
            return False
        try:
            r1 = subprocess.run(
                ["python", "-m", "pytest", test_dir, "-q", "--no-header", "--tb=no"],
                cwd=os.path.join(out_dir, "03_implementation"),
                capture_output=True, text=True, timeout=60
            )
            r2 = subprocess.run(
                ["python", "-m", "pytest", test_dir, "-q", "--no-header", "--tb=no"],
                cwd=os.path.join(out_dir, "03_implementation"),
                capture_output=True, text=True, timeout=60
            )
            return r1.returncode == r2.returncode
        except Exception:
            return False

    def _check_traceability(self, out_dir: str) -> int:
        """Score FR↔code↔test traceability (0-10)."""
        score = 0
        reqs_json = os.path.join(out_dir, "01_requirements",
                                 "structured_requirements.json")
        if not os.path.exists(reqs_json):
            return 0
        try:
            reqs = json.load(open(reqs_json))
        except Exception:
            return 0

        frs = reqs.get("functional_requirements", [])
        if not frs:
            return 0

        impl_src = os.path.join(out_dir, "03_implementation", "src")
        test_dir = os.path.join(out_dir, "04_tests", "tests")

        for fr in frs[:10]:
            fr_id = fr.get("id", "")
            if not fr_id: continue

            found_in_code = False
            if os.path.isdir(impl_src):
                for rf in Path(impl_src).rglob("*.py"):
                    try:
                        if fr_id in open(rf).read(4096):
                            found_in_code = True
                            break
                    except Exception:
                        pass
            if found_in_code: score += 0.5

            found_in_test = False
            if os.path.isdir(test_dir):
                for tf in Path(test_dir).rglob("test_*.py"):
                    try:
                        if fr_id in open(tf).read(4096):
                            found_in_test = True
                            break
                    except Exception:
                        pass
            if found_in_test: score += 0.5

        return min(10, int(score))

    def _check_ieee_830(self, out_dir: str) -> int:
        """Score IEEE 830 SRS completeness (0-30)."""
        srs_path = os.path.join(out_dir, "01_requirements",
                                "requirement_specification.md")
        if not os.path.exists(srs_path):
            return 0
        content = open(srs_path).read()
        score = 0
        sections = [
            ("引言", 5), ("目的", 4), ("范围", 3), ("术语表", 4),
            ("总体描述", 5), ("具体需求", 5), ("功能需求", 4),
        ]
        for kw, pts in sections:
            if kw in content: score += pts
        return min(30, score)

    def _check_ieee_1016(self, out_dir: str) -> int:
        """Score IEEE 1016 SDD completeness (0-25)."""
        sdd_path = os.path.join(out_dir, "02_design",
                                "architecture_design_spec.md")
        if not os.path.exists(sdd_path):
            return 0
        content = open(sdd_path).read()
        score = 0
        sections = [
            ("设计概述", 5), ("架构视图", 5), ("数据流图", 4),
            ("类图", 3), ("ER图", 2), ("技术栈", 3), ("安全", 3),
        ]
        for kw, pts in sections:
            if kw in content: score += pts
        return min(25, score)

    def _check_code_completeness(self, out_dir: str) -> int:
        """Score code file completeness (0-20)."""
        score = 0
        impl = os.path.join(out_dir, "03_implementation")
        checks = [
            ("src/__init__.py", 3), ("src/main.py", 3),
            ("requirements.txt", 2), (".env.example", 3),
            ("pyproject.toml", 3), ("Makefile", 2), ("Dockerfile", 2),
            ("README.md", 2),
        ]
        for fname, pts in checks:
            if os.path.exists(os.path.join(impl, fname)):
                score += pts
        src = os.path.join(impl, "src")
        if os.path.isdir(src):
            py_count = len(list(Path(src).rglob("*.py")))
            if py_count >= 5: score += 3
            elif py_count >= 3: score += 1
        return min(20, score)

    def _check_test_completeness(self, out_dir: str) -> int:
        """Score test completeness (0-15)."""
        score = 0
        test_dir = os.path.join(out_dir, "04_tests", "tests")
        if os.path.isdir(test_dir):
            test_files = list(Path(test_dir).rglob("test_*.py"))
            if len(test_files) >= 3:
                score += 8
            elif len(test_files) >= 1:
                score += 4
            # Has conftest
            if os.path.exists(os.path.join(test_dir, "conftest.py")):
                score += 3
            # Has test_*.py with actual assertions
            for tf in test_files:
                try:
                    content = open(tf).read()
                    if len(re.findall(r'assert\s+', content)) >= 3:
                        score += 2
                        break
                except Exception:
                    pass
        return min(15, score)

    def _check_report_completeness(self, out_dir: str) -> int:
        """Score final report completeness (0-10)."""
        score = 0
        rep_path = os.path.join(out_dir, "06_reports", "executive_report.md")
        if os.path.exists(rep_path):
            content = open(rep_path).read()
            for kw in ["Dashboard", "质量门", "需求追溯", "阶段摘要", "建议"]:
                if kw in content: score += 2
        return min(10, score)

    def _check_glossary(self, out_dir: str) -> bool:
        for doc in ["01_requirements/requirement_specification.md",
                    "02_design/architecture_design_spec.md"]:
            fp = os.path.join(out_dir, doc)
            if os.path.exists(fp):
                content = open(fp).read(4096)
                if "术语表" in content or "Glossary" in content:
                    return True
        return False

    def _check_rtm(self, out_dir: str) -> int:
        """Score RTM completeness (0-20)."""
        rep = os.path.join(out_dir, "06_reports", "executive_report.md")
        if not os.path.exists(rep):
            return 0
        content = open(rep).read()
        score = 0
        if "需求追溯矩阵" in content or "RTM" in content:
            score += 5
        # Count actual RTM rows
        rtm_section = content.split("需求追溯")[-1] if "需求追溯" in content else ""
        rows = len([l for l in rtm_section.split("\n") if l.startswith("| FR-")])
        score += min(10, rows * 2)
        # Check for ✅/🟡/⚠️ coverage markers
        score += min(5, len(re.findall(r'[✅🟡⚠️]', rtm_section)))
        return min(20, score)

    @staticmethod
    def _count_complex_functions(py_files: list) -> int:
        """Count functions with CC > 10."""
        count = 0
        for fp in py_files:
            try:
                tree = ast.parse(open(fp).read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cc = 1  # base complexity
                        for child in ast.walk(node):
                            if isinstance(child, (ast.If, ast.While, ast.For,
                                                  ast.ExceptHandler, ast.BoolOp,
                                                  ast.And, ast.Or)):
                                cc += 1
                        if cc > 10:
                            count += 1
            except Exception:
                pass
        return count

    @staticmethod
    def _check_docstrings(py_files: list) -> int:
        """Score docstring coverage (0-10)."""
        total_fn = 0
        with_docs = 0
        for fp in py_files:
            try:
                tree = ast.parse(open(fp).read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef)):
                        total_fn += 1
                        if ast.get_docstring(node):
                            with_docs += 1
            except Exception:
                pass
        if total_fn == 0: return 0
        ratio = with_docs / total_fn * 100
        if ratio >= 90: return 10
        if ratio >= 70: return 7
        if ratio >= 50: return 4
        return 1

    @staticmethod
    def _check_circular_imports(src_dir: str) -> int:
        """Heuristic: count mutual import pairs."""
        if not os.path.isdir(src_dir): return 0
        imports = {}
        for fp in Path(src_dir).rglob("*.py"):
            try:
                tree = ast.parse(open(fp).read())
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.module:
                            imported.add(node.module.split(".")[0])
                imports[fp.stem] = imported
            except Exception:
                pass
        cycles = 0
        for a, a_imports in imports.items():
            for b in a_imports:
                if b in imports and a in imports[b]:
                    cycles += 1
        return cycles // 2  # each counted twice

    @staticmethod
    def _count_abcs(src_dir: str) -> int:
        """Count ABC/Protocol base classes."""
        if not os.path.isdir(src_dir): return 0
        count = 0
        for fp in Path(src_dir).rglob("*.py"):
            try:
                content = open(fp).read()
                if "ABC" in content or "Protocol" in content or "abstractmethod" in content:
                    count += 1
            except Exception:
                pass
        return count

    # ==================================================================
    # Recommendations
    # ==================================================================

    def _generate_recommendations(self, report: EvalReport) -> list:
        recs = []
        for d in report.dimensions:
            if d.score < 60:
                recs.append(f"🔧 **{d.name}**: {d.score}/100 — priority improvement area")
            elif d.score < 80:
                recs.append(f"📝 **{d.name}**: {d.score}/100 — could be improved")

        # Phase-specific
        for p in report.phases:
            if p.artifacts_missing:
                missing = ", ".join(p.artifacts_missing[:3])
                recs.append(f"📦 **{p.phase}**: missing — {missing}")

        if not recs:
            recs.append("✅ All dimensions above threshold. Excellent quality!")
        return recs

    # ==================================================================
    # Report generation
    # ==================================================================

    def print_report(self, report: EvalReport):
        """Print formatted evaluation report to stdout."""
        lines = [
            f"\n{'='*70}",
            f"  DevAgent Pipeline Evaluation — {report.case_name}",
            f"{'='*70}",
            f"\n  📊 Overall Score: {report.overall_score:.1f}/100",
            f"\n  Dimensions:",
        ]
        for d in report.dimensions:
            bar = "█" * (d.score // 5) + "░" * (20 - d.score // 5)
            lines.append(f"    {d.name:30s} [{bar}] {d.score:>3d}/100 (×{d.weight:.2f})")
            for det in d.details[:3]:
                lines.append(f"      {det}")

        lines.append(f"\n  Phases ({len(report.phases)}):")
        for p in report.phases:
            icon = "✅" if p.success else "⚠️"
            lines.append(f"    {icon} {p.phase:15s} — "
                        f"{len(p.artifacts_found)} artifacts"
                        + (f" (missing: {', '.join(p.artifacts_missing)[:60]})"
                           if p.artifacts_missing else ""))

        if report.recommendations:
            lines.append(f"\n  Recommendations:")
            for r in report.recommendations:
                lines.append(f"    {r}")

        lines.append(f"\n{'='*70}\n")
        print("\n".join(lines))

    def save_report(self, report: EvalReport, output_path: str):
        """Save evaluation report as JSON."""
        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"✅ Evaluation report: {output_path}")


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="DevAgent Pipeline Evaluator")
    ap.add_argument("--output_dir", "-d", default="",
                    help="Pipeline output dir to evaluate")
    ap.add_argument("--case", "-c", default="", help="Case name")
    ap.add_argument("--suite", default="",
                    choices=["", "quick", "full"],
                    help="Run a suite: quick (no ext tools) or full")
    ap.add_argument("--report", default="", help="JSON report output path")
    args = ap.parse_args()

    evaluator = PipelineEvaluator()

    if args.suite == "quick" and args.output_dir:
        report = evaluator.evaluate_quick(args.output_dir, args.case)
        evaluator.print_report(report)
        if args.report:
            evaluator.save_report(report, args.report)

    elif args.output_dir:
        report = evaluator.evaluate(args.output_dir, args.case)
        evaluator.print_report(report)
        if args.report:
            evaluator.save_report(report, args.report)

    else:
        print("Usage:")
        print("  python -m devagent.benchmarks.pipeline_evaluator \\")
        print("      --output_dir ./outputs/run_task_xxx --case calculator")
        print("\n  python -m devagent.benchmarks.pipeline_evaluator \\")
        print("      --output_dir ./outputs/run_task_xxx --suite quick")
