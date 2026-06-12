"""Professional review and reporting agent with IEEE-standard reports.

v3.3: Delegates report rendering to devagent.reporting package.
The LLM provides content; the reporting package provides structure.
"""

import json
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.artifact_registry import ArtifactRegistry


class ReviewAgent(BaseAgent):
    """Agent for professional review and executive reporting."""

    def run(self, state: AgentState) -> AgentState:
        """Compile executive report and structured summary."""
        self._generate_executive_report(state)
        self._generate_structured_summary(state)
        state.status = "FINISHED"
        state.add_trace("ReviewAgent", "completed", {
            "report_path": state.final_report or ""
        })
        return state

    def _generate_executive_report(self, state: AgentState):
        """Generate professional IEEE-style report via reporting package."""
        from ..reporting.executive import render_executive_report
        content = render_executive_report(state, task_id=state.task_id)

        out_root = state.output_root or state.input_path or "outputs"
        registry = ArtifactRegistry(out_root)
        from ..agent_core.schemas import Artifact as ArtifactModel

        report_art = ArtifactModel(
            id="", type="reports:executive", format="md",
            content=content, metadata={"filename": "executive_report.md"}
        )
        entry = registry.register_from_state(state, "reports", report_art)
        state.final_report = entry.get("path")

    def _compute_quality_gates(self, state: AgentState) -> list:
        """Compute quality gate pass/fail from pipeline state."""
        gates = []

        has_req = bool(state.requirements)
        gates.append({
            "name": "需求文档化",
            "passed": has_req,
            "detail": f"{len(state.requirements.get('functional_requirements',[]))} FRs" if has_req else "缺失"
        })

        has_design = bool(state.design_artifacts)
        gates.append({
            "name": "设计完成",
            "passed": has_design,
            "detail": f"{len(state.design_artifacts.get('module_division',[]))} 模块" if has_design else "缺失"
        })

        has_code = bool(state.code_files)
        gates.append({
            "name": "代码生成",
            "passed": has_code,
            "detail": f"{len(state.code_files)} 文件" if has_code else "缺失"
        })

        tr = state.test_results or {}
        test_c = tr.get("collected", 0)
        test_f = tr.get("failed", 0)
        gates.append({
            "name": "测试通过",
            "passed": test_c > 0 and test_f == 0,
            "detail": f"{tr.get('passed',0)}/{test_c}" if test_c > 0 else "未执行"
        })

        gates.append({
            "name": "无错误",
            "passed": len(state.errors) == 0 if state.errors else True,
            "detail": f"{len(state.errors)} 个错误" if state.errors else "清洁"
        })

        return gates

    @staticmethod
    def _generate_recommendations(state: AgentState) -> list:
        """Generate recommendations based on results."""
        recs = []
        if state.errors:
            recs.append(f"**排查错误**: {len(state.errors)} 个错误需要处理")
        tr = state.test_results or {}
        if tr.get("failed", 0) > 0:
            recs.append(f"**修复测试失败**: {tr.get('failed', 0)} 个测试未通过")
        if tr.get("collected", 0) == 0 and state.code_files:
            recs.append("**增加测试覆盖**: 已生成代码但无测试")
        if not recs:
            recs.append("✅ 所有质量检查通过。")
        return recs

    def _generate_structured_summary(self, state: AgentState):
        """Generate a structured JSON summary."""
        tr = state.test_results or {}

        summary = {
            "task_id": state.task_id,
            "task_type": state.task_type,
            "status": state.status,
            "success": state.status == "FINISHED",
            "artifacts": {
                "requirements": bool(state.requirements),
                "design": bool(state.design_artifacts),
                "code_files": len(state.code_files or []),
                "test_files": len(state.test_files or []),
            },
            "metrics": {
                "requirements_count": len((state.requirements or {}).get("functional_requirements", [])),
                "code_files_generated": len(state.code_files or []),
                "test_collected": tr.get("collected", 0),
                "test_passed": tr.get("passed", 0),
                "test_failed": tr.get("failed", 0),
            },
            "quality_gates": self._compute_quality_gates(state),
            "errors": [
                {"phase": e.get("phase", ""), "message": e.get("message", "")}
                for e in (state.errors or [])
            ],
            "warnings": [
                {"phase": w.get("phase", ""), "message": w.get("message", "")}
                for w in (state.warnings or [])
            ],
            "recommendations": self._generate_recommendations(state),
        }

        out_root = state.output_root or state.input_path or "outputs"
        registry = ArtifactRegistry(out_root)
        from ..agent_core.schemas import Artifact as ArtifactModel

        summary_art = ArtifactModel(
            id="", type="reports:summary", format="json",
            content=json.dumps(summary, ensure_ascii=False, indent=2),
            metadata={"filename": "result_summary.json"},
        )
        registry.register_from_state(state, "reports", summary_art)
