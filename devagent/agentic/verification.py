"""Formal Verification — symbolic execution and contract checking for DevAgent V2.

Implements design doc 14: CodeImportanceClassifier, SymbolicExecutor (CrossHair),
ContractExtractor, LLMContractGenerator, and VerificationGate integration.

Four verification levels:
  L1: Syntax + Lint (always)
  L2: Unit Tests + Mutation (always)
  L3: Symbolic Execution (for high-importance code)
  L4: Formal Verification (for critical code)
"""

import os
import ast
import re
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class Contract:
    pre: str = "True"       # Python boolean expression
    post: str = "True"
    raises: str = ""        # Exception type name
    description: str = ""


@dataclass
class SymbolicReport:
    success: bool
    message: str = ""
    violations: list[dict] = field(default_factory=list)
    contracts_checked: int = 0
    duration_ms: float = 0.0


# ============================================================================
# Code Importance Classifier
# ============================================================================

class CodeImportanceClassifier:
    """Auto-classifies code into importance levels for verification depth."""

    HIGH_IMPORTANCE_PATTERNS = {
        "auth": ["authenticate", "login", "password", "credential", "token",
                 "session", "authorize", "permission", "role", "oauth",
                 "jwt", "hash_password", "verify_password"],
        "payment": ["payment", "charge", "billing", "invoice", "price",
                    "currency", "transaction", "refund", "wallet", "order_total"],
        "data_integrity": ["migrate", "migration", "schema", "backup",
                          "restore", "validate", "sanitize", "escape",
                          "serialize", "deserialize"],
        "security": ["encrypt", "decrypt", "hash", "salt", "crypto",
                    "signature", "verify", "csrf", "xss", "sql_injection",
                    "input_validation", "rate_limit"],
    }

    @classmethod
    def classify(cls, file_path: str, function_name: str = "",
                 code_content: str = "") -> str:
        """Returns: low | medium | high | critical"""
        content_lower = code_content.lower()
        name_lower = function_name.lower()
        path_lower = file_path.lower()

        score = 0
        for category, keywords in cls.HIGH_IMPORTANCE_PATTERNS.items():
            for kw in keywords:
                if kw in name_lower:
                    score += 3
                if kw in content_lower:
                    score += 1
                if kw in path_lower:
                    score += 2

        if score >= 8:
            return "critical"
        elif score >= 5:
            return "high"
        elif score >= 2:
            return "medium"
        return "low"

    @classmethod
    def should_verify(cls, file_path: str, function_name: str = "",
                      code_content: str = "") -> bool:
        level = cls.classify(file_path, function_name, code_content)
        return level in ("high", "critical")


# ============================================================================
# Contract Extractor
# ============================================================================

class ContractExtractor:
    """Extracts pre/post-condition contracts from code comments and type hints."""

    CONTRACT_PATTERNS = [
        (r'@requires:\s*(.+)', "requires"),
        (r'@ensures:\s*(.+)', "ensures"),
        (r'@raises:\s*(\w+)\s+when\s+(.+)', "raises"),
        (r'@pre:\s*(.+)', "requires"),
        (r'@post:\s*(.+)', "ensures"),
    ]

    def extract(self, function_source: str) -> list[Contract]:
        contracts = []
        for pattern, ct in self.CONTRACT_PATTERNS:
            for m in re.finditer(pattern, function_source, re.MULTILINE):
                if ct == "requires":
                    contracts.append(Contract(pre=m.group(1).strip(),
                                             description=f"Pre-condition: {m.group(1).strip()}"))
                elif ct == "ensures":
                    contracts.append(Contract(post=m.group(1).strip(),
                                             description=f"Post-condition: {m.group(1).strip()}"))
                elif ct == "raises":
                    exc_type = m.group(1)
                    condition = m.group(2).strip()
                    contracts.append(Contract(
                        pre=f"not ({condition})" if "not" not in condition else condition,
                        raises=exc_type,
                        description=f"Raises {exc_type} when {condition}"
                    ))
        return contracts

    @staticmethod
    def extract_function_body(source: str, func_name: str) -> Optional[str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.get_source_segment(source, node)
        return None


# ============================================================================
# LLM Contract Generator
# ============================================================================

class LLMContractGenerator:
    """Uses LLM to propose contracts for functions without explicit annotations."""

    PROMPT = """You are a formal verification expert. Given a function, propose contracts.

## Function
```python
{function_code}
```

## Instructions
Propose 2-4 pre/post-condition pairs as simple Python boolean expressions.
Focus on: null safety, bounds checking, type correctness, arithmetic safety.

Output JSON:
{{
  "contracts": [
    {{
      "pre": "b != 0",
      "post": "isinstance(result, float) or result is None",
      "description": "Division by non-zero returns float; zero divisor returns None"
    }}
  ]
}}"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def generate(self, function_code: str) -> list[Contract]:
        if not self.llm:
            return []

        prompt = self.PROMPT.format(function_code=function_code[:2000])
        try:
            result = self.llm.chat_structured(
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception:
            return []

        contracts = []
        for c in result.get("contracts", []):
            contracts.append(Contract(
                pre=c.get("pre", "True"),
                post=c.get("post", "True"),
                description=c.get("description", "")
            ))
        return contracts


# ============================================================================
# Symbolic Executor (CrossHair)
# ============================================================================

class SymbolicExecutor:
    """Symbolic execution via CrossHair for contract verification."""

    def __init__(self, llm_client=None):
        self._crosshair_available = self._check_crosshair()
        self.llm = llm_client
        self.contract_gen = LLMContractGenerator(llm_client)

    @staticmethod
    def _check_crosshair() -> bool:
        try:
            result = subprocess.run(["crosshair", "--version"],
                                   capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def verify_function(self, file_path: str, function_name: str,
                        contracts: list[Contract] = None,
                        source_code: str = "") -> SymbolicReport:
        """Verify a function against its contracts using symbolic execution."""
        if not self._crosshair_available:
            return SymbolicReport(
                success=False,
                message="CrossHair not installed. Install: pip install crosshair-tool"
            )

        source = source_code or Path(file_path).read_text(encoding="utf-8", errors="replace")

        # Auto-extract contracts if none provided
        if not contracts:
            contracts = ContractExtractor().extract(source)
        if not contracts:
            func_body = ContractExtractor.extract_function_body(source, function_name)
            if func_body and self.llm:
                contracts = self.contract_gen.generate(func_body)
        if not contracts:
            return SymbolicReport(
                success=True,
                message="No contracts to verify — add @requires/@ensures comments or enable LLM generation"
            )

        # Build verification script
        script = self._build_script(file_path, function_name, contracts)

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(script)
            script_path = f.name

        import time
        start = time.time()
        try:
            proc = subprocess.run(
                ["crosshair", "check", script_path, "--per_condition_timeout=10"],
                capture_output=True, text=True, timeout=60
            )
            duration = (time.time() - start) * 1000
            violations = self._parse_violations(proc.stdout + proc.stderr)
            return SymbolicReport(
                success=len(violations) == 0,
                message=f"Checked {len(contracts)} contract(s)",
                violations=violations,
                contracts_checked=len(contracts),
                duration_ms=duration
            )
        except subprocess.TimeoutExpired:
            return SymbolicReport(False, "Symbolic execution timed out (60s)")
        except Exception as e:
            return SymbolicReport(False, f"Symbolic execution error: {e}")
        finally:
            if os.path.exists(script_path):
                os.unlink(script_path)

    def _build_script(self, file_path: str, function_name: str,
                      contracts: list[Contract]) -> str:
        """Generate a CrossHair verification script."""
        module_name = Path(file_path).stem
        dir_name = str(Path(file_path).parent)

        contract_decorators = []
        for c in contracts:
            pre_check = f"lambda: {c.pre}" if c.pre != "True" else "lambda: True"
            post_check = f"lambda result: {c.post}" if c.post != "True" else "lambda result: True"
            contract_decorators.append(
                f"@crosshair.with_contracts(pre={pre_check}, post={post_check})"
            )

        decorators_block = "\n".join(contract_decorators)

        return f'''import sys
sys.path.insert(0, "{dir_name}")

import crosshair
from {module_name} import {function_name}

{decorators_block}
def _verify_{function_name}(*args, **kwargs):
    return {function_name}(*args, **kwargs)
'''

    @staticmethod
    def _parse_violations(output: str) -> list[dict]:
        violations = []
        for m in re.finditer(r'counterexample.*:(.+)', output, re.IGNORECASE):
            violations.append({"detail": m.group(1).strip()[:300]})
        return violations


# ============================================================================
# Verification Gate
# ============================================================================

class VerificationGate:
    """Integrates formal verification into the quality gate pipeline.

    This is L7 in the QualityGateSystem — non-blocking but logs all findings.
    """

    def __init__(self, workspace: str = ".", llm_client=None):
        self.workspace = workspace
        self.symbolic = SymbolicExecutor(llm_client)
        self.classifier = CodeImportanceClassifier()

    async def check(self, state: dict) -> tuple[bool, str]:
        """Check if recently modified high-importance code needs verification."""
        modified = state.get("modified_files", [])
        if not modified:
            return True, "No files modified — skipping verification"

        # Only verify high-importance files
        important = []
        for f in modified[-3:]:  # Last 3 modified files
            full_path = os.path.join(self.workspace, f)
            if not os.path.exists(full_path):
                continue

            content = Path(full_path).read_text(encoding="utf-8", errors="replace")

            # Check each function in the file
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    func_source = ast.get_source_segment(content, node)
                    if func_source and self.classifier.should_verify(
                        f, node.name, func_source
                    ):
                        important.append((f, node.name, func_source))

        if not important:
            return True, "No high-importance code modified — skipping verification"

        # Verify important functions
        results = []
        for file_path, func_name, func_source in important:
            report = self.symbolic.verify_function(
                os.path.join(self.workspace, file_path),
                func_name,
                source_code=func_source
            )
            results.append({
                "file": file_path,
                "function": func_name,
                "contracts_checked": report.contracts_checked,
                "violations": len(report.violations),
                "success": report.success
            })

        violations = sum(1 for r in results if not r["success"])
        if violations > 0:
            return True, f"Verification: {violations} potential issues found (non-blocking)"
        return True, f"Verification: {len(results)} function(s) checked, all passed"
