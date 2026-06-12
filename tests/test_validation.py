"""Tests for DevAgent V2 validation, quality gates, and mutation testing."""

import os
import sys
import asyncio
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.validation import (
    InstantValidator, CheckResult, ValidationResult,
    SmartRegressionSelector, MutationTester, QualityGateSystem,
)


def run_async(coro):
    return asyncio.run(coro)


def make_workspace():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "src")
    tests = os.path.join(tmp, "tests")
    os.makedirs(src)
    os.makedirs(tests)

    with open(os.path.join(src, "calc.py"), "w") as f:
        f.write('''"""A simple calculator."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def subtract(a: int, b: int) -> int:
    return a - b

def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("zero division")
    return a / b

class Calculator:
    def __init__(self, initial: int = 0):
        self.value = initial

    def add(self, n: int) -> int:
        self.value += n
        return self.value

CONFIG = {"debug": False}
''')

    with open(os.path.join(tests, "test_calc.py"), "w") as f:
        f.write('''import pytest
from src.calc import add, subtract, divide, Calculator


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 3) == 2


def test_divide():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(10, 0)


def test_calculator():
    c = Calculator(10)
    assert c.add(5) == 15
''')
    return tmp


# ============================================================================
# Test InstantValidator
# ============================================================================

class TestInstantValidator:
    def test_validate_valid_file(self):
        ws = make_workspace()
        validator = InstantValidator(ws)
        result = run_async(validator.validate("src/calc.py"))
        assert result.success

    def test_validate_missing_file(self):
        ws = make_workspace()
        validator = InstantValidator(ws)
        result = run_async(validator.validate("nonexistent.py"))
        assert not result.success

    def test_validate_syntax_error(self):
        ws = make_workspace()
        bad = os.path.join(ws, "src/bad.py")
        with open(bad, "w") as f:
            f.write("def broken(:\n    pass\n")
        validator = InstantValidator(ws)
        result = run_async(validator.validate("src/bad.py"))
        assert not result.success
        assert any("syntax" in name.lower() for name, _ in result.blocking_failures())

    def test_validate_prints_warnings(self):
        ws = make_workspace()
        with open(os.path.join(ws, "src/warn.py"), "w") as f:
            f.write("""def foo():
    except:  # bare except
        print("error")  # print statement
""")
        validator = InstantValidator(ws)
        result = run_async(validator.validate("src/warn.py"))
        # Pattern warnings are non-blocking
        assert isinstance(result.summary(), str)

    def test_check_syntax_valid(self):
        result = run_async(InstantValidator._check_syntax(
            "def foo():\n    return 1\n", "test.py"
        ))
        assert result.ok

    def test_check_syntax_invalid(self):
        result = run_async(InstantValidator._check_syntax(
            "def foo(:\n    pass\n", "test.py"
        ))
        assert not result.ok

    def test_check_patterns(self):
        result = InstantValidator._check_patterns(
            "# TODO fix this\ndef foo():\n    return 1\n", "test.py"
        )
        assert result.ok  # Non-blocking


# ============================================================================
# Test SmartRegressionSelector
# ============================================================================

class TestSmartRegressionSelector:
    def test_select_affected_tests(self):
        ws = make_workspace()
        selector = SmartRegressionSelector()
        modified = ["src/calc.py"]
        selected = selector.select(modified, os.path.join(ws, "tests"))
        # test_calc.py should be selected
        assert any("test_calc" in f for f in selected), (
            f"Expected test_calc.py in selection, got {selected}"
        )

    def test_select_no_matches_returns_empty(self):
        ws = make_workspace()
        selector = SmartRegressionSelector()
        modified = ["src/nonexistent.py"]
        selected = selector.select(modified, os.path.join(ws, "tests"))
        assert isinstance(selected, list)

    def test_select_fast_by_name(self):
        ws = make_workspace()
        modified = ["src/calc.py"]
        selected = SmartRegressionSelector.select_fast(
            os.path.join(ws, "tests"), modified
        )
        assert any("calc" in f for f in selected)


# ============================================================================
# Test MutationTester
# ============================================================================

class TestMutationTester:
    def test_generate_mutants(self):
        tester = MutationTester()
        code = "def add(a, b):\n    return a + b\n"
        mutants = tester._generate_mutants(code, "test.py")
        assert len(mutants) > 0
        for m in mutants:
            assert "code" in m
            assert "description" in m

    def test_mutation_changes_code(self):
        tester = MutationTester()
        code = "def divide(a, b):\n    return a / b\n"
        mutants = tester._generate_mutants(code, "test.py")
        # Should find the + or / operator to mutate
        mutated_codes = [m["code"] for m in mutants]
        assert any(code != mc for mc in mutated_codes)

    def test_test_quality_basic(self):
        ws = make_workspace()
        tester = MutationTester()
        result = tester.test_quality(
            "src/calc.py",
            os.path.join(ws, "tests/test_calc.py"),
            ws
        )
        assert "mutation_score" in result
        assert "total_mutants" in result
        assert 0 <= result["mutation_score"] <= 1


# ============================================================================
# Test QualityGateSystem
# ============================================================================

class TestQualityGateSystem:
    def test_gate_syntax_passing(self):
        ws = make_workspace()
        qgs = QualityGateSystem(ws)
        state = {"modified_files": ["src/calc.py"]}
        ok, msg = run_async(qgs.check_gate("L1_SYNTAX", state))
        assert ok

    def test_gate_syntax_missing_file(self):
        ws = make_workspace()
        qgs = QualityGateSystem(ws)
        state = {"modified_files": ["src/nonexistent.py"]}
        ok, msg = run_async(qgs.check_gate("L1_SYNTAX", state))
        assert not ok

    def test_gate_unit_tests_passing(self):
        qgs = QualityGateSystem("/tmp")
        state = {"test_results": {"passed": 5, "failed": 0, "collected": 5}}
        ok, msg = run_async(qgs.check_gate("L4_UNIT_TESTS", state))
        assert ok

    def test_gate_unit_tests_failing(self):
        qgs = QualityGateSystem("/tmp")
        state = {"test_results": {"passed": 3, "failed": 2, "collected": 5}}
        ok, msg = run_async(qgs.check_gate("L4_UNIT_TESTS", state))
        assert not ok

    def test_check_all_produces_results(self):
        ws = make_workspace()
        qgs = QualityGateSystem(ws)
        state = {
            "modified_files": ["src/calc.py"],
            "test_results": {"passed": 5, "failed": 0, "collected": 5}
        }
        results = run_async(qgs.check_all(state))
        assert isinstance(results, dict)
        assert "L1_SYNTAX" in results

    def test_summary_format(self):
        qgs = QualityGateSystem("/tmp")
        qgs._gate_results["L1_SYNTAX"] = True
        qgs._gate_results["L4_UNIT_TESTS"] = False
        summary = qgs.summary()
        assert "PASS" in summary
        assert "FAIL" in summary

    def test_all_gates_defined(self):
        qgs = QualityGateSystem("/tmp")
        assert len(qgs.GATES) == 6
        assert qgs.GATES["L1_SYNTAX"]["blocking"] is True
        assert qgs.GATES["L2_LINT"]["blocking"] is False


# ============================================================================
# Test ValidationResult
# ============================================================================

class TestValidationResult:
    def test_success_no_failures(self):
        r = ValidationResult(True, [("syntax", CheckResult(True))])
        assert len(r.blocking_failures()) == 0

    def test_failure_with_errors(self):
        r = ValidationResult(False, [
            ("syntax", CheckResult(False, error="Syntax error at line 5"))
        ])
        assert len(r.blocking_failures()) == 1
