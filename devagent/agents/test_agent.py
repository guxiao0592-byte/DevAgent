"""Professional test agent — requirements-driven + coverage-guided + iterative fix.

Industry-standard testing methodology:
  1. Requirements-Driven: Injects FR/UC/acceptance-criteria into the prompt
  2. Equivalence Class Partitioning + Boundary Value Analysis
  3. Coverage-Guided Supplementation: generates extra tests for uncovered code
  4. Iterative Fix Loop: test failure → debug → repair → retest (max 3 rounds)
  5. Assertion Quality: rejects tests with trivial assertions (assert True, pass, …)
  6. Security Tests: injection, auth bypass, secret exposure for web projects

Output:
  - test_*.py files (validated syntax + assertion quality)
  - conftest.py with fixtures
  - pytest_result.json (collected/passed/failed + coverage metrics)
  - test_execution_report.md (professional test report)
"""

import ast
import json
import os
import re
from typing import Optional
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.test_runner import PytestRunner


# ============================================================================
# Prompt — Requirements-Driven Test Generation
# ============================================================================

TEST_PROMPT = """You are a senior QA engineer designing a comprehensive, traceable test suite.

===== INPUTS YOU RECEIVE =====
1. Functional Requirements (FR-XX) with acceptance criteria
2. Use Cases (UC-XX) with main/alternative flows
3. Source code API signatures (class/function definitions)
4. **Coverage gap report** — functions/lines that need tests (if available)

===== METHODOLOGY =====

### Equivalence Class Partitioning
For each parameter/input, identify:
  - Valid equivalence classes (normal values)
  - Invalid equivalence classes (too small, too large, wrong type, None)

### Boundary Value Analysis
For each boundary, test:
  - ON the boundary (e.g. 0, max int, empty string)
  - ONE ABOVE the boundary
  - ONE BELOW the boundary

### Coverage Targets
  - Line coverage: >= 85%
  - Branch coverage: >= 80%
  - Every public function: >= 1 happy-path + >= 1 error-path + >= 1 edge-case test

===== CRITICAL RULES =====
1. Every test_*.py file MUST have >= 1 `def test_*()` function
2. Every generated .py MUST be valid Python syntax
3. **Every test docstring MUST cite [FR-XX] or [UC-XX] or [NFR-XX]**
4. Tests MUST verify BEHAVIOR (public API), not implementation
5. **NO trivial assertions**: assert True, assert 1==1, plain `pass` are FORBIDDEN
6. **USE assert ==** not assert is (except for None/True/False)
7. **Every assert MUST have a message**: assert x == y, f"Expected {y}, got {x}"

===== OUTPUT FORMAT =====
{
  "test_strategy": {
    "approach": "describe approach",
    "equivalence_classes": [
      {"function": "func_name", "parameter": "param_name",
       "valid": ["val1","val2"], "invalid": ["None","''","negative"]}
    ],
    "boundary_values": [
      {"function": "func_name", "boundary": "zero",
       "on_boundary": 0, "above": 1, "below": -1}
    ],
    "coverage_goals": {"line": ">=85%", "branch": ">=80%"},
    "test_categories": ["unit","integration","edge_case","security"],
    "total_test_count": 20
  },
  "test_files": {
    "conftest.py": "import pytest\\n\\n# shared fixtures here\\n",
    "test_<module>.py": "import pytest\\n\\ndef test_xxx():  # [FR-XX]\\n    ...\\n"
  },
  "security_tests": {
    "injection_tests": ["test_input_sanitization", ...],
    "auth_tests": ["test_unauthorized_access_blocked", ...],
    "secret_exposure_tests": ["test_no_hardcoded_secrets", ...]
  }
}

Generate complete, runnable Python test files. Every test MUST have a real assertion with a message. No trivial tests."""


# ============================================================================
# TestAgent
# ============================================================================

class TestAgent(BaseAgent):
    """Industry-standard test agent — requirements-driven + coverage-guided."""

    MAX_FIX_ROUNDS = 3
    COVERAGE_LINE_TARGET = 85.0
    COVERAGE_BRANCH_TARGET = 80.0

    def __init__(self, llm_client, config=None):
        super().__init__(llm_client, config)
        self.test_runner = PytestRunner(
            timeout=(config or {}).get("pytest_timeout", 120)
        )

    def run(self, state: AgentState) -> AgentState:
        """Generate tests FROM REQUIREMENTS, execute, fix, measure coverage.

        Flow:
          1. Build context: requirements + design + code signatures
          2. LLM generates tests
          3. Validate syntax + assertion quality
          4. Execute pytest with coverage.py
          5. If failures → debug_issue → repair → retest (max 3 rounds)
          6. If coverage < threshold → generate supplementary tests
          7. Save results + report
        """
        code_files = state.code_files or []
        if not code_files:
            state.add_error("test", "No code files available for test generation")
            state.status = "TEST_DONE"
            return state

        # ==== Step 1: Build rich context ====
        context = self._build_context(state, code_files)

        # ==== Step 2: Generate initial tests ====
        result = self._generate_tests(context, coverage_gap=None)
        if not result:
            state.add_error("test", "LLM returned empty test result")
            state.status = "TEST_DONE"
            return state

        # ==== Step 3: Validate + save ====
        tests_dir = state.get_output_subdir("tests")
        tests_src_dir = os.path.join(tests_dir, "tests")
        os.makedirs(tests_src_dir, exist_ok=True)

        test_files = result.get("test_files", {})
        if not test_files:
            state.add_error("test", "LLM generated no test files")
            state.status = "TEST_DONE"
            return state

        saved_paths = self._save_test_files(test_files, tests_src_dir, state)
        state.test_files = saved_paths

        # Strategy
        strategy = result.get("test_strategy", {})
        self._save_json_artifact(state, "tests", "test_strategy.json", strategy)

        # ==== Step 4: Execute + fix loop ====
        impl_dir = state.get_output_subdir("implementation")
        test_results = self._execute_with_fix_loop(
            tests_src_dir, impl_dir, state, code_files, context
        )
        state.test_results = test_results

        # ==== Step 5: Coverage-guided supplement ====
        coverage = test_results.get("coverage") or {}
        line_rate = coverage.get("line_rate", 0)
        if line_rate < self.COVERAGE_LINE_TARGET and line_rate > 0:
            print(f"[TestAgent] Coverage {line_rate:.1f}% < {self.COVERAGE_LINE_TARGET}% "
                  f"target — generating supplementary tests", flush=True)
            supplement = self._generate_supplementary_tests(
                context, coverage, test_results
            )
            if supplement:
                extra_paths = self._save_test_files(
                    supplement.get("test_files", {}), tests_src_dir, state
                )
                state.test_files = list(set(saved_paths + extra_paths))
                test_results = self._execute_with_fix_loop(
                    tests_src_dir, impl_dir, state, code_files, context
                )
                state.test_results = test_results

        # ==== Step 6: Report ====
        self._save_json_artifact(state, "tests", "pytest_result.json", test_results)
        report = self._generate_report(test_results, strategy, state)
        if report:
            self._save_artifact(state, "tests", "test_execution_report.md", report)

        final_cov = (test_results.get("coverage") or {}).get("line_rate", 0)
        state.status = "TEST_DONE"
        state.add_trace("TestAgent", "completed", {
            "tests_collected": test_results.get("collected", 0),
            "passed": test_results.get("passed", 0),
            "failed": test_results.get("failed", 0),
            "coverage_line_pct": final_cov,
        })
        return state

    # ==================================================================
    # Context building
    # ==================================================================

    def _build_context(self, state: AgentState, code_files: list[str]) -> str:
        """Build rich test-generation context: requirements + design + code + acceptance criteria."""
        parts = []

        # 1. Requirements with acceptance criteria
        reqs = state.requirements or {}
        if reqs:
            parts.append("## 1. Functional Requirements\n")
            for fr in reqs.get("functional_requirements", []):
                ac = "; ".join(fr.get("acceptance_criteria", []))
                parts.append(
                    f"- **{fr.get('id','FR-??')}** {fr.get('name','')}\n"
                    f"  Description: {fr.get('description','')}\n"
                    f"  Priority: {fr.get('priority','medium')}\n"
                    f"  Acceptance Criteria: {ac or 'None specified'}"
                )

        # 2. Use cases
        use_cases = reqs.get("use_cases", [])
        if use_cases:
            parts.append("\n## 2. Use Cases\n")
            for uc in use_cases:
                flow = " → ".join(uc.get("main_flow", [])[:5])
                alt = "; ".join(
                    af.get("condition", "") for af in uc.get("alternative_flows", [])[:3]
                )
                parts.append(
                    f"- **{uc.get('id','UC-??')}** {uc.get('name','')}\n"
                    f"  Main flow: {flow}\n"
                    f"  Alternatives: {alt or 'none'}"
                )

        # 3. Security requirements
        sec_reqs = reqs.get("security_requirements", [])
        if sec_reqs:
            parts.append("\n## 3. Security Requirements\n")
            for s in sec_reqs:
                parts.append(f"- **{s.get('id','NFR-SEC-??')}** {s.get('description','')}")

        # 4. API signatures (compact — just class/func names, params, types)
        parts.append("\n## 4. Source Code API\n")
        for fpath in code_files:
            if not fpath.endswith(".py") or not os.path.exists(fpath):
                continue
            try:
                content = self.file_tool.read_text(fpath)
                sigs = self._extract_signatures(content, os.path.basename(fpath))
                if sigs:
                    parts.append(sigs)
            except Exception:
                pass

        return "\n".join(parts)

    # ==================================================================
    # Test generation
    # ==================================================================

    def _generate_tests(self, context: str, coverage_gap: Optional[dict] = None):
        """Call LLM to generate test files."""
        prompt = f"Generate a comprehensive pytest test suite.\n\n{context}"

        if coverage_gap:
            missing_files = coverage_gap.get("missing", [])
            if missing_files:
                prompt += "\n\n## ⚠️ COVERAGE GAPS — Generate tests for these files\n"
                for m in missing_files[:5]:
                    mf = m.get("file", "")
                    ml = m.get("missing_lines", [])
                    prompt += f"- `{mf}` — uncovered lines: {ml[:10]}\n"

        return self.llm.chat_structured(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=TEST_PROMPT
        )

    def _generate_supplementary_tests(self, context: str,
                                       coverage: dict,
                                       prev_results: dict) -> Optional[dict]:
        """Generate extra tests targeting uncovered code."""
        if not coverage or not coverage.get("missing"):
            return None

        # Build a targeted gap report
        gap_context = f"{context}\n\n## ⚠️ COVERAGE GAPS — Generate ONLY tests for these uncovered areas\n"
        for m in coverage["missing"][:5]:
            gap_context += (
                f"- `{m['file']}`: uncovered lines {m['missing_lines'][:10]}\n"
            )
        gap_context += "\nGenerate ONLY test functions for the uncovered lines above. Do NOT regenerate existing test files.\n"

        return self.llm.chat_structured(
            messages=[{"role": "user", "content": gap_context}],
            system_prompt=TEST_PROMPT
        )

    # ==================================================================
    # Save + validate
    # ==================================================================

    def _save_test_files(self, test_files: dict, tests_dir: str,
                          state: AgentState) -> list[str]:
        """Validate syntax + assertion quality, then save."""
        saved = []
        for rel_path, content in test_files.items():
            validated = self._validate_python_syntax(content, rel_path)
            if not validated:
                continue
            if rel_path.startswith("test_") and not self._has_meaningful_assertions(validated):
                print(f"[TestAgent] ⚠ Skipping {rel_path}: no meaningful assertions",
                      flush=True)
                continue
            sp = self._save_artifact(state, "tests", rel_path, validated)
            if sp:
                saved.append(sp)

        # Generate conftest.py with sys.path fix for cross-directory imports
        self._ensure_conftest(tests_dir, state)
        return saved

    def _ensure_conftest(self, tests_dir: str, state: AgentState):
        """Generate conftest.py that adds implementation source to sys.path.

        Without this, tests that do ``from src.xxx import yyy`` fail with
        ModuleNotFoundError when pytest runs from a different working directory.
        """
        impl_dir = state.get_output_subdir("implementation")
        conftest_path = os.path.join(tests_dir, "conftest.py")

        # Don't overwrite a user-provided conftest (LLM may have generated one)
        if os.path.exists(conftest_path):
            return

        conftest = (
            '"""Auto-generated by DevAgent — ensures tests can import project source."""\n'
            'import sys\n'
            'from pathlib import Path\n'
            '\n'
            '# Add implementation directory to Python path so tests can\n'
            '# import the generated source (e.g. from src.xxx import yyy)\n'
            '# Path layout: outputs/<task>/04_tests/tests/conftest.py → ../../03_implementation/\n'
            f'_IMPL_DIR = Path(__file__).resolve().parent.parent.parent / "{os.path.basename(impl_dir)}"\n'
            'if _IMPL_DIR.is_dir():\n'
            '    sys.path.insert(0, str(_IMPL_DIR))\n'
        )
        self.file_tool.write_text(conftest_path, conftest)

    @staticmethod
    def _has_meaningful_assertions(content: str) -> bool:
        """Reject test files with only trivial assertions."""
        # Count actual assert statements that are NOT trivial
        meaningful = 0
        for m in re.finditer(
            r'assert\s+(?!True\b|False\b|1\s*==\s*1|0\s*==\s*0|None\b\s*==\s*None)'
            r'(.+?)(?:#.*)?$',
            content, re.MULTILINE
        ):
            stmt = m.group(1).strip()
            # Skip trivial: pass, ...
            if stmt in ("pass", "..."):
                continue
            meaningful += 1

        # Must have >= 1 meaningful assertion AND >= 1 test function
        has_tests = bool(re.search(r'def test_\w+', content))
        return has_tests and meaningful >= 1

    # ==================================================================
    # Execute with iterative fix loop
    # ==================================================================

    def _execute_with_fix_loop(self, tests_src_dir: str, impl_dir: str,
                                state: AgentState, code_files: list[str],
                                context: str) -> dict:
        """Run tests → fix failures → retest (max MAX_FIX_ROUNDS rounds)."""
        results = self._execute_tests(tests_src_dir, impl_dir, state)

        for round_num in range(1, self.MAX_FIX_ROUNDS + 1):
            failed = results.get("failed", 0)
            errors = results.get("errors", 0)
            if failed == 0 and errors == 0:
                break  # All passing

            print(f"[TestAgent] Fix round {round_num}: {failed} failed, {errors} errors",
                  flush=True)

            # Try automatic repair
            fixed = self._auto_fix_failures(results, state, code_files, context)
            if not fixed:
                break  # Can't fix — stop trying

            # Re-run
            results = self._execute_tests(tests_src_dir, impl_dir, state)

        return results

    def _auto_fix_failures(self, results: dict, state: AgentState,
                            code_files: list[str], context: str) -> bool:
        """Attempt automatic fix of test failures.

        Strategy:
          1. If the test file has a syntax/import error → fix the test file
          2. If the source code has a bug revealed by the test → fix the source
          3. Use the LLM to suggest fixes based on the error output

        Returns: True if fixes were applied, False if unfixable
        """
        failed_cases = results.get("failed_cases", [])
        stderr = results.get("stderr", "")
        stdout = results.get("stdout", "")
        if not failed_cases and "ERRORS" not in stdout and "error" not in stderr.lower():
            return False

        # Build fix prompt
        fix_prompt = (
            "## The following tests failed. Suggest code fixes.\n\n"
            f"### Test failures:\n"
            f"{json.dumps(failed_cases[:10], indent=2, ensure_ascii=False)}\n\n"
            f"### Stderr:\n{stderr[:2000]}\n\n"
            f"### Context:\n{context[:2000]}\n\n"
            "Output JSON: {\"fixes\": [{\"file\": \"path.py\", "
            "\"old_code\": \"...\", \"new_code\": \"...\", \"reason\": \"...\"}]}"
        )

        try:
            fix_result = self.llm.chat_structured(
                messages=[{"role": "user", "content": fix_prompt}],
                system_prompt="You are a debugging expert. Suggest minimal, safe code fixes. Output valid JSON."
            )
        except Exception:
            return False

        fixes = fix_result.get("fixes", [])
        if not fixes:
            return False

        applied = 0
        for fix in fixes:
            fpath = fix.get("file", "")
            old = fix.get("old_code", "")
            new = fix.get("new_code", "")
            if not fpath or not old or not new or old == new:
                continue

            # Resolve file path
            full_path = fpath
            if not os.path.isabs(fpath):
                impl_dir = state.get_output_subdir("implementation")
                full_path = os.path.join(impl_dir, "src", os.path.basename(fpath))
                if not os.path.exists(full_path):
                    full_path = os.path.join(state.get_output_subdir("tests"), "tests",
                                            os.path.basename(fpath))

            if not os.path.exists(full_path):
                continue

            try:
                content = self.file_tool.read_text(full_path)
                if old in content:
                    new_content = content.replace(old, new, 1)
                    self.file_tool.write_text(full_path, new_content)
                    applied += 1
                    print(f"[TestAgent]   Fixed {os.path.basename(full_path)}: {fix.get('reason','')[:80]}",
                          flush=True)
            except Exception:
                continue

        return applied > 0

    # ==================================================================
    # Test execution
    # ==================================================================

    def _execute_tests(self, tests_src_dir: str, impl_dir: str,
                        state: AgentState) -> dict:
        """Execute tests with coverage — multi-strategy for import resolution."""
        # Strategy 1: Copy tests alongside src/ in impl dir + coverage
        if os.path.isdir(impl_dir):
            impl_tests = os.path.join(impl_dir, "tests")
            os.makedirs(impl_tests, exist_ok=True)

            import shutil
            for root, dirs, files in os.walk(tests_src_dir):
                for fn in files:
                    if fn.endswith(".py"):
                        shutil.copy2(
                            os.path.join(root, fn),
                            os.path.join(impl_tests, fn)
                        )

            results = self.test_runner.run_tests(
                impl_tests, work_dir=impl_dir,
                extra_env={"PYTHONPATH": impl_dir},
                with_coverage=True,
                source_dir=os.path.join(impl_dir, "src"),
            )
            if results.get("collected", 0) > 0:
                return results

        # Strategy 2: Run from test dir with PYTHONPATH pointing to impl
        results = self.test_runner.run_tests(
            tests_src_dir,
            work_dir=impl_dir if os.path.isdir(impl_dir) else None,
            extra_env={"PYTHONPATH": impl_dir} if os.path.isdir(impl_dir) else None,
        )
        if results.get("collected", 0) > 0:
            return results

        # Strategy 3: Walk tree for any test file (with PYTHONPATH)
        parent = os.path.dirname(tests_src_dir)
        for root, _dirs, files in os.walk(parent):
            for fn in files:
                if fn.startswith("test_") and fn.endswith(".py"):
                    results = self.test_runner.run_tests(
                        root,
                        work_dir=impl_dir if os.path.isdir(impl_dir) else None,
                        extra_env={"PYTHONPATH": impl_dir} if os.path.isdir(impl_dir) else None,
                    )
                    return results

        return results

    # ==================================================================
    # Report
    # ==================================================================

    def _generate_report(self, results: dict, strategy: dict,
                          state: AgentState) -> str:
        """Generate a professional IEEE 829-style test report."""
        collected = results.get("collected", 0)
        passed = results.get("passed", 0)
        failed = results.get("failed", 0)
        errors = results.get("errors", 0)
        success_rate = (passed / max(collected, 1)) * 100 if collected > 0 else 0
        coverage = results.get("coverage") or {}
        line_cov = coverage.get("line_rate", "N/A")
        branch_cov = coverage.get("branch_rate", "N/A")

        lines = [
            "# Test Execution Report",
            f"\n> Based on IEEE 829-2008 Standard for Software Test Documentation",
            f"\n## Summary\n",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Tests Collected | {collected} |",
            f"| Passed | {passed} |",
            f"| Failed | {failed} |",
            f"| Errors | {errors} |",
            f"| Success Rate | {success_rate:.1f}% |",
            f"| Line Coverage | {line_cov}% |",
            f"| Branch Coverage | {branch_cov}% |",
        ]

        # Coverage detail
        missing = coverage.get("missing", [])
        if missing:
            lines.append(f"\n## Coverage Gaps\n")
            for m in missing[:10]:
                lines.append(
                    f"- **{m['file']}**: uncovered lines {m['missing_lines'][:8]}"
                )

        # Quality gates
        lines.append(f"\n## Quality Gates\n")
        gates = [
            ("Pass Rate >= 80%", success_rate >= 80),
            ("Line Coverage >= 85%", isinstance(line_cov, (int, float)) and line_cov >= 85),
            ("Branch Coverage >= 80%", isinstance(branch_cov, (int, float)) and branch_cov >= 80),
            ("Zero Errors", errors == 0),
        ]
        all_pass = True
        for name, ok in gates:
            lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok:
                all_pass = False
        lines.append(f"\n**Overall: {'PASSED ✅' if all_pass else 'FAILED ❌ — review gaps above'}**\n")

        # Strategy
        if strategy:
            lines.append(f"## Test Strategy\n")
            lines.append(f"- Approach: {strategy.get('approach','N/A')}")
            eq = strategy.get("equivalence_classes", [])
            if eq:
                lines.append(f"- Equivalence Classes: {len(eq)} partitions")
            bv = strategy.get("boundary_values", [])
            if bv:
                lines.append(f"- Boundary Values: {len(bv)} boundaries tested")

        # Failed cases
        if results.get("failed_cases"):
            lines.append(f"\n## Failed Test Cases\n")
            for case in results["failed_cases"][:15]:
                lines.append(f"- **{case.get('name','unknown')}**: {case.get('message','')[:120]}")

        return "\n".join(lines)

    # ==================================================================
    # AST helpers
    # ==================================================================

    @staticmethod
    def _extract_signatures(source_code: str, file_path: str) -> str:
        """Extract class/function signatures from source code."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return ""

        lines = [f"\n### {file_path}"]
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = ', '.join(
                    b.id if isinstance(b, ast.Name) else
                    b.attr if isinstance(b, ast.Attribute) else '*'
                    for b in node.bases
                )
                lines.append(f"class {node.name}" + (f"({bases})" if bases else "") + ":")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in item.args.args if a.arg != 'self']
                        ret = TestAgent._format_type(item.returns) if item.returns else ''
                        prefix = 'async def ' if isinstance(item, ast.AsyncFunctionDef) else 'def '
                        lines.append(f"    {prefix}{item.name}({', '.join(args)})"
                                    + (f" -> {ret}" if ret else ""))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not any(
                    isinstance(p, ast.ClassDef) and hasattr(p, 'body') and node in p.body
                    for p in ast.walk(tree) if isinstance(p, ast.ClassDef)
                ):
                    args = [a.arg for a in node.args.args]
                    ret = TestAgent._format_type(node.returns) if node.returns else ''
                    lines.append(f"def {node.name}({', '.join(args)})"
                                + (f" -> {ret}" if ret else ""))
        return '\n'.join(lines)

    @staticmethod
    def _format_type(node: ast.AST) -> str:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute):
            return f'{TestAgent._format_type(node.value)}.{node.attr}'
        if isinstance(node, ast.Subscript):
            return f'{TestAgent._format_type(node.value)}[{TestAgent._format_type(node.slice)}]'
        if isinstance(node, ast.Constant): return str(node.value)
        return ''

    # ==================================================================
    # Syntax validator + fixer
    # ==================================================================

    @staticmethod
    def _validate_python_syntax(content: str, filename: str) -> str:
        """Validate Python syntax and fix common LLM artifacts."""
        content = content.lstrip('﻿')

        # Phase 1: Clean
        cleaned = []
        for line in content.split('\n'):
            stripped = line.strip()
            if re.match(r'^[=\-_\*]{3,}$', stripped) and not stripped.startswith('__'):
                continue
            if (stripped and not stripped.startswith('#') and not any(
                stripped.startswith(kw) for kw in (
                    'import ', 'from ', 'def ', 'class ', '@', '"""', "'''",
                    'raise ', 'return ', 'pass', 'assert ', 'yield ', 'with ',
                    'if ', 'for ', 'while ', 'try:', 'except', 'finally:',
                    'break', 'continue', 'self.', 'True', 'False', 'None',
                    '__all__', 'else:', 'elif ',
                )
            )):
                if any(c.isalpha() for c in stripped[:3]) and ' ' in stripped:
                    if ' = ' not in stripped and not stripped.rstrip().endswith(':'):
                        line = '# ' + line
            cleaned.append(line)
        content = '\n'.join(cleaned)

        # Phase 2: Validate
        try:
            tree = ast.parse(content)
            has_fn = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        for n in ast.walk(tree))
            has_cls = any(isinstance(n, ast.ClassDef) and n.name.startswith('Test')
                         for n in ast.walk(tree))
            if filename == 'conftest.py':
                return content if (has_fn or has_cls) else 'import pytest\n\n'
            return content if (has_fn or has_cls) else ''
        except SyntaxError:
            # Phase 3: Line-by-line fix
            fixed = []
            for line in content.split('\n'):
                s = line.strip()
                if not s or s.startswith('#'):
                    fixed.append(line)
                    continue
                try:
                    ast.parse(s)
                    fixed.append(line)
                except SyntaxError:
                    fixed.append('# ' + line)
            fixed_text = '\n'.join(fixed)
            try:
                tree2 = ast.parse(fixed_text)
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       for n in ast.walk(tree2)):
                    return fixed_text
            except SyntaxError:
                pass
            return ''
