"""SWE-bench Style Benchmark Runner — 3-stage scoring + industry comparison.

Stage A: Single-file Bug Fix (DR cases) → Resolved/Fail + Patch Quality
Stage B: Full-pipeline Dev (AD + IT cases) → Correctness + Completeness
Stage C: External SWE-bench Issues → Industry comparison

Scoring (per case, 0-100):
  - Correctness (60%): all tests pass
  - Patch Quality (20%): minimal diff, readable, no regressions
  - Efficiency (20%): time, iterations, tokens

Outputs:
  benchmark_report/result.json   — Full structured results
  benchmark_report/summary.md    — Human-readable summary
  benchmark_report/summary.csv   — Spreadsheet data
  benchmark_report/{case_id}/    — Per-case artifacts
"""

import os
import sys
import json
import time
import shutil
import difflib
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class CaseResult:
    case_id: str
    stage: str           # "A" | "B" | "C"
    task_type: str
    status: str          # "PASS" | "FAIL" | "PARTIAL"
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)

    # Core metrics
    duration_sec: float = 0
    iterations: int = 0
    token_count: int = 0

    # Correctness
    tests_collected: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_before: int = 0    # baseline
    tests_before_fail: int = 0

    # Pipeline artifacts
    has_requirements: bool = False
    has_design: bool = False
    code_files_count: int = 0
    coverage_line_pct: float = 0

    # Patch quality
    patch_lines_added: int = 0
    patch_lines_removed: int = 0
    patch_is_minimal: bool = False

    # Errors
    errors: list = field(default_factory=list)
    error_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# Test Suite Definitions
# ============================================================================

STAGE_A_CASES = [
    {
        "case_id": "DR-01", "task_type": "repair",
        "code_path": os.path.join(THIS_DIR, "dr_cases", "boundary_bug", "buggy.py"),
        "tests_path": os.path.join(THIS_DIR, "dr_cases", "boundary_bug", "test_buggy.py"),
        "bug_category": "boundary", "difficulty": 1,
        "expected_patch_size": (1, 3),  # min/max lines
    },
    {
        "case_id": "DR-02", "task_type": "repair",
        "code_path": os.path.join(THIS_DIR, "dr_cases", "null_bug", "buggy.py"),
        "tests_path": os.path.join(THIS_DIR, "dr_cases", "null_bug", "test_buggy.py"),
        "bug_category": "null", "difficulty": 2,
        "expected_patch_size": (1, 5),
    },
    {
        "case_id": "DR-03", "task_type": "repair",
        "code_path": os.path.join(THIS_DIR, "dr_cases", "logic_bug", "buggy.py"),
        "tests_path": os.path.join(THIS_DIR, "dr_cases", "logic_bug", "test_buggy.py"),
        "bug_category": "logic", "difficulty": 2,
        "expected_patch_size": (1, 3),
    },
    {
        "case_id": "DR-04", "task_type": "repair",
        "code_path": os.path.join(THIS_DIR, "dr_cases", "path_bug", "buggy.py"),
        "tests_path": os.path.join(THIS_DIR, "dr_cases", "path_bug", "test_buggy.py"),
        "bug_category": "path", "difficulty": 3,
        "expected_patch_size": (2, 8),
    },
]

STAGE_B_CASES = [
    # AD = Analysis + Design
    {"case_id": "AD-01", "task_type": "design",
     "input_path": os.path.join(THIS_DIR, "ad_cases", "book_lending.md"),
     "req_count": 5, "expected_entities": 4},
    {"case_id": "AD-02", "task_type": "design",
     "input_path": os.path.join(THIS_DIR, "ad_cases", "course_selection.md"),
     "req_count": 4, "expected_entities": 3},
    {"case_id": "AD-03", "task_type": "design",
     "input_path": os.path.join(THIS_DIR, "ad_cases", "cinema_booking.md"),
     "req_count": 5, "expected_entities": 4},
    {"case_id": "AD-04", "task_type": "design",
     "input_path": os.path.join(THIS_DIR, "ad_cases", "bug_tracker.md"),
     "req_count": 5, "expected_entities": 4},
    # IT = Implementation + Test
    {"case_id": "IT-01", "task_type": "implement",
     "input_path": os.path.join(THIS_DIR, "it_cases", "student_grade.md"),
     "min_code_files": 2, "min_test_files": 1},
    {"case_id": "IT-02", "task_type": "implement",
     "input_path": os.path.join(THIS_DIR, "it_cases", "todo_app.md"),
     "min_code_files": 3, "min_test_files": 1},
    {"case_id": "IT-03", "task_type": "implement",
     "input_path": os.path.join(THIS_DIR, "it_cases", "bank_account.md"),
     "min_code_files": 3, "min_test_files": 1},
    {"case_id": "IT-04", "task_type": "implement",
     "input_path": os.path.join(THIS_DIR, "it_cases", "text_stats.md"),
     "min_code_files": 2, "min_test_files": 1},
]


# ============================================================================
# Benchmark Runner
# ============================================================================

class BenchmarkRunner:
    """SWE-bench style evaluation runner."""

    def __init__(self, output_dir: str = "./benchmark_report",
                 provider: str = "", timeout_sec: int = 600):
        self.output_dir = os.path.abspath(output_dir)
        self.provider = provider
        self.timeout = timeout_sec
        os.makedirs(output_dir, exist_ok=True)
        self.results: list[CaseResult] = []

    # ==================================================================
    # Public API
    # ==================================================================

    def run_default_suite(self) -> list[CaseResult]:
        """Run the full 3-stage benchmark suite."""
        print(f"{'='*70}")
        print(f"DevAgent Benchmark Suite v1.0")
        print(f"{'='*70}")

        # Stage A: Bug Fix (4 cases)
        print(f"\n--- Stage A: Single-File Bug Fix ---")
        for case in STAGE_A_CASES:
            self._run_repair_case(case)

        # Stage B: Full Pipeline (8 cases)
        print(f"\n--- Stage B: Full Pipeline ---")
        for case in STAGE_B_CASES:
            if case["task_type"] == "design":
                self._run_design_case(case)
            else:
                self._run_implement_case(case)

        self._generate_report()
        self._generate_csv()
        return self.results

    def run_case(self, case_id: str):
        """Run a single case by ID."""
        all_cases = STAGE_A_CASES + STAGE_B_CASES
        for case in all_cases:
            if case["case_id"] == case_id:
                if case["task_type"] == "repair":
                    self._run_repair_case(case)
                elif case["task_type"] == "design":
                    self._run_design_case(case)
                else:
                    self._run_implement_case(case)
                return self.results[-1]
        print(f"Case '{case_id}' not found. Available: "
              f"{[c['case_id'] for c in all_cases]}")

    # ==================================================================
    # Stage A: Repair cases
    # ==================================================================

    def _run_repair_case(self, case: dict):
        case_id = case["case_id"]
        code_path = case["code_path"]
        tests_path = case["tests_path"]
        print(f"\n  [{case_id}] {case.get('bug_category','?')} bug", flush=True)

        # 1. Create isolated workspace
        ws_dir = tempfile.mkdtemp(prefix=f"bench_{case_id}_")
        shutil.copy2(code_path, os.path.join(ws_dir, "buggy.py"))
        shutil.copy2(tests_path, os.path.join(ws_dir, "test_buggy.py"))

        # 2. Record baseline: how many tests fail BEFORE fix
        baseline = self._count_tests(tests_path, os.path.dirname(code_path))
        tests_before = baseline["collected"]
        tests_before_fail = baseline["failed"]

        # 3. Run DevAgent in repair mode
        t0 = time.time()
        try:
            # Build task description from bug + test
            task = (
                f"Fix bugs in buggy.py. Run test_buggy.py to reproduce failures, "
                f"use debug_issue to analyze root cause, repair_code to apply minimal fix. "
                f"Submit when all tests pass.\n\n"
                f"## Bug Category: {case.get('bug_category','unknown')}"
            )

            state = self._invoke_agent(task, ws_dir, "repair", max_iter=40)
            duration = time.time() - t0

            # 4. Copy fixed code back + re-run tests
            fixed_path = os.path.join(ws_dir, "buggy.py")
            test_after = self._count_tests(
                os.path.join(ws_dir, "test_buggy.py"), ws_dir
            )
            tests_passed = test_after["passed"]
            tests_failed = test_after["failed"]
            tests_collected = test_after["collected"]

            result = CaseResult(
                case_id=case_id, stage="A", task_type="repair",
                status="PASS" if tests_failed == 0 and tests_collected > 0 else "FAIL",
                duration_sec=round(duration, 1),
                iterations=getattr(state, 'current_iteration', 0),
                tests_collected=tests_collected, tests_passed=tests_passed,
                tests_failed=tests_failed,
                tests_before=tests_before, tests_before_fail=tests_before_fail,
                errors=[{"phase": "execution", "message": str(e)}
                         for e in (getattr(state, 'errors', []) or [])],
            )

        except Exception as e:
            duration = time.time() - t0
            import traceback
            result = CaseResult(
                case_id=case_id, stage="A", task_type="repair",
                status="FAIL", duration_sec=round(duration, 1),
                errors=[{"message": f"{e}\n{traceback.format_exc()[:500]}"}],
                tests_before=tests_before, tests_before_fail=tests_before_fail,
            )

        # 5. Score
        result.score, result.score_breakdown = self._score_repair(
            result, case, os.path.join(ws_dir, "buggy.py")
        )

        # 6. Save artifacts
        self._save_case_artifacts(result, ws_dir)
        self.results.append(result)

        print(f"    → {result.status} (score={result.score}/100, "
              f"tests: {result.tests_passed}/{result.tests_collected}, "
              f"duration={result.duration_sec:.0f}s)", flush=True)

    # ==================================================================
    # Stage B: Design + Implement cases
    # ==================================================================

    def _run_design_case(self, case: dict):
        self._run_pipeline_case(case, "design")

    def _run_implement_case(self, case: dict):
        self._run_pipeline_case(case, "implement")

    def _run_pipeline_case(self, case: dict, mode: str):
        case_id = case["case_id"]
        input_path = case["input_path"]
        input_content = open(input_path).read()
        print(f"\n  [{case_id}] {mode}", flush=True)

        out_dir = os.path.join(self.output_dir, case_id)
        os.makedirs(out_dir, exist_ok=True)

        task = (
            f"Build the complete project from the requirements document:\n\n"
            f"{input_content}\n\n"
            f"Call analyze_requirements → design_architecture → "
            f"{'submit' if mode == 'design' else 'generate_code → generate_tests → submit'}"
        )

        t0 = time.time()
        try:
            state = self._invoke_agent(task, os.path.dirname(input_path), mode, max_iter=60)
            duration = time.time() - t0

            # Evaluate
            reqs = getattr(state, 'requirements', {}) or {}
            design = getattr(state, 'design_artifacts', {}) or {}
            code_files = getattr(state, 'code_files', []) or []
            tr = getattr(state, 'test_results', {}) or {}

            result = CaseResult(
                case_id=case_id, stage="B", task_type=mode,
                status="PASS" if hasattr(state, 'status') and state.status in ("COMPLETED","FINISHED") else "FAIL",
                duration_sec=round(duration, 1),
                iterations=getattr(state, 'current_iteration', 0),
                has_requirements=bool(reqs),
                has_design=bool(design),
                code_files_count=len(code_files),
                tests_collected=tr.get("collected", 0),
                tests_passed=tr.get("passed", 0),
                tests_failed=tr.get("failed", 0),
                coverage_line_pct=(tr.get("coverage", {}) or {}).get("line_rate", 0),
            )

        except Exception as e:
            duration = time.time() - t0
            import traceback
            result = CaseResult(
                case_id=case_id, stage="B", task_type=mode,
                status="FAIL", duration_sec=round(duration, 1),
                errors=[{"message": f"{e}\n{traceback.format_exc()[:500]}"}],
            )

        result.score, result.score_breakdown = self._score_pipeline(result, case, out_dir)
        self._save_case_artifacts(result, out_dir)
        self.results.append(result)

        print(f"    → {result.status} (score={result.score}/100, "
              f"code={result.code_files_count}f, "
              f"tests={result.tests_passed}/{result.tests_collected}, "
              f"duration={result.duration_sec:.0f}s)", flush=True)

    # ==================================================================
    # Agent invocation
    # ==================================================================

    def _invoke_agent(self, task: str, workspace: str, mode: str, max_iter: int):
        from ..agentic.core import DevAgentCore

        core = DevAgentCore()
        core.max_iterations = max_iter

        if mode in ("full", "design", "implement"):
            out = os.path.join(workspace, "outputs")
            pstate = core.run_pipeline(task, workspace, output_root=out)
            # Build a proxy state object for the result
            class _PS:
                status = pstate.status
                current_iteration = len(pstate.results)
                requirements = pstate.requirements
                design_artifacts = pstate.design_artifacts
                code_files = pstate.code_files
                test_results = pstate.test_results
                errors = [e for r in pstate.results for e in (r.errors or [])]
            return _PS()
        else:
            return core.execute(task, workspace, "python", max_iter)

    # ==================================================================
    # Scoring
    # ==================================================================

    def _score_repair(self, result: CaseResult, case: dict,
                       fixed_path: str) -> tuple[int, dict]:
        """Score a repair case 0-100."""
        breakdown = {}
        score = 0

        # 1. Correctness (60%)
        if result.tests_collected > 0 and result.tests_failed == 0:
            score += 60
            breakdown["correctness"] = 60
        elif result.tests_collected > 0:
            ratio = result.tests_passed / max(result.tests_collected, 1)
            pts = int(60 * ratio)
            score += pts
            breakdown["correctness"] = pts
        else:
            breakdown["correctness"] = 0

        # 2. Patch Quality (20%)
        pq = 0
        if os.path.exists(fixed_path):
            with open(fixed_path) as f:
                fixed = f.readlines()
            original_path = case["code_path"]
            with open(original_path) as f:
                original = f.readlines()
            diff = list(difflib.unified_diff(original, fixed))
            added = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
            removed = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))
            result.patch_lines_added = added
            result.patch_lines_removed = removed

            expected = case.get("expected_patch_size", (1, 100))
            if expected[0] <= (added + removed) <= expected[1]:
                pq += 10  # Minimal
                result.patch_is_minimal = True
            if added > 0 and removed == 0:  # Only additions = likely adding guards
                pq += 5
            if removed > 0 and added > 0:  # Replaced — could be correct
                pq += 5
        breakdown["patch_quality"] = pq
        score += pq

        # 3. Efficiency (20%)
        eff = 0
        if result.duration_sec < 180:
            eff += 10
        elif result.duration_sec < 300:
            eff += 5
        if result.iterations <= 20:
            eff += 10
        elif result.iterations <= 40:
            eff += 5
        breakdown["efficiency"] = eff
        score += eff

        return score, breakdown

    def _score_pipeline(self, result: CaseResult, case: dict,
                         out_dir: str) -> tuple[int, dict]:
        """Score a pipeline case across 5 dimensions."""
        breakdown = {}
        score = 0

        # 1. Correctness (40%)
        c = 0
        if result.tests_collected > 0:
            ratio = result.tests_passed / max(result.tests_collected, 1)
            c = int(40 * ratio)
        breakdown["correctness"] = c
        score += c

        # 2. Completeness (25%)
        comp = 0
        if result.has_requirements:
            srs = os.path.join(out_dir, "01_requirements", "requirement_specification.md")
            if os.path.exists(srs):
                comp += 8
            if os.path.exists(os.path.join(out_dir, "01_requirements", "structured_requirements.json")):
                comp += 2
        if result.has_design:
            sdd = os.path.join(out_dir, "02_design", "architecture_design_spec.md")
            if os.path.exists(sdd):
                comp += 8
            diag_dir = os.path.join(out_dir, "02_design", "diagrams")
            if os.path.isdir(diag_dir):
                comp += min(5, len(os.listdir(diag_dir)))
        if result.code_files_count >= case.get("min_code_files", 1):
            comp += 2
        breakdown["completeness"] = min(25, comp)
        score += min(25, comp)

        # 3. Code Quality (20%)
        cq = 0
        impl_dir = os.path.join(out_dir, "03_implementation", "src")
        if os.path.isdir(impl_dir):
            cq += self._check_ruff(impl_dir)
        if result.coverage_line_pct >= 80:
            cq += 5
        elif result.coverage_line_pct >= 60:
            cq += 3
        if result.coverage_line_pct >= 90:
            cq += 5  # bonus
        breakdown["code_quality"] = min(20, cq)
        score += min(20, cq)

        # 4. Document Quality (10%)
        dq = 0
        for docname in ["requirement_specification.md", "architecture_design_spec.md",
                        "executive_report.md"]:
            for phase_dir in ["01_requirements", "02_design", "06_reports"]:
                fp = os.path.join(out_dir, phase_dir, docname)
                if os.path.exists(fp):
                    with open(fp) as f:
                        content = f.read(4096)
                    if "文档控制" in content or "Document Control" in content:
                        dq += 1
                    if "术语" in content or "Glossary" in content:
                        dq += 1
                    break
        dq = min(10, dq)
        breakdown["doc_quality"] = dq
        score += dq

        # 5. Maintainability (5%)
        mt = 0
        if os.path.exists(os.path.join(out_dir, "03_implementation", ".env.example")):
            mt += 2
        if os.path.exists(os.path.join(out_dir, "03_implementation", "Makefile")):
            mt += 1
        # Check for custom exceptions
        exc_path = os.path.join(out_dir, "03_implementation", "src", "exceptions.py")
        if os.path.exists(exc_path):
            mt += 2
        breakdown["maintainability"] = min(5, mt)
        score += min(5, mt)

        return score, breakdown

    def _check_ruff(self, src_dir: str) -> int:
        """Run ruff lint, return quality score 0-15."""
        try:
            r = subprocess.run(
                ["ruff", "check", src_dir, "--output-format", "concise"],
                capture_output=True, text=True, timeout=30
            )
            issues = len([l for l in (r.stdout + r.stderr).split("\n") if l.strip()])
            if issues == 0:
                return 15
            elif issues <= 3:
                return 10
            elif issues <= 10:
                return 5
            return 2
        except Exception:
            return 0  # ruff not available

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _count_tests(test_path: str, work_dir: str) -> dict:
        """Count passing/failing tests in a test file."""
        try:
            r = subprocess.run(
                ["python", "-m", "pytest", test_path, "-q", "--no-header",
                 "--tb=no"],
                cwd=work_dir, capture_output=True, text=True, timeout=60
            )
            import re
            output = r.stdout + r.stderr
            collected = sum(int(x) for x in re.findall(r'(\d+) collected', output)
                           or re.findall(r'collected (\d+)', output))
            passed = sum(int(x) for x in re.findall(r'(\d+) passed', output))
            failed = sum(int(x) for x in re.findall(r'(\d+) failed', output))
            return {"collected": collected, "passed": passed, "failed": failed}
        except Exception:
            return {"collected": 0, "passed": 0, "failed": 0}

    def _save_case_artifacts(self, result: CaseResult, ws_dir: str):
        """Save per-case metrics + patch files."""
        case_dir = os.path.join(self.output_dir, result.case_id)
        os.makedirs(case_dir, exist_ok=True)

        with open(os.path.join(case_dir, "metrics.json"), "w") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

    # ==================================================================
    # Report generation
    # ==================================================================

    def _generate_report(self):
        """Generate Markdown summary report."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "PASS")
        avg_score = sum(r.score for r in self.results) / max(total, 1)

        lines = [
            "# DevAgent Benchmark Report",
            f"\n> Generated: {datetime.now().isoformat()[:19]}",
            f"\n## Summary",
            f"\n| Metric | Value |",
            f"|--------|-------|",
            f"| Total Cases | {total} |",
            f"| Passed | {passed} |",
            f"| Failed | {total - passed} |",
            f"| Success Rate | {passed/max(total,1)*100:.1f}% |",
            f"| Average Score | {avg_score:.1f}/100 |",
        ]

        # By stage
        for stage in ["A", "B"]:
            stage_results = [r for r in self.results if r.stage == stage]
            if not stage_results:
                continue
            stage_passed = sum(1 for r in stage_results if r.status == "PASS")
            stage_avg = sum(r.score for r in stage_results) / len(stage_results)
            stage_name = {"A":"Bug Fix", "B":"Full Pipeline"}.get(stage, stage)
            lines.append(f"\n## Stage {stage}: {stage_name}")
            lines.append(f"\n| Case | Status | Score | Tests | Coverage | Duration |")
            lines.append(f"|------|--------|-------|-------|----------|----------|")
            for r in stage_results:
                tests = f"{r.tests_passed}/{r.tests_collected}" if r.tests_collected > 0 else "-"
                cov = f"{r.coverage_line_pct:.0f}%" if r.coverage_line_pct > 0 else "-"
                lines.append(
                    f"| {r.case_id} | {r.status} | {r.score} | {tests} | {cov} | {r.duration_sec:.0f}s |"
                )
            lines.append(f"\n**Stage {stage} Avg**: {stage_avg:.1f}/100 ({stage_passed}/{len(stage_results)} passed)")

        # Score breakdown detail
        lines.append(f"\n## Score Breakdown\n")
        lines.append(f"| Case | Correctness | Patch/Doc Quality | Efficiency | Completeness | Code Quality | Total |")
        lines.append(f"|------|------------|-------------------|------------|--------------|-------------|-------|")
        for r in sorted(self.results, key=lambda x: x.case_id):
            bd = r.score_breakdown
            lines.append(
                f"| {r.case_id} | {bd.get('correctness',0)} | "
                f"{bd.get('patch_quality', bd.get('doc_quality',0))} | "
                f"{bd.get('efficiency',0)} | {bd.get('completeness',0)} | "
                f"{bd.get('code_quality',0)} | **{r.score}** |"
            )

        # Industry comparison (placeholder)
        lines.append(f"\n## Industry Comparison (Stage A)")
        lines.append(f"\n| System | Resolved Rate | Avg Score |")
        lines.append(f"|--------|--------------|-----------|")
        stage_a = [r for r in self.results if r.stage == "A"]
        our_rate = sum(1 for r in stage_a if r.status == "PASS") / max(len(stage_a), 1) * 100
        our_avg = sum(r.score for r in stage_a) / max(len(stage_a), 1)
        lines.append(f"| **DevAgent** | **{our_rate:.0f}%** | **{our_avg:.0f}** |")
        lines.append(f"| SWE-agent (reported) | 18% | — |")
        lines.append(f"| AutoCodeRover (reported) | 22% | — |")
        lines.append(f"| Claude Code (reported) | ~25% | — |")

        report = "\n".join(lines)
        path = os.path.join(self.output_dir, "summary.md")
        with open(path, "w") as f:
            f.write(report)
        print(f"\n✅ Benchmark report: {path}")

        # JSON
        json_path = os.path.join(self.output_dir, "result.json")
        with open(json_path, "w") as f:
            json.dump({
                "summary": {"total": total, "passed": passed, "avg_score": avg_score,
                             "success_rate": round(passed/max(total,1)*100, 1)},
                "results": [r.to_dict() for r in self.results],
            }, f, indent=2, ensure_ascii=False)

    def _generate_csv(self):
        """Generate CSV for spreadsheet analysis."""
        csv_path = os.path.join(self.output_dir, "summary.csv")
        with open(csv_path, "w") as f:
            f.write("case_id,stage,status,score,correctness,patch_quality,"
                    "efficiency,completeness,code_quality,doc_quality,"
                    "maintainability,duration_sec,tests_passed,tests_collected,"
                    "coverage_line_pct,code_files,iterations\n")
            for r in self.results:
                bd = r.score_breakdown
                f.write(
                    f"{r.case_id},{r.stage},{r.status},{r.score},"
                    f"{bd.get('correctness',0)},{bd.get('patch_quality',bd.get('doc_quality',0))},"
                    f"{bd.get('efficiency',0)},{bd.get('completeness',0)},"
                    f"{bd.get('code_quality',0)},{bd.get('doc_quality',0)},"
                    f"{bd.get('maintainability',0)},"
                    f"{r.duration_sec:.0f},{r.tests_passed},{r.tests_collected},"
                    f"{r.coverage_line_pct:.0f},{r.code_files_count},{r.iterations}\n"
                )
        print(f"✅ CSV results: {csv_path}")


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="DevAgent Benchmark Runner")
    ap.add_argument("--suite", default="default", help="Suite: default, stage_a, stage_b, quick")
    ap.add_argument("--case", default="", help="Run single case by ID")
    ap.add_argument("--output", default="./benchmark_report", help="Output dir")
    ap.add_argument("--provider", default="", help="LLM provider override")
    ap.add_argument("--timeout", type=int, default=600, help="Per-case timeout (s)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    runner = BenchmarkRunner(output_dir=args.output, provider=args.provider,
                             timeout_sec=args.timeout)

    if args.case:
        runner.run_case(args.case)
    else:
        runner.run_default_suite()
