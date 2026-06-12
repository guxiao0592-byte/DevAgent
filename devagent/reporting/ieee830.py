"""IEEE 830-1998 Software Requirements Specification (SRS) Renderer.

Takes structured requirements JSON from RequirementAgent → renders
a complete, standards-compliant SRS document in Markdown.

The LLM provides the CONTENT (entities, FRs, NFRs, use cases);
this module provides the STRUCTURE (document control, sections,
cross-references, traceability anchors).

IEEE 830 SRS Structure:
  1. Introduction (Purpose, Scope, Glossary, References, Overview)
  2. Overall Description (Perspective, Functions, Users, Constraints, Assumptions)
  3. Specific Requirements (Interfaces, FRs, NFRs, Security, Performance, Design Constraints)
  4. Appendices (Glossary, RTM, Issue Tracking)
"""

import datetime
import json
from .templates import (document_control_table, revision_history, glossary_section,
                        section, rtm_table)


def render_srs(requirements: dict, task_id: str = "", version: str = "1.0") -> str:
    """Render a complete IEEE 830 SRS document from structured requirements JSON.

    Args:
        requirements: The structured requirements dict from RequirementAgent.
                     Must contain: project_summary, domain_model, actors,
                     functional_requirements, nonfunctional_requirements,
                     use_cases, constraints, risk_assessment, assumptions
        task_id: Task identifier for document ID
        version: Document version string

    Returns:
        Complete SRS markdown string
    """
    if not requirements:
        return _empty_srs()

    today = datetime.date.today().isoformat()
    project = requirements.get("project_summary", {})
    project_name = project.get("name", "Unnamed Project")

    doc_id = f"SRS-{project_name.replace(' ','_')}-v{version}"

    parts = []

    # ====== Document Control ======
    parts.append(f"# 软件需求规格说明书 (SRS)\n")
    parts.append(f"> 基于 IEEE 830-1998 标准\n")
    parts.append(document_control_table(
        doc_id=doc_id, version=version, date=today,
        status="Draft"
    ))
    parts.append(revision_history([
        {"version": version, "date": today, "author": "DevAgent",
         "changes": "Initial requirements specification"}
    ]))

    # ====== 1. Introduction ======
    parts.append(section(1, "引言", ""))

    parts.append(section(2, "1.1 目的", (
        f"本文档定义了 **{project_name}** 的软件需求规格。"
        f"目标读者包括开发团队、测试团队、项目经理和利益相关者。\n\n"
        f"**项目描述**: {project.get('description', 'N/A')}"
    )))

    parts.append(section(2, "1.2 范围", (
        f"本文档涵盖 {project_name} 的完整功能需求、非功能需求、"
        f"用例规格和领域模型。"
    )))

    parts.append(section(2, "1.3 定义、缩略语与术语表", ""))
    terms = requirements.get("glossary", [])
    if not terms:
        terms = [
            {"term": "FR", "definition": "Functional Requirement — 功能需求"},
            {"term": "NFR", "definition": "Non-Functional Requirement — 非功能需求"},
            {"term": "UC", "definition": "Use Case — 用例"},
            {"term": "SRS", "definition": "Software Requirements Specification — 软件需求规格说明书"},
        ]
    parts.append(glossary_section(terms))

    parts.append(section(2, "1.4 参考文献", (
        "- IEEE 830-1998: Recommended Practice for Software Requirements Specifications\n"
        "- 项目输入文档 (requirements.md)\n"
    )))

    parts.append(section(2, "1.5 概述", (
        "本文档按以下结构组织:\n"
        "- **第2章**: 总体描述 — 产品视角、功能概述、用户特征、约束与假设\n"
        "- **第3章**: 具体需求 — 外部接口、功能需求(FR)、非功能需求(NFR)、"
        "安全需求、性能需求\n"
        "- **附录**: 术语表、需求追溯矩阵(RTM)\n"
    )))

    # ====== 2. Overall Description ======
    parts.append(section(1, "总体描述", ""))

    # 2.1 Product Perspective
    parts.append(section(2, "2.1 产品视角", (
        f"**{project_name}** 是一个 {_get_project_type(requirements)}。\n\n"
        f"**目标用户**: {project.get('target_users', 'N/A')}\n\n"
        f"**业务目标**:\n" +
        "\n".join(f"- {g}" for g in project.get("business_goals", []))
    )))

    # 2.2 Product Functions
    frs = requirements.get("functional_requirements", [])
    parts.append(section(2, "2.2 产品功能概述", (
        f"系统包含 **{len(frs)}** 个功能需求，按优先级分布:\n\n"
        f"| 优先级 | 数量 |\n"
        f"|--------|------|\n"
        f"| Critical | {sum(1 for f in frs if f.get('priority')=='critical')} |\n"
        f"| High | {sum(1 for f in frs if f.get('priority')=='high')} |\n"
        f"| Medium | {sum(1 for f in frs if f.get('priority')=='medium')} |\n"
        f"| Low | {sum(1 for f in frs if f.get('priority')=='low')} |\n"
    )))

    # 2.3 User Characteristics
    actors = requirements.get("actors", [])
    if actors:
        parts.append(section(2, "2.3 用户特征", ""))
        lines = ["| Actor ID | 角色 | 描述 | 目标 |",
                 "|----------|------|------|------|"]
        for a in actors:
            goals = ", ".join(a.get("goals", []))[:80]
            lines.append(
                f"| {a.get('id','')} | {a.get('name','')} | "
                f"{a.get('description','')[:60]} | {goals} |"
            )
        parts.append("\n".join(lines) + "\n")

    # 2.4 Constraints
    constraints = requirements.get("constraints", [])
    if constraints:
        parts.append(section(2, "2.4 约束", ""))
        for c in constraints:
            parts.append(f"- **{c.get('type','')}**: {c.get('description','')}\n")

    # 2.5 Assumptions & Dependencies
    assumptions = requirements.get("assumptions", [])
    if assumptions:
        parts.append(section(2, "2.5 假设与依赖", ""))
        for a in assumptions:
            parts.append(f"- {a}\n")

    # ====== 3. Specific Requirements ======
    parts.append(section(1, "具体需求", ""))

    # 3.1 Domain Model
    domain = requirements.get("domain_model", {})
    entities = domain.get("entities", [])
    if entities:
        parts.append(section(2, "3.1 领域模型", f"共 **{len(entities)}** 个领域实体\n"))
        for ent in entities:
            parts.append(f"### {ent.get('name', 'Entity')}\n")
            parts.append(f"- **描述**: {ent.get('description', '')}\n")
            if ent.get("attributes"):
                parts.append("| 属性 | 类型 | 约束 | 描述 |")
                parts.append("|------|------|------|------|")
                for attr in ent["attributes"]:
                    parts.append(
                        f"| {attr.get('name','')} | `{attr.get('type','')}` | "
                        f"{attr.get('constraints','')} | {attr.get('description','')} |"
                    )
                parts.append("")
            if ent.get("relationships"):
                parts.append("**关系**:\n")
                for rel in ent["relationships"]:
                    parts.append(
                        f"- {rel.get('type','')} → **{rel.get('target','')}**: "
                        f"{rel.get('description','')}\n"
                    )
                parts.append("")

    # 3.2 Functional Requirements
    if frs:
        parts.append(section(2, "3.2 功能需求 (FR)", ""))
        for fr in frs:
            fr_id = fr.get("id", "FR-??")
            fr_name = fr.get("name", "")
            priority = fr.get("priority", "medium").upper()
            priority_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
            icon = priority_icon.get(priority, "⚪")

            parts.append(f"### {fr_id} {icon} {fr_name}\n")
            parts.append(f"- **描述**: {fr.get('description','')}\n")
            parts.append(f"- **优先级**: {priority}\n")
            parts.append(f"- **参与者**: {', '.join(fr.get('actor_ids',[]))}\n")
            deps = fr.get("dependencies", [])
            if deps:
                parts.append(f"- **依赖**: {', '.join(deps)}\n")

            ac = fr.get("acceptance_criteria", [])
            if ac:
                parts.append("\n**验收标准**:\n")
                for c in ac:
                    parts.append(f"- [ ] {c}\n")
            parts.append("")

    # 3.3 Non-Functional Requirements
    nfrs = requirements.get("nonfunctional_requirements", [])
    sec_nfrs = requirements.get("security_requirements", [])
    obs_nfrs = requirements.get("observability_requirements", [])

    if nfrs:
        parts.append(section(2, "3.3 非功能需求 (NFR)", ""))
        for nfr in nfrs:
            parts.append(f"### {nfr.get('id','NFR-??')}: {nfr.get('name','')}\n")
            parts.append(f"- **类别**: {nfr.get('category','')}\n")
            parts.append(f"- **描述**: {nfr.get('description','')}\n")
            if nfr.get("target_metric"):
                parts.append(f"- **目标指标**: {nfr['target_metric']}\n")
            parts.append("")

    # 3.4 Security Requirements (OWASP-aligned)
    if sec_nfrs:
        parts.append(section(2, "3.4 安全需求 (NFR-SEC)", ""))
        parts.append("| ID | 类别 | OWASP | 描述 | 目标 |")
        parts.append("|----|------|-------|------|------|")
        for s in sec_nfrs:
            parts.append(
                f"| {s.get('id','')} | {s.get('category','')} | "
                f"{s.get('owasp_category','')} | {s.get('description','')[:60]} | "
                f"{s.get('target_metric','')[:50]} |"
            )
        parts.append("")

    # 3.5 Observability Requirements
    if obs_nfrs:
        parts.append(section(2, "3.5 可观测性需求 (NFR-OBS)", ""))
        for o in obs_nfrs:
            parts.append(
                f"- **{o.get('id','NFR-OBS-??')}** [{o.get('category','')}]: "
                f"{o.get('description','')} — 目标: {o.get('target_metric','')}\n"
            )
        parts.append("")

    # 3.6 Use Cases
    use_cases = requirements.get("use_cases", [])
    if use_cases:
        parts.append(section(2, "3.6 用例规格 (UC)", ""))
        for uc in use_cases:
            uc_id = uc.get("id", "UC-??")
            uc_name = uc.get("name", "")
            parts.append(f"### {uc_id}: {uc_name}\n")
            parts.append(f"- **参与者**: {', '.join(uc.get('actors',[]))}\n")

            preconds = uc.get("preconditions", [])
            if preconds:
                parts.append("\n**前置条件**:\n")
                for pc in preconds:
                    parts.append(f"  - {pc}\n")

            main_flow = uc.get("main_flow", [])
            if main_flow:
                parts.append("\n**主流程**:\n")
                for i, step in enumerate(main_flow, 1):
                    parts.append(f"  {i}. {step}\n")

            alt_flows = uc.get("alternative_flows", [])
            if alt_flows:
                parts.append("\n**备选流程**:\n")
                for af in alt_flows:
                    parts.append(f"  - **当 {af.get('condition','')}**:\n")
                    for s in af.get("flow", []):
                        parts.append(f"      - {s}\n")

            postconds = uc.get("postconditions", [])
            if postconds:
                parts.append("\n**后置条件**:\n")
                for pc in postconds:
                    parts.append(f"  - {pc}\n")

            rules = uc.get("business_rules", [])
            if rules:
                parts.append("\n**业务规则**:\n")
                for br in rules:
                    parts.append(f"  - {br}\n")
            parts.append("")

    # ====== 4. Risk Assessment ======
    risks = requirements.get("risk_assessment", [])
    if risks:
        parts.append(section(1, "风险评估", ""))
        parts.append("| 风险 | 概率 | 影响 | 缓解措施 | 应急方案 |")
        parts.append("|------|------|------|---------|---------|")
        for r in risks:
            parts.append(
                f"| {r.get('risk','')[:50]} | {r.get('probability','')} | "
                f"{r.get('impact','')} | {r.get('mitigation','')[:50]} | "
                f"{r.get('contingency','')[:50]} |"
            )
        parts.append("")

    # ====== Appendices ======
    parts.append(section(1, "附录", ""))

    # A. RTM placeholder
    parts.append(section(2, "附录 A: 需求追溯矩阵 (RTM)", (
        "*此矩阵将在设计阶段完成后自动填充。*\n"
    )))

    # B. Issue Tracking
    parts.append(section(2, "附录 B: 问题跟踪", (
        "| 问题 ID | 描述 | 状态 | 负责人 |\n"
        "|---------|------|------|--------|\n"
        "| — | — | — | — |\n"
    )))

    return "\n".join(parts).replace("\n\n\n", "\n\n")


def _empty_srs() -> str:
    return "# 软件需求规格说明书 (SRS)\n\n*需求数据不可用 — 请先运行 analyze_requirements*\n"


def _get_project_type(req: dict) -> str:
    """Heuristic: determine project type from requirements."""
    text = json.dumps(req).lower()
    if "api" in text or "endpoint" in text or "rest" in text:
        return "Web API 服务"
    if "cli" in text or "command" in text or "terminal" in text:
        return "命令行工具 (CLI)"
    if "web" in text or "browser" in text or "frontend" in text:
        return "Web 应用"
    if "library" in text or "sdk" in text or "package" in text:
        return "软件库/SDK"
    return "软件系统"
