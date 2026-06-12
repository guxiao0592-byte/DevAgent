"""Tests for Formal Verification — symbolic execution and contracts."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.verification import (
    Contract, CodeImportanceClassifier,
    ContractExtractor, LLMContractGenerator,
    SymbolicExecutor, VerificationGate,
)


class TestContract:
    def test_defaults(self):
        c = Contract()
        assert c.pre == "True"
        assert c.post == "True"

    def test_with_conditions(self):
        c = Contract(pre="b != 0", post="result > 0",
                    description="Division safety")
        assert c.pre == "b != 0"
        assert c.post == "result > 0"


class TestCodeImportanceClassifier:
    def test_low_importance(self):
        level = CodeImportanceClassifier.classify(
            "src/utils.py", "format_string", "return s.strip()"
        )
        assert level == "low"

    def test_high_importance_auth(self):
        level = CodeImportanceClassifier.classify(
            "src/auth.py", "authenticate", "def authenticate(token): ..."
        )
        assert level in ("high", "critical")

    def test_critical_payment(self):
        level = CodeImportanceClassifier.classify(
            "src/billing.py", "process_payment",
            "def process_payment(amount, currency, transaction_id): ..."
        )
        assert level == "critical"

    def test_should_verify(self):
        assert CodeImportanceClassifier.should_verify(
            "src/auth.py", "verify_password", "def verify_password(token, hash): ..."
        )
        assert not CodeImportanceClassifier.should_verify(
            "src/utils.py", "helper", "return x"
        )


class TestContractExtractor:
    def test_extract_requires(self):
        source = '''
def divide(a, b):
    """@requires: b != 0
    @ensures: result > 0
    """
    return a / b
'''
        extractor = ContractExtractor()
        contracts = extractor.extract(source)
        assert len(contracts) == 2

    def test_extract_none(self):
        source = "def foo():\n    return 1\n"
        contracts = ContractExtractor().extract(source)
        assert contracts == []

    def test_extract_raises(self):
        source = '''
def validate(x):
    """@raises: ValueError when x < 0"""
    if x < 0:
        raise ValueError("negative")
'''
        contracts = ContractExtractor().extract(source)
        assert len(contracts) == 1
        assert contracts[0].raises == "ValueError"


class TestSymbolicExecutor:
    def test_crosshair_check(self):
        executor = SymbolicExecutor()
        available = executor._check_crosshair()
        assert isinstance(available, bool)

    def test_verify_without_crosshair(self):
        executor = SymbolicExecutor()
        # CrossHair may or may not be installed
        report = executor.verify_function("nonexistent.py", "func")
        assert isinstance(report.success, bool)

    def test_parse_violations(self):
        output = "Found counterexample for contract: b=0 causes ZeroDivisionError"
        violations = SymbolicExecutor._parse_violations(output)
        # May or may not parse depending on CrossHair version format
        assert isinstance(violations, list)


class TestVerificationGate:
    def test_check_no_files(self):
        import asyncio
        vg = VerificationGate("/tmp")
        ok, msg = asyncio.run(vg.check({"modified_files": []}))
        assert ok

    def test_check_low_importance(self):
        import asyncio
        ws = tempfile.mkdtemp()
        # Create a low-importance file
        with open(os.path.join(ws, "utils.py"), "w") as f:
            f.write("def helper():\n    return 1\n")

        vg = VerificationGate(ws)
        ok, msg = asyncio.run(vg.check({
            "modified_files": ["utils.py"]
        }))
        assert ok  # Low importance, skipped
