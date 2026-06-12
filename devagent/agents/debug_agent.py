"""Professional debug analysis agent with deep root cause investigation.

Provides engineering-grade debugging:
1. Failed test analysis with assertion details
2. Call stack reconstruction and variable state analysis
3. Root cause categorization (null pointer, off-by-one, logic error, race condition, etc.)
4. Impact scope assessment
5. Multiple fix hypotheses with pros/cons
6. Reproduction steps for manual verification
"""

import json
import os
from .base_agent import BaseAgent
from ..agent_core.state import AgentState


DEBUG_PROMPT = """You are a senior debugging engineer conducting a root cause analysis on a test failure.

=== ANALYSIS PROCESS ===

STEP 1 - Symptom Analysis:
- What assertion failed? What was the actual vs expected value?
- What exception was thrown? At which line?
- What is the full error message and traceback?

STEP 2 - Code Flow Reconstruction:
- Trace the execution path from the test call to the failure point
- Identify the function call chain
- Evaluate what variable states would be at each step

STEP 3 - Root Cause Identification:
- Classify the bug type: null_pointer, off_by_one, logic_error, type_error, boundary_condition, concurrency, resource_leak, configuration, api_misuse, incorrect_algorithm
- Identify the exact file, function, and line range
- Show the suspected code snippet

STEP 4 - Impact Assessment:
- What functionality is affected?
- What other code paths share this bug?
- What is the severity?

STEP 5 - Fix Hypothesis:
- Propose the primary fix approach
- Propose alternative approaches if applicable
- For each: describe trade-offs (safety, performance, invasiveness)

Output ONLY valid JSON:
{
  "bug_classification": {
    "type": "logic_error / null_pointer / off_by_one / type_error / boundary_condition / concurrency / resource_leak / configuration / api_misuse / incorrect_algorithm / other",
    "confidence": "high/medium/low",
    "pattern": "description of the bug pattern (e.g., 'off-by-one in loop boundary')"
  },
  "symptom": {
    "test_name": "name of failing test",
    "assertion": "what assertion failed",
    "expected": "expected value",
    "actual": "actual value",
    "exception_type": "if applicable",
    "error_message": "full error message"
  },
  "execution_flow": {
    "call_chain": ["test_something()", "-> service.method()", "-> model.compute()"],
    "suspected_transition": "the step where things go wrong"
  },
  "bug_location": {
    "file": "relative/path.py",
    "function": "function_name",
    "line_range": [10, 15],
    "suspected_code": "the problematic code snippet",
    "variable_state_at_failure": {
      "var_name": "value at time of failure"
    }
  },
  "root_cause": "detailed explanation of why the bug occurs, referencing specific code",
  "impact_scope": {
    "affected_functionality": "what breaks",
    "affected_code_paths": ["other callers of this function"],
    "cascading_effects": "what else could break due to this bug",
    "severity": "critical/high/medium/low"
  },
  "reproduction_steps": [
    "step 1: call function with specific input",
    "step 2: observe result"
  ],
  "fix_hypotheses": [
    {
      "approach": "primary / alternative",
      "description": "how to fix it",
      "code_change": "specific code change",
      "pros": ["pro 1"],
      "cons": ["con 1"],
      "risk": "low/medium/high"
    }
  ],
  "prevention": "how to prevent similar bugs in the future"
}

Be precise. Use actual variable names and values from the code. Do NOT guess — if you cannot determine a value, state that it is unknown."""


class DebugAgent(BaseAgent):
    """Agent for professional debugging and root cause analysis."""

    def run(self, state: AgentState) -> AgentState:
        """Analyze failures and locate bugs with deep investigation."""
        test_results = state.test_results
        code_files = [f for f in state.code_files if os.path.exists(f)]

        if not test_results or test_results.get("success", True):
            state.debug_analysis = {
                "needed": False,
                "message": "All tests pass — no debugging required"
            }
            state.status = "REPAIR_DONE"
            return state

        failed_cases = test_results.get("failed_cases", [])
        stderr = test_results.get("stderr", "")
        stdout = test_results.get("stdout", "")

        # Read source files for context
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
        truncated_stderr = self._truncate_text(stderr, max_chars=3000)
        truncated_stdout = self._truncate_text(stdout, max_chars=3000)

        analysis_prompt = (
            f"Perform a thorough root cause analysis on the following test failures.\n\n"
            f"=== FAILED TEST CASES ===\n"
            f"{json.dumps(failed_cases, indent=2, ensure_ascii=False)}\n\n"
            f"=== STDERR ===\n{truncated_stderr}\n\n"
            f"=== STDOUT ===\n{truncated_stdout}\n\n"
            f"=== SOURCE CODE ===\n{truncated_code}"
        )

        result = self.llm.chat_structured(
            messages=[{"role": "user", "content": analysis_prompt}],
            system_prompt=DEBUG_PROMPT
        )

        state.debug_analysis = result

        # Generate professional debug report
        md_content = self._generate_debug_report(result)
        self._save_artifact(state, "repair", "debug_analysis_report.md", md_content)
        self._save_json_artifact(state, "repair", "debug_analysis.json", result)

        state.status = "REPAIR_DONE"
        bug_loc = result.get("bug_location", {})
        state.add_trace("DebugAgent", "completed", {
            "bug_file": bug_loc.get("file", "unknown"),
            "bug_function": bug_loc.get("function", "unknown"),
            "bug_type": result.get("bug_classification", {}).get("type", "unknown"),
            "severity": result.get("impact_scope", {}).get("severity", "unknown"),
            "fix_hypotheses": len(result.get("fix_hypotheses", []))
        })

        return state

    @staticmethod
    def _generate_debug_report(analysis: dict) -> str:
        """Generate professional markdown debug report."""
        lines = []
        lines.append("# Debug Analysis Report\n")

        # Symptom
        sym = analysis.get("symptom", {})
        lines.append("## 1. Symptom\n")
        lines.append(f"- **Test**: {sym.get('test_name', 'unknown')}")
        lines.append(f"- **Assertion**: `{sym.get('assertion', 'N/A')}`")
        lines.append(f"- **Expected**: `{sym.get('expected', 'N/A')}`")
        lines.append(f"- **Actual**: `{sym.get('actual', 'N/A')}`")
        if sym.get("exception_type"):
            lines.append(f"- **Exception**: `{sym['exception_type']}`")
        if sym.get("error_message"):
            lines.append(f"- **Error**:\n  ```\n  {sym['error_message']}\n  ```")

        # Bug Classification
        cls = analysis.get("bug_classification", {})
        lines.append("\n## 2. Bug Classification\n")
        lines.append(f"- **Type**: `{cls.get('type', 'unknown')}`")
        lines.append(f"- **Pattern**: {cls.get('pattern', 'N/A')}")
        lines.append(f"- **Confidence**: {cls.get('confidence', 'N/A')}")

        # Execution Flow
        flow = analysis.get("execution_flow", {})
        lines.append("\n## 3. Execution Flow\n")
        call_chain = flow.get("call_chain", [])
        if call_chain:
            for i, step in enumerate(call_chain):
                lines.append(f"  {i+1}. `{step}`")
        if flow.get("suspected_transition"):
            lines.append(f"\n- **Suspected Failure Point**: {flow['suspected_transition']}")

        # Bug Location
        loc = analysis.get("bug_location", {})
        lines.append("\n## 4. Bug Location\n")
        lines.append(f"- **File**: `{loc.get('file', 'unknown')}`")
        lines.append(f"- **Function**: `{loc.get('function', 'unknown')}`")
        lines.append(f"- **Lines**: {loc.get('line_range', 'N/A')}")
        suspected = loc.get("suspected_code", "")
        if suspected:
            lines.append(f"\n  **Suspected Code**:\n  ```python\n  {suspected}\n  ```")
        var_state = loc.get("variable_state_at_failure", {})
        if var_state:
            lines.append("\n  **Variable State at Failure**:")
            for k, v in var_state.items():
                lines.append(f"  - `{k}` = `{v}`")

        # Root Cause
        lines.append(f"\n## 5. Root Cause Analysis\n{analysis.get('root_cause', 'N/A')}\n")

        # Impact
        impact = analysis.get("impact_scope", {})
        lines.append("## 6. Impact Assessment\n")
        lines.append(f"- **Severity**: {impact.get('severity', 'unknown')}")
        lines.append(f"- **Affected Functionality**: {impact.get('affected_functionality', 'N/A')}")
        affected_paths = impact.get("affected_code_paths", [])
        if affected_paths:
            lines.append("- **Affected Code Paths**:")
            for p in affected_paths:
                lines.append(f"  - `{p}`")
        if impact.get("cascading_effects"):
            lines.append(f"- **Cascading Effects**: {impact['cascading_effects']}")

        # Reproduction
        repro = analysis.get("reproduction_steps", [])
        if repro:
            lines.append("\n## 7. Reproduction Steps\n")
            for i, step in enumerate(repro, 1):
                lines.append(f"  {i}. {step}")

        # Fix Hypotheses
        hypotheses = analysis.get("fix_hypotheses", [])
        if hypotheses:
            lines.append("\n## 8. Fix Hypotheses\n")
            for h in hypotheses:
                approach_label = "**Primary Fix**" if h.get("approach") == "primary" else "**Alternative**"
                lines.append(f"\n### {approach_label}\n")
                lines.append(f"- **Description**: {h.get('description', 'N/A')}")
                if h.get("code_change"):
                    lines.append(f"- **Code Change**: `{h['code_change']}`")
                lines.append(f"- **Risk**: {h.get('risk', 'N/A')}")
                pros = h.get("pros", [])
                if pros:
                    lines.append("  - Pros:")
                    for p in pros:
                        lines.append(f"    - {p}")
                cons = h.get("cons", [])
                if cons:
                    lines.append("  - Cons:")
                    for c in cons:
                        lines.append(f"    - {c}")

        # Prevention
        if analysis.get("prevention"):
            lines.append(f"\n## 9. Prevention\n{analysis['prevention']}")

        return "\n".join(lines)
