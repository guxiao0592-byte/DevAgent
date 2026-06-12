"""IEEE 1016-2009 Software Design Description (SDD) Renderer.

Takes structured design JSON from DesignAgent → renders a complete,
standards-compliant SDD document in Markdown with embedded diagrams.

The LLM provides the CONTENT (modules, classes, schemas, contracts);
this module provides the STRUCTURE and generates CORRECT diagrams.

IEEE 1016 SDD Structure:
  1. Design Overview (Architecture, Goals, Key Decisions)
  2. Architectural Views (Logical, Process, Development, Physical, Data, Security)
  3. Detailed Design (Module Specs, Interface Specs, Database Design)
  4. Requirements Traceability
  5. Appendices (Glossary, ADRs, Technology Stack)
"""

import datetime
from .templates import (document_control_table, revision_history, glossary_section,
                        section)
from .diagrams import (class_diagram, er_diagram, dfd_level_0, dfd_level_1,
                       sequence_diagram, state_machine, deployment_diagram,
                       component_diagram, mermaid_block)


def render_sdd(design: dict, requirements: dict = None,
               task_id: str = "", version: str = "1.0") -> str:
    """Render a complete IEEE 1016 SDD document.

    Args:
        design: Structured design dict from DesignAgent. Must contain:
                architecture_overview, module_division, class_diagram_mermaid,
                er_diagram_mermaid, sequence_diagrams, database_schema,
                api_contracts, technology_stack, key_interfaces
        requirements: Optional requirements dict for traceability
        task_id: Task identifier
        version: Document version

    Returns:
        Complete SDD markdown string with embedded diagrams
    """
    if not design:
        return _empty_sdd()

    today = datetime.date.today().isoformat()
    arch = design.get("architecture_overview", {})
    project_name = (requirements or {}).get("project_summary", {}).get("name",
                   arch.get("pattern", "Unnamed Project"))

    doc_id = f"SDD-{project_name.replace(' ','_')}-v{version}"

    parts = []

    # ====== Document Control ======
    parts.append(f"# 软件设计说明书 (SDD)\n")
    parts.append(f"> 基于 IEEE 1016-2009 标准\n")
    parts.append(document_control_table(
        doc_id=doc_id, version=version, date=today, status="Draft"
    ))
    parts.append(revision_history([
        {"version": version, "date": today, "author": "DevAgent",
         "changes": "Initial design specification"}
    ]))

    # ====== 1. Design Overview ======
    parts.append(section(1, "设计概述", ""))

    parts.append(section(2, "1.1 系统架构", (
        f"**架构模式**: {arch.get('pattern', '未指定')}\n"
    )))

    # Key Design Decisions
    decisions = design.get("architecture_decisions", [])
    if decisions:
        parts.append(section(2, "1.2 关键设计决策 (ADR)", ""))
        for adr in decisions:
            adr_id = adr.get("id", "ADR-??")
            parts.append(f"### {adr_id}: {adr.get('title','')}\n")
            parts.append(f"- **状态**: {adr.get('status','Proposed')}\n")
            parts.append(f"- **背景**: {adr.get('context','')}\n")
            parts.append(f"- **决策**: {adr.get('decision','')}\n")
            parts.append(f"- **后果**: {adr.get('consequences','')}\n")
            alts = adr.get("alternatives", [])
            if alts:
                parts.append("- **考虑的替代方案**:\n")
                for alt in alts:
                    parts.append(f"  - {alt.get('name','')}: {alt.get('reason_rejected','')}\n")
            parts.append("")

    # Design Goals
    parts.append(section(2, "1.3 设计目标", (
        "- **可维护性**: 模块化设计，单一职责原则\n"
        "- **可测试性**: 依赖注入，接口隔离\n"
        "- **安全性**: 纵深防御，最小权限原则\n"
        "- **可扩展性**: 开放/封闭原则，策略模式\n"
    )))

    # ====== 2. Architectural Views ======
    parts.append(section(1, "架构视图", ""))

    # 2.1 Context Diagram
    context_diagram = arch.get("context_diagram_mermaid", "")
    if context_diagram:
        parts.append(section(2, "2.1 系统上下文图 (C4 Level 1)", ""))
        parts.append(mermaid_block("flowchart", _clean_mermaid(context_diagram)))

    # 2.2 Container Diagram
    container_diagram = arch.get("container_diagram_mermaid", "")
    if container_diagram:
        parts.append(section(2, "2.2 容器图 (C4 Level 2)", ""))
        parts.append(mermaid_block("flowchart", _clean_mermaid(container_diagram)))

    # 2.3 DFD Level 0 + Level 1
    dfds = design.get("data_flow_diagrams", {})
    if isinstance(dfds, dict):
        dfd0 = dfds.get("level_0_mermaid", "")
        if dfd0:
            parts.append(section(2, "2.3 数据流图 Level 0 (系统上下文)", ""))
            parts.append(mermaid_block("flowchart", _clean_mermaid(dfd0)))

        dfd1 = dfds.get("level_1_mermaid", "")
        if dfd1:
            parts.append(section(2, "2.4 数据流图 Level 1 (过程分解)", ""))
            parts.append(mermaid_block("flowchart", _clean_mermaid(dfd1)))

    # 2.5 Module Division
    modules = design.get("module_division", [])
    if modules:
        parts.append(section(2, "2.5 模块分解", ""))
        for mod in modules:
            mname = mod.get("name", "")
            parts.append(f"### {mname}\n")
            parts.append(f"- **职责**: {mod.get('responsibility','')}\n")
            parts.append(f"- **依赖**: {', '.join(mod.get('dependencies',[]))}\n")
            ifaces = mod.get("interfaces", [])
            if ifaces:
                parts.append("- **接口**:\n")
                for iface in ifaces:
                    parts.append(f"  - `{iface}`\n")
            classes = mod.get("key_classes", [])
            if classes:
                parts.append(f"- **关键类**: `{'`, `'.join(classes)}`\n")
            parts.append("")

        # Component diagram from modules
        try:
            comp_diag = component_diagram(modules)
            parts.append("#### 模块依赖图\n")
            parts.append(mermaid_block("flowchart", comp_diag))
        except Exception:
            pass

    # 2.6 Class Diagram
    class_mmd = design.get("class_diagram_mermaid", "")
    if class_mmd:
        parts.append(section(2, "2.6 类图", ""))
        parts.append(mermaid_block("classDiagram", _clean_mermaid(class_mmd)))

    # 2.7 ER Diagram
    er_mmd = design.get("er_diagram_mermaid", "")
    if er_mmd:
        parts.append(section(2, "2.7 实体关系图 (ER)", ""))
        parts.append(mermaid_block("erDiagram", _clean_mermaid(er_mmd)))

    # 2.8 Database Schema
    db_schema = design.get("database_schema", [])
    if db_schema:
        parts.append(section(2, "2.8 数据库设计", ""))
        for table in db_schema:
            tname = table.get("table", "unknown")
            parts.append(f"#### 表: `{tname}`\n")
            parts.append(f"**描述**: {table.get('description','')}\n")
            cols = table.get("columns", [])
            if cols:
                parts.append("| 列名 | 类型 | 约束 | 可空 |")
                parts.append("|------|------|------|------|")
                for col in cols:
                    nullable = "YES" if col.get("nullable") else "NO"
                    parts.append(
                        f"| {col.get('name','')} | {col.get('type','')} | "
                        f"{col.get('constraints','')} | {nullable} |"
                    )
                parts.append("")
            indexes = table.get("indexes", [])
            if indexes:
                parts.append(f"**索引**: `{'`, `'.join(indexes)}`\n")
            rels = table.get("relationships", [])
            if rels:
                parts.append("**外键关系**:\n")
                for rel in rels:
                    parts.append(
                        f"- `{rel.get('column','')}` → {rel.get('references','')} "
                        f"(ON DELETE {rel.get('on_delete','CASCADE')})\n"
                    )
            parts.append("")

    # 2.9 Sequence Diagrams
    seq_diagrams = design.get("sequence_diagrams", [])
    if seq_diagrams:
        parts.append(section(2, "2.9 时序图", ""))
        for sd in seq_diagrams:
            parts.append(f"### {sd.get('name','Sequence')}\n")
            content = sd.get("diagram_mermaid", "")
            if content:
                parts.append(mermaid_block("sequenceDiagram", _clean_mermaid(content)))

    # 2.10 State Machine Diagrams
    state_diagrams = design.get("state_machine_diagrams", [])
    if state_diagrams:
        parts.append(section(2, "2.10 状态机图", ""))
        for sm in state_diagrams:
            parts.append(f"### {sm.get('entity','Entity')} 状态机\n")
            content = sm.get("diagram_mermaid", "")
            if content:
                parts.append(mermaid_block("stateDiagram-v2", _clean_mermaid(content)))

    # 2.11 Deployment Diagram
    dep_diag = design.get("deployment_diagram_mermaid", "")
    if dep_diag:
        parts.append(section(2, "2.11 部署图", ""))
        parts.append(mermaid_block("flowchart", _clean_mermaid(dep_diag)))

    # ====== 3. Detailed Design ======
    parts.append(section(1, "详细设计", ""))

    # 3.1 API Contracts
    api_contracts = design.get("api_contracts", [])
    if api_contracts:
        parts.append(section(2, "3.1 API 合约", ""))
        for api in api_contracts:
            endpoint = api.get("endpoint", "")
            auth = "🔒" if api.get("auth_required") else "🔓"
            parts.append(f"### {auth} {endpoint}\n")
            parts.append(f"**描述**: {api.get('description','')}\n")
            req_body = api.get("request_body", {})
            if req_body:
                parts.append(f"**请求**: `{req_body.get('content_type','application/json')}`\n")
                schema = req_body.get("schema", {})
                if schema:
                    parts.append("```json\n" + _format_json(schema) + "\n```\n")
            responses = api.get("responses", {})
            if responses:
                parts.append("**响应**:\n")
                for code, resp in responses.items():
                    parts.append(f"- **{code}**: {resp.get('description','')}\n")
            parts.append("")

    # 3.2 Key Interfaces
    interfaces = design.get("key_interfaces", [])
    if interfaces:
        parts.append(section(2, "3.2 关键接口", ""))
        for iface in interfaces:
            parts.append(
                f"- **{iface.get('name','')}** (`{iface.get('module','')}`)\n"
                f"  - 签名: `{iface.get('method_signature','')}`\n"
                f"  - 描述: {iface.get('description','')}\n\n"
            )

    # ====== 4. Technology Stack ======
    tech = design.get("technology_stack", {})
    if tech:
        parts.append(section(1, "技术栈", ""))
        parts.append("| 类别 | 选择 | 理由 |")
        parts.append("|------|------|------|")
        for cat, val in tech.items():
            if isinstance(val, dict):
                parts.append(f"| {cat} | {val.get('name','')} | {val.get('rationale','')} |")
            elif val:
                parts.append(f"| {cat} | {val} | — |")
        parts.append("")

    # ====== 5. Security View (STRIDE) ======
    threats = design.get("threat_model", [])
    if threats:
        parts.append(section(1, "安全视图 (STRIDE 威胁模型)", ""))
        for t in threats:
            comp = t.get("component", "Unknown")
            parts.append(f"## {comp}\n")
            parts.append("| 威胁类别 | 风险等级 | 缓解措施 |")
            parts.append("|---------|---------|---------|")
            for cat in ["spoofing", "tampering", "repudiation",
                        "information_disclosure", "denial_of_service",
                        "elevation_of_privilege"]:
                cdata = t.get(cat, {}) or {}
                risk = cdata.get("risk", "—")
                mitigation = cdata.get("mitigation", "—")
                parts.append(f"| {cat.replace('_',' ').title()} | {risk} | {mitigation} |")
            parts.append("")

    # ====== Appendices ======
    parts.append(section(1, "附录", ""))

    # Glossary
    terms = [{"term": "ADR", "definition": "Architecture Decision Record — 架构决策记录"},
             {"term": "C4", "definition": "Context, Containers, Components, Code — 架构可视化模型"},
             {"term": "DFD", "definition": "Data Flow Diagram — 数据流图"},
             {"term": "STRIDE", "definition": "Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege — 威胁建模框架"}]
    parts.append(glossary_section(terms))

    return "\n".join(parts).replace("\n\n\n", "\n\n")


def _clean_mermaid(content: str) -> str:
    """Extract clean Mermaid from markdown code blocks."""
    if "```mermaid" in content:
        content = content.split("```mermaid")[1]
        if "```" in content:
            content = content.split("```")[0]
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
    return content.strip()


def _format_json(obj, indent=2) -> str:
    import json
    return json.dumps(obj, indent=indent, ensure_ascii=False)


def _empty_sdd() -> str:
    return "# 软件设计说明书 (SDD)\n\n*设计数据不可用 — 请先运行 design_architecture*\n"
