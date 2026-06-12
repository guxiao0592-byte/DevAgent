"""Professional code repair agent with minimal-fix principle and verification.

Provides engineering-grade repair:
1. Minimal diff generation with clear annotations (only change what's needed)
2. Before/after comparison for each modified file
3. Regression test execution and reporting
4. Fix risk assessment
5. Alternative fix documentation
"""

import json
import os
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.patch_tool import PatchTool
from ..tools.test_runner import PytestRunner


REPAIR_PROMPT = """You are a senior software repair engineer applying minimal, safe fixes to production code.

=== FIX PRINCIPLES ===
1. **Minimal Change**: Change only the minimum necessary to fix the bug. Do NOT refactor, reformat, or add features.
2. **Preserve Contract**: The fix must not break existing behavior for valid inputs.
3. **Defensive**: Add input validation where the bug indicates missing checks.
4. **Consistent**: Match the surrounding code style and patterns.
5. **Documented**: Each change should be explainable in one sentence.

=== OUTPUT ===
For each file to fix, provide the COMPLETE corrected file content (not a diff — the full file as it should be after the fix).

Output ONLY valid JSON:
{
  "files_to_fix": {
    "relative/path.py": "complete fixed file content (full file, not just diff)"
  },
  "changes": [
    {
      "file": "relative/path.py",
      "line": 10,
      "change_type": "modified / added / deleted",
      "before": "original line(s)",
      "after": "fixed line(s)",
      "rationale": "why this change fixes the bug"
    }
  ],
  "fix_explanation": "concise explanation of what was wrong and how it was fixed",
  "fix_type": "boundary_fix / null_check / logic_fix / off_by_one / type_fix / exception_handling / config_fix / other",
  "risk_assessment": {
    "level": "low/medium/high",
    "reasoning": "why this risk level",
    "backout_strategy": "how to revert if needed"
  },
  "verification_steps": ["step 1: run specific test", "step 2: verify specific behavior"]
}"""


class RepairAgent(BaseAgent):
    """Agent for generating minimal, verified code patches."""

    def __init__(self, llm_client, config=None):
        super().__init__(llm_client, config)
        self.patch_tool = PatchTool()
        self.test_runner = PytestRunner(timeout=(config or {}).get("pytest_timeout", 120))

    def run(self, state: AgentState) -> AgentState:
        """Generate minimal patches and verify with regression tests."""
        debug_analysis = state.debug_analysis
        code_files = [f for f in state.code_files if os.path.exists(f)]

        if not debug_analysis or debug_analysis.get("needed") is False:
            state.add_warning("repair", "No debug analysis available, skipping repair")
            state.status = "REPAIR_DONE"
            return state

        # Read all source files for context
        code_context = ""
        for fpath in code_files:
            try:
                rel = os.path.relpath(fpath)
                code_context += f"\n# File: {rel}\n"
                code_context += self.file_tool.read_text(fpath)
                code_context += "\n"
            except Exception:
                pass

        truncated_code = self._truncate_text(code_context, max_chars=8000)

        repair_prompt = (
            f"Apply minimal fixes to resolve the following bugs.\n\n"
            f"=== DEBUG ANALYSIS ===\n"
            f"{json.dumps(debug_analysis, indent=2, ensure_ascii=False)}\n\n"
            f"=== SOURCE CODE ===\n{truncated_code}"
        )

        result = self.llm.chat_structured(
            messages=[{"role": "user", "content": repair_prompt}],
            system_prompt=REPAIR_PROMPT
        )

        files_to_fix = result.get("files_to_fix", {})
        changes = result.get("changes", [])
        fix_explanation = result.get("fix_explanation", "")
        fix_type = result.get("fix_type", "other")

        # Apply fixes and generate patches
        patches = []
        impl_dir = state.get_output_subdir("implementation")
        repair_dir = state.get_output_subdir("repair")
        repaired_dir = os.path.join(repair_dir, "repaired_code")
        os.makedirs(repaired_dir, exist_ok=True)

        modified_files = []
        before_after = []
        for rel_path, fixed_content in files_to_fix.items():
            orig_path = os.path.join(impl_dir, rel_path) if not os.path.isabs(rel_path) else rel_path
            repaired_path = os.path.join(repaired_dir, os.path.basename(rel_path))

            if os.path.exists(orig_path):
                original_text = self.file_tool.read_text(orig_path)
                patch = self.patch_tool.generate_patch_from_text(original_text, fixed_content, rel_path)
                patches.append(patch)
                modified_files.append(rel_path)
                before_after.append({
                    "file": rel_path,
                    "before": original_text[:500],
                    "after": fixed_content[:500]
                })
            else:
                patches.append(f"--- /dev/null\n+++ b/{rel_path}\n@@ -0,0 +1 @@\n+[new file]\n")
                modified_files.append(rel_path)
                before_after.append({
                    "file": rel_path,
                    "before": "(new file)",
                    "after": fixed_content[:500]
                })

            # Write repaired code
            self.file_tool.write_text(repaired_path, fixed_content)

            # Update original for regression test
            if os.path.exists(orig_path):
                self.file_tool.write_text(orig_path, fixed_content)
            # Register repaired file as artifact (prefer central registry)
            try:
                # Use relative filename within the repair phase
                artifact_name = os.path.join("repaired_code", os.path.basename(rel_path)) if not os.path.isabs(rel_path) else os.path.basename(rel_path)
                self._save_artifact(state, "repair", artifact_name, fixed_content)
            except Exception:
                # Non-fatal: continue if registry/save fails
                pass

        combined_patch = "\n".join(patches)

        # Run regression tests
        regression_results = self._run_regression_tests(state, impl_dir)

        # Save patch as .diff
        self._save_artifact(state, "repair", "patch.diff", combined_patch)

        # Save detailed results
        risk = result.get("risk_assessment", {})
        self._save_json_artifact(state, "repair", "repair_result.json", {
            "fix_type": fix_type,
            "fix_explanation": fix_explanation,
            "changes": changes,
            "modified_files": modified_files,
            "patch_size_bytes": len(combined_patch),
            "regression_results": regression_results,
            "risk_assessment": risk
        })

        # Generate professional repair report
        report = self._generate_professional_report(
            fix_explanation, modified_files, changes, combined_patch,
            regression_results, fix_type, risk, before_after
        )
        self._save_artifact(state, "repair", "repair_report.md", report)

        state.repair_patch = {
            "patch": combined_patch,
            "modified_files": modified_files,
            "fix_explanation": fix_explanation,
            "regression_results": regression_results,
            "fix_type": fix_type,
            "changes": changes
        }

        state.status = "REPAIR_DONE"
        state.add_trace("RepairAgent", "completed", {
            "files_modified": modified_files,
            "changes_count": len(changes),
            "patch_size": len(combined_patch),
            "regression_passed": regression_results.get("passed", 0),
            "regression_total": regression_results.get("collected", 0),
            "regression_ok": regression_results.get("success", False)
        })

        return state

    def _run_regression_tests(self, state: AgentState, work_dir: str) -> dict:
        """Run regression tests to verify the fix."""
        tests_dir = state.get_output_subdir("tests")
        tests_src_dir = os.path.join(tests_dir, "tests")
        if os.path.exists(tests_src_dir):
            results = self.test_runner.run_tests(tests_src_dir, work_dir=work_dir)
            return results
        return {"collected": 0, "passed": 0, "failed": 0, "success": True, "message": "No tests found"}

    @staticmethod
    def _generate_professional_report(
        explanation: str, files: list, changes: list, patch: str,
        regression: dict, fix_type: str, risk: dict, before_after: list
    ) -> str:
        """Generate a comprehensive repair report."""
        lines = []
        lines.append("# Software Repair Report\n")

        # Summary
        lines.append("## Summary\n")
        lines.append(f"- **Fix Type**: {fix_type}")
        lines.append(f"- **Files Modified**: {len(files)} — {', '.join(files)}")
        lines.append(f"- **Total Changes**: {len(changes)}")
        lines.append(f"- **Risk Level**: {risk.get('level', 'N/A')}")
        lines.append(f"\n**Fix Explanation**: {explanation}\n")

        # Change Log
        if changes:
            lines.append("## Detailed Change Log\n")
            for c in changes:
                lines.append(f"### {c.get('change_type', 'modified').upper()}: `{c.get('file', '')}` line {c.get('line', '?')}")
                lines.append(f"- **Rationale**: {c.get('rationale', 'N/A')}")
                before = c.get("before", "")
                after = c.get("after", "")
                if before:
                    lines.append(f"  - Before: `{before}`")
                if after:
                    lines.append(f"  - After: `{after}`")

        # Patch
        if patch.strip():
            lines.append("\n## Patch\n")
            lines.append("```diff")
            lines.append(patch[:3000])
            if len(patch) > 3000:
                lines.append("... [patch truncated]")
            lines.append("```\n")

        # Regression Results
        lines.append("## Regression Test Results\n")
        if regression.get("collected", 0) > 0:
            success_rate = (regression.get("passed", 0) / regression.get("collected", 1)) * 100
            passed = regression.get("passed", 0)
            failed = regression.get("failed", 0)
            lines.append(f"- **Tests Run**: {regression.get('collected', 0)}")
            lines.append(f"- **Passed**: {passed}")
            lines.append(f"- **Failed**: {failed}")
            lines.append(f"- **Success Rate**: {success_rate:.1f}%")
            lines.append(f"\n**Regression Verdict**: {'ALL PASSED' if regression.get('success') else f'{failed} FAILURE(S)'}\n")
        else:
            lines.append("No regression tests available.\n")

        # Risk Assessment
        lines.append("## Risk Assessment\n")
        lines.append(f"- **Level**: {risk.get('level', 'N/A')}")
        lines.append(f"- **Reasoning**: {risk.get('reasoning', 'N/A')}")
        if risk.get("backout_strategy"):
            lines.append(f"- **Backout Strategy**: {risk['backout_strategy']}")

        # Verification Steps
        verification = risk if isinstance(risk, dict) else {}
        if verification.get("backout_strategy"):
            pass  # already printed

        return "\n".join(lines)
