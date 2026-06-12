"""Three-layer hybrid fault localization for DevAgent V2.

Architecture:
  Layer 1 — SBFL:      Spectrum-based fault localization using sys.settrace
                         to collect per-test execution traces and compute
                         Ochiai suspiciousness scores.

  Layer 2 — Static:     AST-based defect pattern detection:
                         null-check, boundary, exception, type, error propagation.

  Layer 3 — LLM Fusion: Combines SBFL rankings + static warnings + error info
                         to produce precise bug locations with confidence scores.

  Pipeline — End-to-end orchestration with async parallel execution of
              SBFL and static analysis, priority-based file selection,
              and fallback to exploratory localization when signals are weak.

References:
  - Ochiai formula: a_ef / sqrt((a_ef + a_nf) * (a_ef + a_ep))
  - AutoCodeRover (NUS): SBFL + LLM hybrid localization
  - SWE-Fixer: static + dynamic + LLM triad
"""

import os
import re
import ast
import json
import sys
import asyncio
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class Statement:
    """A single executable statement in a source file."""
    file: str
    line: int
    function: str
    suspiciousness: float = 0.0

    def __hash__(self):
        return hash((self.file, self.line))

    def __eq__(self, other):
        return isinstance(other, Statement) and self.file == other.file and self.line == other.line


@dataclass
class TraceResult:
    """Result of running a single test with execution tracing."""
    stmts: set[Statement] = field(default_factory=set)
    passed: bool = True
    error_info: Optional[dict] = None
    duration_ms: float = 0.0


@dataclass
class StaticIssue:
    """A defect found by static analysis."""
    file: str
    line: int
    function: str = ""
    message: str = ""
    category: str = ""       # null_check, boundary, exception, type, propagation
    confidence: float = 0.5  # 0.0 – 1.0


@dataclass
class FaultReport:
    """Complete fault localization report."""
    # SBFL
    sbfl_ranked: list[Statement] = field(default_factory=list)
    sbfl_duration_ms: float = 0.0

    # Static
    static_issues: list[StaticIssue] = field(default_factory=list)
    static_duration_ms: float = 0.0

    # LLM
    llm_result: dict = field(default_factory=dict)
    llm_duration_ms: float = 0.0

    # Combined
    bug_file: str = ""
    bug_function: str = ""
    bug_line: int = 0
    root_cause: str = ""
    fix_suggestion: str = ""
    confidence: str = "medium"  # high / medium / low
    total_duration_ms: float = 0.0

    @property
    def top_suspects(self) -> list[Statement]:
        return self.sbfl_ranked[:10]


# ============================================================================
# Layer 1: Coverage Tracer & SBFL
# ============================================================================

class TraceCollector:
    """Collects per-test execution traces by running pytest and parsing output.

    Uses a practical approach: runs each test individually, collects pass/fail
    status and traceback frame info. For passing tests, we estimate coverage
    by parsing the test's imports to find which source modules were exercised.
    """

    SKIP_DIRS = {"venv", ".venv", "site-packages", "__pycache__",
                 ".pytest_cache", ".git", "node_modules"}

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)

    def run_test(self, test_file: str, test_func: str,
                 timeout: int = 30) -> TraceResult:
        """Run a single test and collect execution traces from output."""
        start = __import__('time').time()

        try:
            # Use --tb=long to get full traceback frames including source file paths
            cmd = [sys.executable, "-m", "pytest",
                 f"{test_file}::{test_func}",
                 "-v", "--tb=long", "--no-header",
                 "-p", "no:cacheprovider"]
            proc = subprocess.run(
                cmd, cwd=self.project_root,
                capture_output=True, text=True,
                timeout=timeout
            )
            duration_ms = (__import__('time').time() - start) * 1000
            passed = proc.returncode == 0
            combined = proc.stdout + "\n" + proc.stderr

            # Extract executed statements from traceback frames
            stmts = self._extract_traceback_stmts(combined)

            # For passing tests that exercise source code, also add import-based estimates
            if passed:
                stmts |= self._estimate_imported_stmts(test_file)

            error_info = None
            if not passed:
                error_info = self._parse_error_from_output(combined)

            return TraceResult(stmts=stmts, passed=passed,
                              error_info=error_info, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            return TraceResult(passed=False,
                              error_info={"message": f"Test timed out after {timeout}s"},
                              duration_ms=timeout * 1000)

    def _extract_traceback_stmts(self, output: str) -> set[Statement]:
        """Extract file:line:function from pytest traceback output.

        Handles multiple formats:
          - File "/path/file.py", line 42, in func_name
          - /path/file.py:42: in func_name
          - /path/file.py:42: AssertionError
        """
        stmts = set()

        # Format 1: "File \"...\", line N, in func"
        for m in re.finditer(
            r'File "([^"]+)", line (\d+), in (\w+)',
            output, re.MULTILINE
        ):
            filename = m.group(1)
            line = int(m.group(2))
            func = m.group(3)
            resolved = self._resolve_path(filename) or filename
            if self._is_project_file(resolved):
                rel = os.path.relpath(os.path.realpath(resolved), self.project_root)
                stmts.add(Statement(file=rel, line=line, function=func))

        # Format 2: "/path/file.py:N: in func_name"
        for m in re.finditer(
            r'([/\w.-]+\.py):(\d+): in (\w+)',
            output, re.MULTILINE
        ):
            filename = m.group(1)
            line = int(m.group(2))
            func = m.group(3)
            resolved = self._resolve_path(filename)
            if resolved and self._is_project_file(resolved):
                rel = os.path.relpath(os.path.realpath(resolved), self.project_root)
                stmts.add(Statement(file=rel, line=line, function=func))

        # Format 3: Test file header: "/path/file.py::test_name"
        for m in re.finditer(
            r'([/\w.-]+\.py)::(\w+)',
            output, re.MULTILINE
        ):
            filename = m.group(1)
            resolved = self._resolve_path(filename)
            if resolved and os.path.exists(resolved) and self._is_project_file(resolved):
                rel = os.path.relpath(os.path.realpath(resolved), self.project_root)
                func = m.group(2)
                line = self._find_func_line(resolved, func)
                stmts.add(Statement(file=rel, line=line, function=func))

        return stmts

    def _resolve_path(self, filename: str) -> Optional[str]:
        """Resolve a filename that may be relative, absolute, or symlinked."""
        if filename.startswith("/"):
            return filename
        # Relative path — resolve against project_root
        resolved = os.path.join(self.project_root, filename)
        if os.path.exists(resolved):
            return resolved
        # Try realpath
        return resolved if os.path.exists(os.path.realpath(resolved)) else None

        return stmts

    @staticmethod
    def _find_func_line(file_path: str, func_name: str) -> int:
        """Find the line number of a function definition."""
        try:
            tree = ast.parse(Path(file_path).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node.lineno
        return 0

    def _estimate_imported_stmts(self, test_file: str) -> set[Statement]:
        """For a passing test, estimate which source statements were executed
        by parsing the test file's imports and adding key function entry points."""
        stmts = set()
        try:
            tree = ast.parse(Path(test_file).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return stmts

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    parts = node.module.split(".")
                    for name in (n.name for n in node.names):
                        # Try to resolve what was imported
                        func_path = os.path.join(
                            self.project_root, *parts, f"{name}.py"
                        )
                        if not os.path.exists(func_path):
                            func_path = os.path.join(
                                self.project_root, *parts, "__init__.py"
                            )
                        if os.path.exists(func_path) and self._is_project_file(func_path):
                            rel = os.path.relpath(
                                os.path.realpath(func_path), self.project_root
                            )
                            # Add function entry points from the imported module
                            stmts |= self._extract_func_entry_points(func_path, rel, name)
        return stmts

    def _extract_func_entry_points(self, file_path: str, rel: str,
                                    name: str) -> set[Statement]:
        """Extract function definition lines from an imported module."""
        stmts = set()
        try:
            tree = ast.parse(Path(file_path).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return stmts
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name == name or name == "*" or True:
                    stmts.add(Statement(file=rel, line=node.lineno, function=node.name))
        return stmts

    def _is_project_file(self, filename: str) -> bool:
        if not filename.endswith('.py'):
            return False
        real = os.path.realpath(filename)
        project_real = os.path.realpath(self.project_root)
        if not real.startswith(project_real + os.sep) and real != project_real:
            return False
        for d in self.SKIP_DIRS:
            if os.sep + d + os.sep in real:
                return False
        return True

    @staticmethod
    def _parse_error_from_output(output: str) -> dict:
        """Extract structured error info from pytest output."""
        info = {"message": "", "test_name": "", "expected": "", "actual": "",
                "error_file": "", "error_line": 0}

        for pat in [r'(test_\w+)\s.*?(?:FAILED|ERROR)',
                     r'(\w+\.py)::(test_\w+)',
                     r'FAILED\s+\S+::(test_\w+)']:
            m = re.search(pat, output)
            if m:
                groups = m.groups()
                info["test_name"] = groups[1] if len(groups) > 1 else groups[0]
                break

        m = re.search(r'(?:AssertionError|assert)\s*(.+)', output)
        if m:
            info["message"] = m.group(1).strip()[:300]

        for pat in [r'Expected\s*:\s*(.+)', r'Expected\s+(.+)']:
            m = re.search(pat, output)
            if m:
                info["expected"] = m.group(1).strip()[:200]
                break

        m = re.search(r'File "([^"]+)", line (\d+)', output, re.MULTILINE)
        if m:
            info["error_file"] = os.path.realpath(m.group(1))
            info["error_line"] = int(m.group(2))

        if not info["message"]:
            err = output.split("FAILURES")[-1] if "FAILURES" in output else output
            info["message"] = err.strip()[:500]

        return info


class SBFLocalizer:
    """Spectrum-Based Fault Localization using Ochiai formula.

    Runs all tests with execution tracing, then computes suspiciousness
    scores for each statement based on the test spectrum.

    Ochiai formula: suspiciousness = a_ef / sqrt((a_ef + a_nf) * (a_ef + a_ep))
      a_ef: # of FAILING tests that executed the statement
      a_ep: # of PASSING tests that executed the statement
      a_nf: # of FAILING tests that did NOT execute the statement
    """

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.tracer = TraceCollector(project_root)

    def localize(self, test_path: str = "tests/",
                 source_path: str = "src/") -> list[Statement]:
        """Run all tests with tracing and compute SBFL scores."""
        test_files = self._discover_test_files(test_path)
        if not test_files:
            return []

        test_funcs = []
        for tf in test_files:
            funcs = self._discover_test_funcs(tf)
            for func_name in funcs:
                test_funcs.append((tf, func_name))

        passed_traces: list[TraceResult] = []
        failed_traces: list[TraceResult] = []

        total = len(test_funcs)
        for i, (tf, func_name) in enumerate(test_funcs):
            trace = self.tracer.run_test(tf, func_name, timeout=(60 if total > 20 else 30))
            if trace.passed:
                passed_traces.append(trace)
            else:
                failed_traces.append(trace)

        # Compute Ochiai scores
        total_failed = len(failed_traces)
        total_passed = len(passed_traces)

        # Collect all unique statements
        all_stmts: dict[tuple, Statement] = {}
        for trace in failed_traces + passed_traces:
            for stmt in trace.stmts:
                key = (stmt.file, stmt.line)
                if key not in all_stmts:
                    all_stmts[key] = stmt

        # Compute spectrum scores for each statement
        for stmt in all_stmts.values():
            a_ef = sum(1 for t in failed_traces if stmt in t.stmts)
            a_ep = sum(1 for t in passed_traces if stmt in t.stmts)
            a_nf = total_failed - a_ef

            denom = ((a_ef + a_nf) * (a_ef + a_ep)) ** 0.5
            stmt.suspiciousness = a_ef / denom if denom > 0 else 0.0

        # Rank by suspiciousness (descending)
        ranked = sorted(all_stmts.values(),
                       key=lambda s: s.suspiciousness, reverse=True)
        return ranked

    def _discover_test_files(self, test_path: str) -> list[str]:
        p = Path(self.project_root) / test_path
        if not p.exists():
            return []
        if p.is_file():
            return [str(p)]
        files = []
        for f in sorted(p.rglob("test_*.py")):
            files.append(str(f))
        return files

    @staticmethod
    def _discover_test_funcs(test_file: str) -> list[str]:
        try:
            tree = ast.parse(Path(test_file).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return []
        funcs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                funcs.append(node.name)
        return funcs


# ============================================================================
# Enhanced SBFL using coverage.py — per-test coverage spectra
# ============================================================================

class CoverageBasedSBFL:
    """SBFL using coverage.py JSON for full per-statement execution spectra.

    Unlike the traceback-based SBFLocalizer, this uses coverage.py to track
    EVERY executed statement per test, giving much more accurate Ochiai scores.

    Architecture:
      1. Run tests individually with: coverage run --source <src> -m pytest <test>::<func>
      2. Parse coverage.json for each test to get executed lines
      3. Build the spectrum matrix: [test_id] → {file:line → executed}
      4. Compute Ochiai scores on the full spectrum
    """

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)

    def localize(self, test_path: str = "tests/",
                 source_path: str = "src/",
                 with_coverage: bool = True) -> tuple[list[Statement], dict]:
        """Run SBFL with coverage.py instrumentation.

        Returns:
            (ranked_statements, coverage_metadata)
        """
        test_files = self._discover_test_files(test_path)
        if not test_files:
            return [], {"error": "No test files found"}

        test_funcs = []
        for tf in test_files:
            for func_name in self._discover_test_funcs(tf):
                test_funcs.append((tf, func_name))

        # Run each test individually and collect coverage
        passed_coverage: list[set[tuple]] = []   # [(file, line), ...]
        failed_coverage: list[set[tuple]] = []   # [(file, line), ...]
        failed_errors: list[dict] = []

        src_abs = os.path.join(self.project_root, source_path)
        total = len(test_funcs)

        for tf, func_name in test_funcs:
            is_passing, covered_lines, error_info = self._run_test_with_coverage(
                tf, func_name, src_abs
            )
            if is_passing:
                passed_coverage.append(covered_lines)
            else:
                failed_coverage.append(covered_lines)
                if error_info:
                    failed_errors.append(error_info)

        total_failed = len(failed_coverage)
        total_passed = len(passed_coverage)

        if total_failed == 0:
            return [], {"message": "All tests pass — no bugs to localize",
                       "total_tests": total, "passed": total_passed}

        # Collect all unique statements
        all_stmts: dict[tuple, Statement] = {}
        for cov_set in failed_coverage + passed_coverage:
            for file_path, line in cov_set:
                key = (file_path, line)
                if key not in all_stmts:
                    func = self._infer_function(file_path, line)
                    all_stmts[key] = Statement(file=file_path, line=line,
                                               function=func)

        # Compute Ochiai scores
        for stmt in all_stmts.values():
            a_ef = sum(1 for cov in failed_coverage
                      if (stmt.file, stmt.line) in cov)
            a_ep = sum(1 for cov in passed_coverage
                      if (stmt.file, stmt.line) in cov)
            a_nf = total_failed - a_ef

            denom = ((a_ef + a_nf) * (a_ef + a_ep)) ** 0.5
            stmt.suspiciousness = a_ef / denom if denom > 0 else 0.0

        ranked = sorted(all_stmts.values(),
                       key=lambda s: s.suspiciousness, reverse=True)

        meta = {
            "total_tests": total,
            "total_failed": total_failed,
            "total_passed": total_passed,
            "total_statements": len(all_stmts),
            "coverage_ranked_count": len([s for s in ranked if s.suspiciousness > 0]),
            "failed_errors": failed_errors,
            "method": "coverage.py + Ochiai",
        }

        return ranked, meta

    def _run_test_with_coverage(self, test_file: str, test_func: str,
                                 source_dir: str) -> tuple[bool, set[tuple], dict]:
        """Run a single test with coverage.py and extract executed lines.

        Returns:
            (passed: bool, covered_lines: set[(file, line)], error_info: dict)
        """
        import json as _json
        import tempfile

        test_id = f"{test_file}::{test_func}"
        tmp_json = os.path.join(tempfile.gettempdir(),
                                f"sbfl_cov_{os.getpid()}_{hash(test_id) & 0x7FFFFFFF}.json")

        try:
            # Erase + run
            subprocess.run(
                [sys.executable, "-m", "coverage", "erase"],
                cwd=self.project_root, capture_output=True, timeout=10
            )
            proc = subprocess.run(
                [sys.executable, "-m", "coverage", "run",
                 "--source", source_dir, "--branch",
                 "-m", "pytest", test_file, "::" + test_func,
                 "-q", "--no-header", "--tb=short"],
                cwd=self.project_root, capture_output=True, text=True, timeout=60
            )
            passed = proc.returncode == 0

            # Get JSON report
            subprocess.run(
                [sys.executable, "-m", "coverage", "json", "-o", tmp_json, "--quiet"],
                cwd=self.project_root, capture_output=True, timeout=15
            )

            covered = self._parse_coverage_json(tmp_json)

            error_info = None
            if not passed:
                error_info = TraceCollector._parse_error_from_output(
                    proc.stdout + "\n" + proc.stderr
                )

            return passed, covered, error_info

        except Exception as e:
            return False, set(), {"message": str(e)}
        finally:
            try:
                os.unlink(tmp_json)
            except OSError:
                pass

    def _parse_coverage_json(self, path: str) -> set[tuple]:
        """Parse coverage.py JSON and return set of (relative_file, line)."""
        if not os.path.exists(path):
            return set()
        import json as _json
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception:
            return set()

        covered = set()
        for fp, finfo in data.get("files", {}).items():
            rel = fp
            project_real = os.path.realpath(self.project_root)
            fp_real = os.path.realpath(fp)
            if fp_real.startswith(project_real + os.sep):
                rel = os.path.relpath(fp_real, self.project_root)
            executed = finfo.get("executed_lines", [])
            for line in executed:
                covered.add((rel, line))
        return covered

    def _infer_function(self, file_path: str, line: int) -> str:
        """Try to infer which function contains the given line."""
        full = os.path.join(self.project_root, file_path)
        if not os.path.exists(full):
            return ""
        try:
            tree = ast.parse(Path(full).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return ""

        best_func = ""
        best_line = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if hasattr(node, 'end_lineno') and node.end_lineno:
                    end = node.end_lineno
                else:
                    end = max(getattr(n, 'lineno', 0) for n in ast.walk(node) if hasattr(n, 'lineno'))
                if node.lineno <= line <= end and node.lineno > best_line:
                    best_func = node.name
                    best_line = node.lineno
        return best_func

    @staticmethod
    def _check_coverage_available() -> bool:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "coverage", "--version"],
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _discover_test_files(test_path: str) -> list[str]:
        p = Path(test_path)
        if not p.is_absolute():
            p = Path.cwd() / test_path
        if not p.exists():
            return []
        if p.is_file():
            return [str(p)]
        return sorted(str(f) for f in p.rglob("test_*.py"))

    @staticmethod
    def _discover_test_funcs(test_file: str) -> list[str]:
        try:
            tree = ast.parse(Path(test_file).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return []
        return [node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test")]


# ============================================================================
# Layer 2: AST Static Analysis
# ============================================================================

class StaticAnalyzer:
    """AST-based defect pattern detection.

    Checks:
      C1: Null/None checks missing on nullable return values
      C2: Boundary conditions missing (index access, division)
      C3: Exception handling gaps
      C4: Type consistency issues
      C5: Error propagation breaks in call chains
    """

    def analyze_file(self, file_path: str) -> list[StaticIssue]:
        full_path = Path(self._resolve(file_path))
        if not full_path.exists():
            return []
        try:
            tree = ast.parse(full_path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return []

        issues = []
        issues.extend(self._check_null_missing(tree, str(full_path)))
        issues.extend(self._check_boundary_missing(tree, str(full_path)))
        issues.extend(self._check_exception_missing(tree, str(full_path)))
        issues.extend(self._check_type_issues(tree, str(full_path)))
        issues.extend(self._check_error_propagation(tree, str(full_path)))
        return issues

    def analyze_directory(self, dir_path: str) -> list[StaticIssue]:
        all_issues = []
        base = Path(dir_path)
        for py_file in base.rglob("*.py"):
            if any(s in py_file.parts for s in TraceCollector.SKIP_DIRS):
                continue
            all_issues.extend(self.analyze_file(str(py_file)))
        return all_issues

    def extract_call_graph(self, dir_path: str) -> dict[str, list[str]]:
        """Build call graph: {file:func_name: [callee_file:func_name]}"""
        graph: dict[str, list[str]] = {}
        base = Path(dir_path)
        for py_file in base.rglob("*.py"):
            if any(s in py_file.parts for s in TraceCollector.SKIP_DIRS):
                continue
            try:
                rel = str(py_file.relative_to(base))
                tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        caller_key = f"{rel}:{node.name}"
                        callees = []
                        for child in ast.walk(node):
                            if isinstance(child, ast.Call):
                                callee = self._get_call_name(child)
                                if callee:
                                    callees.append(callee)
                        graph[caller_key] = callees
            except (SyntaxError, Exception):
                continue
        return graph

    # ---------- C1: Null Check ----------

    def _check_null_missing(self, tree: ast.AST, file_path: str) -> list[StaticIssue]:
        issues = []
        # Find functions that can return None
        nullable_funcs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Return):
                        if child.value is None:
                            nullable_funcs.add(node.name)
                        elif (isinstance(child.value, ast.Name)
                              and child.value.id == "None"):
                            nullable_funcs.add(node.name)

        # Find call sites of nullable functions without None checks
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = self._get_call_name(node)
                if callee and callee in nullable_funcs:
                    if not self._has_null_check(node):
                        issues.append(StaticIssue(
                            file=file_path, line=node.lineno,
                            function=self._enclosing_function(node),
                            message=f"Return of '{callee}' (may be None) used without None check",
                            category="null_check", confidence=0.65
                        ))
        return issues

    # ---------- C2: Boundary Check ----------

    def _check_boundary_missing(self, tree: ast.AST, file_path: str) -> list[StaticIssue]:
        issues = []
        for node in ast.walk(tree):
            # Index/subscript without bounds guard
            if isinstance(node, ast.Subscript):
                if not self._has_surrounding_boundary_guard(node, tree):
                    issues.append(StaticIssue(
                        file=file_path, line=node.lineno,
                        function=self._enclosing_function(node),
                        message="Index/attribute access without boundary or None guard",
                        category="boundary", confidence=0.45
                    ))

            # Division without zero check
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(node.right, ast.Name):
                    if not self._has_zero_check(node.right.id, tree):
                        issues.append(StaticIssue(
                            file=file_path, line=node.lineno,
                            function=self._enclosing_function(node),
                            message=f"Division by variable '{node.right.id}' without zero check",
                            category="boundary", confidence=0.5
                        ))

            # List.pop() / list[index] on potentially empty list
            if isinstance(node, ast.Call):
                callee = self._get_call_name(node)
                if callee in ("pop", "remove") and node.args:
                    # Only flag if it's a method call like some_list.pop()
                    pass  # This is a heuristic, low priority

        return issues

    # ---------- C3: Exception Handling ----------

    def _check_exception_missing(self, tree: ast.AST, file_path: str) -> list[StaticIssue]:
        issues = []
        for node in ast.walk(tree):
            # Risky operations not wrapped in try/except
            if isinstance(node, ast.Call):
                callee = self._get_call_name(node)
                risky_funcs = {"open", "eval", "exec", "json.loads", "int", "float"}
                if callee in risky_funcs:
                    if not self._is_in_try_block(node, tree):
                        issues.append(StaticIssue(
                            file=file_path, line=node.lineno,
                            function=self._enclosing_function(node),
                            message=f"'{callee}()' called without try/except — may raise on bad input",
                            category="exception", confidence=0.35
                        ))
        return issues

    # ---------- C4: Type Issues ----------

    def _check_type_issues(self, tree: ast.AST, file_path: str) -> list[StaticIssue]:
        issues = []
        for node in ast.walk(tree):
            # Check for comparisons between incompatible types (heuristic)
            if isinstance(node, ast.Compare):
                left_type = self._infer_type(node.left, tree)
                for op, comparator in zip(node.ops, node.comparators):
                    right_type = self._infer_type(comparator, tree)
                    if left_type and right_type and left_type != right_type:
                        if {left_type, right_type} == {"int", "str"}:
                            issues.append(StaticIssue(
                                file=file_path, line=node.lineno,
                                function=self._enclosing_function(node),
                                message=f"Possible type mismatch: comparing {left_type} with {right_type}",
                                category="type", confidence=0.3
                            ))
        return issues

    # ---------- C5: Error Propagation ----------

    def _check_error_propagation(self, tree: ast.AST, file_path: str) -> list[StaticIssue]:
        issues = []
        for node in ast.walk(tree):
            # Empty except blocks (swallowing errors)
            if isinstance(node, ast.ExceptHandler):
                body = [n for n in node.body
                       if not isinstance(n, ast.Pass)
                       and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
                if len(body) == 0:
                    issues.append(StaticIssue(
                        file=file_path, line=node.lineno,
                        function=self._enclosing_function(node),
                        message="Empty or `pass`-only except clause — error is silently swallowed",
                        category="propagation", confidence=0.7
                    ))

            # Bare except: (catches too much)
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append(StaticIssue(
                    file=file_path, line=node.lineno,
                    function=self._enclosing_function(node),
                    message="Bare `except:` clause — catches all exceptions including SystemExit/KeyboardInterrupt",
                    category="propagation", confidence=0.55
                ))
        return issues

    # ---------- AST Helpers ----------

    @staticmethod
    def _resolve(file_path: str) -> str:
        return file_path if os.path.isabs(file_path) else os.path.abspath(file_path)

    @staticmethod
    def _get_call_name(node: ast.Call) -> Optional[str]:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    @staticmethod
    def _enclosing_function(node: ast.AST) -> str:
        """Walk up to find the enclosing function name (best effort)."""
        # This is a heuristic since AST nodes don't store parent references
        return ""

    @staticmethod
    def _has_null_check(node: ast.Call) -> bool:
        """Check if the call result is followed by an 'is None' or 'is not None' check.
        This is a best-effort heuristic since we can't easily trace control flow.
        """
        return False  # Requires parent chain — simplified

    @staticmethod
    def _has_zero_check(var_name: str, tree: ast.AST) -> bool:
        """Check if there's an 'if var_name != 0' or 'if var_name:' guard."""
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test_str = ast.unparse(node.test)
                if var_name in test_str:
                    return True
        return False

    @staticmethod
    def _has_surrounding_boundary_guard(node: ast.AST, tree: ast.AST) -> bool:
        """Check if there's a surrounding if/try that guards access."""
        return False  # Requires parent chain

    @staticmethod
    def _is_in_try_block(node: ast.AST, tree: ast.AST) -> bool:
        """Check if node is inside a try block."""
        for try_node in ast.walk(tree):
            if isinstance(try_node, ast.Try):
                for child in ast.walk(try_node):
                    if child is node:
                        return True
        return False

    @staticmethod
    def _infer_type(node: ast.AST, tree: ast.AST) -> Optional[str]:
        """Very basic type inference from AST."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int):
                return "int"
            if isinstance(node.value, str):
                return "str"
            if isinstance(node.value, float):
                return "float"
            if isinstance(node.value, bool):
                return "bool"
            if node.value is None:
                return "None"
        if isinstance(node, ast.Name):
            # Look for annotation in surrounding scope
            for assign in ast.walk(tree):
                if isinstance(assign, ast.AnnAssign) and isinstance(assign.target, ast.Name):
                    if assign.target.id == node.id:
                        ann = assign.annotation
                        if isinstance(ann, ast.Name):
                            return ann.id
        return None

    @staticmethod
    def extract_function_body(source: str, func_name: str) -> Optional[str]:
        """Extract a function's source code from a file."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.get_source_segment(source, node)
        return None


# ============================================================================
# Layer 3: LLM Fusion Reasoning
# ============================================================================

class LLMFaultLocalizer:
    """Fuse SBFL signals + static analysis warnings with LLM reasoning.

    Priority ranking:
      1. SBFL ∩ static (files appearing in both) — highest
      2. SBFL only
      3. Static only
    """

    LOCALIZE_PROMPT = """You are a senior debugging engineer performing root cause analysis.

## Test Failure Information
- Test: {test_name}
- Error: {error_message}
- Expected: {expected}
- Actual: {actual}
- Traceback file: {error_file}
- Traceback line: {error_line}

## SBFL Suspiciousness Ranking (Ochiai formula, ordered highest→lowest)
Only statements with score > 0 are shown:
{sbfl_ranked}

## Static Analysis Warnings
{static_warnings}

## Relevant Code Snippets
{code_snippets}

## Instructions
1. Identify the EXACT bug location with file, function, and line number.
2. Explain the root cause — what logical error, missing check, or incorrect assumption causes the failure.
3. Propose the MINIMAL code change to fix it (the exact old_string → new_string).
4. Rate your confidence: high (certain), medium (likely), low (speculative).

Output ONLY valid JSON:
{{
  "bug_location": {{
    "file": "relative/path.py",
    "function": "function_name",
    "line": 42,
    "suspected_code": "the buggy line or block"
  }},
  "root_cause": "detailed explanation of what went wrong",
  "fix": "precise description of the code change needed",
  "fix_code": {{
    "old_string": "exact code to replace",
    "new_string": "replacement code"
  }},
  "confidence": "high|medium|low",
  "reasoning": "why this is the right location"
}}"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def localize(self,
                 error_info: dict,
                 sbfl_results: list[Statement],
                 static_issues: list[StaticIssue],
                 code_files: dict[str, str] = None) -> dict:
        """Fuse all signals via LLM to produce a precise bug location."""
        if not self.llm:
            return self._heuristic_fallback(error_info, sbfl_results, static_issues)

        # Priority ranking: intersect then union
        sbfl_files = {s.file for s in sbfl_results[:20] if s.suspiciousness > 0}
        static_files = {i.file for i in static_issues if i.confidence > 0.3}

        overlap = list(sbfl_files & static_files)
        priority_files = (overlap[:5]
                        + [f for f in sbfl_files if f not in static_files][:3]
                        + [f for f in static_files if f not in sbfl_files][:2])

        # Build code snippets for priority files
        code_snippets = {}
        if code_files:
            for file_path in priority_files:
                content = code_files.get(file_path)
                if not content:
                    try:
                        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                # Only include suspect functions
                suspect_funcs = {s.function for s in sbfl_results[:10]
                                if s.file == file_path and s.suspiciousness > 0}
                if suspect_funcs:
                    for func in suspect_funcs:
                        body = StaticAnalyzer.extract_function_body(content, func)
                        if body:
                            key = f"{file_path}:{func}"
                            code_snippets[key] = body[:2000]

        # Format inputs
        sbfl_text = self._format_sbfl(sbfl_results[:15])
        static_text = self._format_static(static_issues[:10])

        prompt = self.LOCALIZE_PROMPT.format(
            test_name=error_info.get("test_name", "unknown"),
            error_message=error_info.get("message", "unknown error"),
            expected=error_info.get("expected", "?"),
            actual=error_info.get("actual", "?"),
            error_file=error_info.get("error_file", "unknown"),
            error_line=error_info.get("error_line", "?"),
            sbfl_ranked=sbfl_text or "(no SBFL data — no statements executed by failing tests)",
            static_warnings=static_text or "(no static analysis warnings)",
            code_snippets=json.dumps(code_snippets, indent=2, ensure_ascii=False)
        )

        try:
            result = self.llm.chat_structured(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a fault localization expert. Output only valid JSON."
            )
            return result
        except Exception:
            return self._heuristic_fallback(error_info, sbfl_results, static_issues)

    def _heuristic_fallback(self, error_info: dict,
                            sbfl_results: list[Statement],
                            static_issues: list[StaticIssue]) -> dict:
        """Fallback when LLM is unavailable: use SBFL + static intersection."""
        bug_file = ""
        bug_function = ""
        bug_line = 0

        if sbfl_results and sbfl_results[0].suspiciousness > 0:
            top = sbfl_results[0]
            bug_file = top.file
            bug_function = top.function
            bug_line = top.line

        # Boost: if a static issue matches the top SBFL file
        for issue in static_issues:
            if issue.file == bug_file and issue.confidence > 0.5:
                break

        return {
            "bug_location": {
                "file": bug_file,
                "function": bug_function,
                "line": bug_line,
                "suspected_code": ""
            },
            "root_cause": error_info.get("message", "Unknown error"),
            "fix": "",
            "fix_code": {"old_string": "", "new_string": ""},
            "confidence": "medium" if sbfl_results and sbfl_results[0].suspiciousness > 0.5 else "low",
            "reasoning": "Heuristic fallback: using top SBFL result"
        }

    @staticmethod
    def _format_sbfl(results: list[Statement]) -> str:
        if not results:
            return "(no SBFL data)"
        lines = []
        for s in results[:15]:
            if s.suspiciousness > 0:
                lines.append(
                    f"  {s.file}:{s.line} in {s.function}() — score={s.suspiciousness:.4f}"
                )
        return "\n".join(lines) if lines else "(all scores = 0 — no failing test coverage signal)"

    @staticmethod
    def _format_static(issues: list[StaticIssue]) -> str:
        if not issues:
            return "(no static analysis warnings)"
        lines = []
        for i in issues[:10]:
            lines.append(
                f"  [{i.category}] {i.file}:{i.line} — {i.message} (confidence={i.confidence:.2f})"
            )
        return "\n".join(lines)


# ============================================================================
# End-to-End Pipeline
# ============================================================================

class FaultLocalizationPipeline:
    """Orchestrates SBFL + Static + LLM into a single localization pipeline.

    Uses coverage.py-based SBFL when available (much more accurate),
    falling back to traceback-based SBFL otherwise.
    """

    def __init__(self, workspace: str, llm_client=None):
        self.workspace = os.path.abspath(workspace)
        self.sbfl = SBFLocalizer(self.workspace)
        self.coverage_sbfl = CoverageBasedSBFL(self.workspace)
        self.static = StaticAnalyzer()
        self.llm_loc = LLMFaultLocalizer(llm_client)
        self._use_coverage_sbfl = CoverageBasedSBFL._check_coverage_available()

    async def localize(self,
                       test_path: str = "tests/",
                       source_path: str = "src/",
                       error_info: dict = None) -> FaultReport:
        """Run the full fault localization pipeline.

        Phase 1: Coverage-based SBFL (if coverage.py available) + Static in parallel.
        Phase 2: Fallback to traceback-based SBFL if coverage SBFL produced no results.
        Phase 3: LLM fusion of SBFL + Static signals.
        """
        import time
        t_start = time.time()

        sbfl_meta = {}
        sbfl_results: list[Statement] = []

        # Phase 1: Try coverage-based SBFL first
        if self._use_coverage_sbfl:
            cov_results, cov_meta = await asyncio.to_thread(
                self.coverage_sbfl.localize, test_path, source_path, True
            )
            if cov_results and any(s.suspiciousness > 0 for s in cov_results):
                sbfl_results = cov_results
                sbfl_meta = cov_meta
                sbfl_meta["method"] = "coverage.py + Ochiai"

        # Phase 2: Fallback to traceback-based SBFL
        if not sbfl_results:
            sbfl_task = asyncio.create_task(
                asyncio.to_thread(self.sbfl.localize, test_path, source_path)
            )
            static_task_fb = asyncio.create_task(
                asyncio.to_thread(self.static.analyze_directory,
                                os.path.join(self.workspace, source_path))
            )
            sbfl_results = await sbfl_task
            static_issues = await static_task_fb
            sbfl_meta["method"] = "traceback + Ochiai"

            t_sbfl = (time.time() - t_start) * 1000

            # Collect error info
            if error_info is None:
                error_info = {"message": "Tests are failing", "test_name": ""}

            # Read code files
            code_files = {}
            sbfl_files = {s.file for s in sbfl_results[:10] if s.suspiciousness > 0}
            static_files_set = {i.file for i in static_issues[:10]}
            all_files = sbfl_files | static_files_set
            for f in all_files:
                full = os.path.join(self.workspace, f)
                if os.path.exists(full):
                    try:
                        code_files[f] = Path(full).read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

            # LLM fusion
            t_llm_start = time.time()
            llm_result = self.llm_loc.localize(error_info or {}, sbfl_results,
                                               static_issues, code_files)
            t_llm = (time.time() - t_llm_start) * 1000

            loc = llm_result.get("bug_location", {})
            total_duration = (time.time() - t_start) * 1000

            return FaultReport(
                sbfl_ranked=sbfl_results,
                sbfl_duration_ms=t_sbfl,
                static_issues=static_issues,
                static_duration_ms=0,
                llm_result=llm_result,
                llm_duration_ms=t_llm,
                bug_file=loc.get("file", ""),
                bug_function=loc.get("function", ""),
                bug_line=loc.get("line", 0),
                root_cause=llm_result.get("root_cause", ""),
                fix_suggestion=llm_result.get("fix", ""),
                confidence=llm_result.get("confidence", "medium"),
                total_duration_ms=total_duration
            )

        # Phase 3: Static analysis (still needed for complementary signals)
        static_issues = await asyncio.to_thread(
            self.static.analyze_directory,
            os.path.join(self.workspace, source_path)
        )

        t_sbfl = (time.time() - t_start) * 1000

        # Collect error info from coverage SBFL metadata if not provided
        if error_info is None:
            failed_errors = sbfl_meta.get("failed_errors", [])
            if failed_errors:
                error_info = failed_errors[0]
            else:
                error_info = {"message": "Tests are failing", "test_name": ""}

        # Read code files for LLM context
        code_files = {}
        sbfl_files = {s.file for s in sbfl_results[:10] if s.suspiciousness > 0}
        static_files_set = {i.file for i in static_issues[:10]}
        all_files = sbfl_files | static_files_set
        for f in all_files:
            full = os.path.join(self.workspace, f)
            if os.path.exists(full):
                try:
                    code_files[f] = Path(full).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        # Phase 4: LLM fusion
        t_llm_start = time.time()
        llm_result = self.llm_loc.localize(error_info or {}, sbfl_results,
                                           static_issues, code_files)
        t_llm = (time.time() - t_llm_start) * 1000

        # Build report
        loc = llm_result.get("bug_location", {})
        total_duration = (time.time() - t_start) * 1000

        return FaultReport(
            sbfl_ranked=sbfl_results,
            sbfl_duration_ms=t_sbfl,
            static_issues=static_issues,
            static_duration_ms=0,
            llm_result=llm_result,
            llm_duration_ms=t_llm,
            bug_file=loc.get("file", ""),
            bug_function=loc.get("function", ""),
            bug_line=loc.get("line", 0),
            root_cause=llm_result.get("root_cause", ""),
            fix_suggestion=llm_result.get("fix", ""),
            confidence=llm_result.get("confidence", "medium"),
            total_duration_ms=total_duration
        )
