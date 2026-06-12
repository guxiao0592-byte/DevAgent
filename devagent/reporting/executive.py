"""Executive Report Renderer — Final project dashboard with metrics and RTM.

Takes the complete PipelineState (or AgentState) after all phases,
renders a comprehensive executive report with:
  - Document control
  - Executive Dashboard (20+ metrics with status icons)
  - Quality Gate overview
  - Requirements Traceability Matrix (RTM)
  - Phase-by-phase summary
  - Error/Warning log
  - Recommendations
"""

import datetime
import os
from .templates import (document_control_table, revision_history, rtm_table,
                        quality_gate_table, section)


def render_executive_report(state, task_id: str = "",
                           output_dir: str = ".") -> str:
    """Render a complete executive report from pipeline/agent state.

    Args:
        state: PipelineState or AgentState with accumulated phase outputs
        task_id: Task identifier
        output_dir: Output directory for file discovery

    Returns:
        Complete executive report markdown
    """
    today = datetime.date.today().isoformat()
    status = getattr(state, 'status', 'UNKNOWN')
    doc_id = f"REP-{task_id}-v1.0"

    parts = []
    parts.append(f"# DevAgent 执行报告\n")
    parts.append(f"> 自动生成于 {today}\n")
    parts.append(document_control_table(
        doc_id=doc_id, version="1.0", date=today, status=status
    ))

    # ====== Executive Dashboard ======
    reqs = getattr(state, 'requirements', {}) or {}
    design = getattr(state, 'design_artifacts', {}) or {}
    code_files = getattr(state, 'code_files', []) or []
    tr = getattr(state, 'test_results', {}) or {}
    errors = getattr(state, 'errors', []) or []
    warnings = getattr(state, 'warnings', []) or []

    parts.append(section(1, "Executive Dashboard", ""))

    # Compute all metrics
    fr_count = len(reqs.get("functional_requirements", []))
    nfr_count = len(reqs.get("nonfunctional_requirements", []))
    sec_count = len(reqs.get("security_requirements", []))
    uc_count = len(reqs.get("use_cases", []))
    entity_count = len(reqs.get("domain_model", {}).get("entities", []))
    mod_count = len(design.get("module_division", []))
    api_count = len(design.get("api_contracts", []))
    adr_count = len(design.get("architecture_decisions", []))
    dfd_has = bool((design.get("data_flow_diagrams") or {}).get("level_0_mermaid"))
    threat_has = bool(design.get("threat_model"))
    dep_has = bool(design.get("deployment_diagram_mermaid"))
    state_has = bool(design.get("state_machine_diagrams"))

    test_c = tr.get("collected", 0)
    test_p = tr.get("passed", 0)
    test_f = tr.get("failed", 0)
    test_rate = (test_p / max(test_c, 1)) * 100 if test_c > 0 else 0

    error_count = len(errors)
    warn_count = len(warnings)

    # Determine overall health
    if error_count > 0:
        health = "🔴 有问题"
    elif test_f > 0:
        health = "🟠 测试失败"
    elif warn_count > 0:
        health = "🟡 有警告"
    elif status in ("COMPLETED", "FINISHED"):
        health = "🟢 健康"
    else:
        health = "⚪ " + status

    metrics = [
        ("📋 功能需求 (FR)", str(fr_count), "✅" if fr_count > 0 else "❌"),
        ("🔒 安全需求 (NFR-SEC)", str(sec_count), "✅" if sec_count > 0 else "⚠️"),
        ("📊 非功能需求 (NFR)", str(nfr_count), "—"),
        ("📖 用例 (UC)", str(uc_count), "—"),
        ("🏗️ 领域实体", str(entity_count), "—"),
        ("📦 架构模块", str(mod_count), "—"),
        ("🔌 API 合约", str(api_count), "—"),
        ("📝 架构决策记录 (ADR)", str(adr_count), "✅" if adr_count > 0 else "⚠️"),
        ("🔄 数据流图 (DFD)", "—", "✅" if dfd_has else "⚠️"),
        ("🛡️ 威胁模型 (STRIDE)", "—", "✅" if threat_has else "⚠️"),
        ("📐 部署图", "—", "✅" if dep_has else "⚠️"),
        ("🔄 状态机图", "—", "✅" if state_has else "—"),
        ("💻 代码文件", str(len(code_files)), "✅" if code_files else "❌"),
        ("🧪 测试收集", str(test_c), "—"),
        ("✅ 测试通过", f"{test_p}/{test_c} ({test_rate:.0f}%)",
         "✅" if test_f == 0 and test_c > 0 else "⚠️" if test_f > 0 else "⚠️"),
        ("❌ 测试失败", str(test_f), "✅" if test_f == 0 else "❌"),
        ("⚠️ 错误", str(error_count), "✅" if error_count == 0 else "❌"),
        ("📋 警告", str(warn_count), "✅" if warn_count == 0 else "⚠️"),
        ("🏥 总体健康", health, ""),
    ]

    parts.append("| 指标 | 值 | 状态 |")
    parts.append("|------|-----|------|")
    for m in metrics:
        parts.append(f"| {m[0]} | {m[1]} | {m[2]} |")
    parts.append("")

    # ====== Quality Gates ======
    qg = _compute_quality_gates(reqs, design, code_files, tr, errors)
    parts.append(quality_gate_table(qg))

    # ====== Phase Summary ======
    parts.append(section(1, "阶段摘要", ""))

    # Requirements
    parts.append(section(2, "需求分析", ""))
    if reqs:
        sm = reqs.get("project_summary", {})
        parts.append(f"**项目**: {sm.get('name','未命名')}\n")
        parts.append(f"**描述**: {sm.get('description','N/A')}\n")
        parts.append(f"- 功能需求: {fr_count} | 非功能需求: {nfr_count} | 用例: {uc_count}\n")
    else:
        parts.append("*未执行*\n")

    # Design
    parts.append(section(2, "架构设计", ""))
    if design:
        arch = design.get("architecture_overview", {})
        parts.append(f"**模式**: {arch.get('pattern','N/A')}\n")
        diagrams = []
        for k in ["class_diagram_mermaid", "er_diagram_mermaid"]:
            if design.get(k):
                diagrams.append({"class_diagram_mermaid":"类图","er_diagram_mermaid":"ER图"}.get(k,k))
        if diagrams:
            parts.append(f"**图表**: {', '.join(diagrams)}\n")
        if adr_count:
            parts.append(f"**ADR**: {adr_count} 条决策记录\n")
        if dfd_has:
            parts.append("**DFD**: ✅ Level 0 + Level 1\n")
    else:
        parts.append("*未执行*\n")

    # Implementation
    parts.append(section(2, "代码实现", ""))
    if code_files:
        parts.append(f"**生成文件**: {len(code_files)} 个\n")
        for f in sorted(code_files)[:10]:
            try:
                rel = os.path.relpath(f, output_dir)
            except ValueError:
                rel = f
            parts.append(f"  - `{rel}`\n")
        if len(code_files) > 10:
            parts.append(f"  - ... 及其他 {len(code_files)-10} 个文件\n")
    else:
        parts.append("*未执行*\n")

    # Testing
    parts.append(section(2, "测试执行", ""))
    if tr:
        parts.append(f"| 指标 | 值 |")
        parts.append(f"|------|-----|")
        parts.append(f"| 收集 | {test_c} |")
        parts.append(f"| 通过 | {test_p} |")
        parts.append(f"| 失败 | {test_f} |")
        parts.append(f"| 成功率 | {test_rate:.1f}% |\n")
    else:
        parts.append("*未执行*\n")

    # Repair
    patch = getattr(state, 'repair_patch', None)
    if patch:
        parts.append(section(2, "Bug 修复", ""))
        mod_files = patch.get("modified_files", []) if isinstance(patch, dict) else []
        parts.append(f"**修复文件**: {len(mod_files)}\n")
        for f in mod_files:
            parts.append(f"  - `{f}`\n")
        parts.append("")

    # ====== RTM ======
    parts.append(section(1, "需求追溯矩阵 (RTM)", ""))
    test_files = getattr(state, 'test_files', []) or []
    mod_list = design.get("module_division", []) if design else []
    parts.append(rtm_table(
        requirements=reqs.get("functional_requirements", []),
        design_sections=mod_list,
        code_files=code_files,
        test_files=test_files,
    ))

    # ====== Error & Warning Log ======
    if errors:
        parts.append(section(1, "错误日志", ""))
        for e in errors:
            if isinstance(e, dict):
                parts.append(f"- **[{e.get('phase','')}]** {e.get('message','')}\n")
            else:
                parts.append(f"- {str(e)[:200]}\n")

    if warnings:
        parts.append(section(1, "警告日志", ""))
        for w in warnings:
            if isinstance(w, dict):
                parts.append(f"- [{w.get('phase','')}] {w.get('message','')}\n")
            else:
                parts.append(f"- {str(w)[:200]}\n")

    # ====== Recommendations ======
    parts.append(section(1, "建议与后续步骤", ""))
    recs = []
    if test_f > 0:
        recs.append(f"🔧 **修复测试失败**: {test_f} 个测试未通过，运行 Phase 5 (Bug修复)")
    if test_c == 0 and code_files:
        recs.append("🧪 **增加测试**: 已生成代码但无测试覆盖")
    if not dfd_has:
        recs.append("📐 **生成数据流图**: 建议在设计中增加 DFD")
    if not threat_has:
        recs.append("🛡️ **补充威胁模型**: 建议增加 STRIDE 安全评估")
    if not adr_count:
        recs.append("📝 **记录架构决策**: 建议为关键设计决策生成 ADR")
    if error_count > 0:
        recs.append(f"⚠️ **排查错误**: 执行过程中发生 {error_count} 个错误")
    if not recs:
        recs.append("✅ 所有质量指标正常，项目可以交付。")

    for r in recs:
        parts.append(f"- {r}\n")

    return "\n".join(parts).replace("\n\n\n", "\n\n")


def _compute_quality_gates(reqs: dict, design: dict, code_files: list,
                          tr: dict, errors: list) -> list[dict]:
    """Compute quality gate pass/fail from pipeline state."""
    gates = []

    has_req = bool(reqs)
    gates.append({
        "name": "需求文档化",
        "passed": has_req,
        "detail": f"{len(reqs.get('functional_requirements',[]))} FRs" if has_req else "缺失"
    })

    has_design = bool(design)
    gates.append({
        "name": "设计完成",
        "passed": has_design,
        "detail": f"{len(design.get('module_division',[]))} 模块" if has_design else "缺失"
    })

    has_code = bool(code_files)
    gates.append({
        "name": "代码生成",
        "passed": has_code,
        "detail": f"{len(code_files)} 文件" if has_code else "缺失"
    })

    test_c = tr.get("collected", 0)
    test_f = tr.get("failed", 0)
    gates.append({
        "name": "测试通过",
        "passed": test_c > 0 and test_f == 0,
        "detail": f"{tr.get('passed',0)}/{test_c}" if test_c > 0 else "未执行"
    })

    gates.append({
        "name": "无错误",
        "passed": len(errors) == 0,
        "detail": f"{len(errors)} 个错误" if errors else "清洁"
    })

    return gates
