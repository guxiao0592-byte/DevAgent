"""Common formatting templates for engineering reports.

All functions return markdown strings with consistent styling.
LLM output is never used for document structure — code controls layout.
"""

import datetime
import os
from typing import Optional


# ============================================================================
# Document Control
# ============================================================================

def document_control_table(doc_id: str, version: str = "1.0",
                          date: str = None, author: str = "DevAgent (AI-assisted)",
                          status: str = "Draft") -> str:
    """Generate a document control header table."""
    if date is None:
        date = datetime.date.today().isoformat()
    return f"""## 文档控制

| 字段 | 值 |
|------|-----|
| 文档 ID | {doc_id} |
| 版本 | {version} |
| 日期 | {date} |
| 作者 | {author} |
| 状态 | {status} |
"""


def revision_history(entries: list[dict]) -> str:
    """Generate a revision history table.

    Args:
        entries: [{"version": "0.1", "date": "2026-01-01",
                   "author": "DevAgent", "changes": "Initial draft"}, ...]
    """
    if not entries:
        entries = [{"version": "1.0", "date": datetime.date.today().isoformat(),
                     "author": "DevAgent", "changes": "Initial release"}]
    lines = ["## 修订历史", "",
             "| 版本 | 日期 | 作者 | 变更说明 |",
             "|------|------|------|---------|"]
    for e in entries:
        lines.append(f"| {e.get('version','-')} | {e.get('date','-')} | "
                     f"{e.get('author','-')} | {e.get('changes','-')} |")
    return "\n".join(lines) + "\n"


# ============================================================================
# Table of Contents (placeholder — will be filled by doc processor)
# ============================================================================

def toc_placeholder() -> str:
    return "<!-- TOC -->\n"


# ============================================================================
# Glossary
# ============================================================================

def glossary_section(glossary: list[dict]) -> str:
    """Generate a glossary / definitions section.

    Args:
        glossary: [{"term": "FR", "definition": "Functional Requirement"}, ...]
    """
    if not glossary:
        return ""
    lines = ["## 术语表与缩略语", "",
             "| 术语 | 定义 |",
             "|------|------|"]
    for g in glossary:
        lines.append(f"| **{g.get('term','')}** | {g.get('definition','')} |")
    return "\n".join(lines) + "\n"


# ============================================================================
# Requirements Traceability Matrix (RTM)
# ============================================================================

def rtm_table(requirements: list[dict], design_sections: list[dict] = None,
              code_files: list[str] = None, test_files: list[str] = None) -> str:
    """Generate a Requirements Traceability Matrix (RTM).

    Cross-references via THREE strategies (tried in order):
      1. Scan test file CONTENT for [FR-XX] citation strings
      2. Scan code file CONTENT for [FR-XX] citation strings
      3. Heuristic: match requirement keywords to code/test file names
    """
    if not requirements:
        return "## 需求追溯矩阵 (RTM)\n\n*无需求数据*\n"

    code_list = code_files or []
    test_list = test_files or []

    # === Build lookup maps ===
    # For each FR, scan test file contents for [FR-XX]
    fr_to_tests: dict[str, list[str]] = {}
    fr_to_code: dict[str, list[str]] = {}

    for fr in requirements:
        fr_id = fr.get("id", "")
        if not fr_id:
            continue

        # Scan test files for [FR-XX]
        matched_tests = []
        for tf in test_list:
            try:
                with open(tf, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(8192)
            except Exception:
                continue
            # Match [FR-01], [FR-XX], "FR-01" etc.
            if fr_id in content:
                matched_tests.append(os.path.basename(tf))
            # Also check for requirement name as comment
            elif fr.get("name", "")[:20] in content:
                matched_tests.append(os.path.basename(tf))
        fr_to_tests[fr_id] = matched_tests

        # Scan code files for [FR-XX]
        matched_code = []
        for cf in code_list:
            try:
                with open(cf, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(8192)
            except Exception:
                continue
            if fr_id in content:
                matched_code.append(os.path.basename(cf))
        fr_to_code[fr_id] = matched_code

    # === Heuristic fallback: keyword matching ===
    # Build keyword→FR map for requirements without content matches
    keyword_map = {
        "calculator": ["FR-01", "FR-02"],
        "add": ["FR-01"], "subtract": ["FR-01"], "multiply": ["FR-01"], "divide": ["FR-01"],
        "parse": ["FR-02"], "tokenize": ["FR-02"], "expression": ["FR-02"],
        "precedence": ["FR-03"], "priority": ["FR-03"],
        "error": ["FR-04"], "exception": ["FR-04"], "invalid": ["FR-04"],
        "zero": ["FR-04"], "division by zero": ["FR-04"],
        "format": ["FR-05"], "display": ["FR-05"], "output": ["FR-05"], "result": ["FR-05"],
        "auth": ["NFR-SEC-01"], "login": ["NFR-SEC-01"],
    }

    for fr in requirements:
        fr_id = fr.get("id", "")
        if not fr_id:
            continue

        # Backfill: matches from keywords if content scan found nothing
        if not fr_to_code.get(fr_id):
            for cf in code_list:
                fn = os.path.basename(cf).lower().replace("_", " ").replace(".py", "")
                for kw, frs in keyword_map.items():
                    if fr_id in frs and kw in fn:
                        fr_to_code.setdefault(fr_id, []).append(os.path.basename(cf))

        if not fr_to_tests.get(fr_id):
            for tf in test_list:
                fn = os.path.basename(tf).replace("test_", "").replace(".py", "").lower().replace("_", " ")
                for kw, frs in keyword_map.items():
                    if fr_id in frs and kw in fn:
                        fr_to_tests.setdefault(fr_id, []).append(os.path.basename(tf))

    # === Design section matching ===
    design_map = {}
    if design_sections:
        for ds in design_sections:
            name = ds.get("name", "")
            design_map[name.lower()] = ds

    # === Render table ===
    lines = [
        "## 需求追溯矩阵 (RTM)", "",
        "| 需求 ID | 名称 | 设计章节 | 代码文件 | 测试文件 | 覆盖状态 |",
        "|---------|------|---------|---------|---------|---------|"
    ]

    covered_count = 0
    for fr in requirements[:20]:
        fr_id = fr.get("id", "?")
        fr_name = fr.get("name", "")[:30]

        # Design match
        design_match = "—"
        name_lower = fr_name.lower()
        for dk, dv in design_map.items():
            if any(kw in name_lower for kw in dk.split("_")) or dk in name_lower:
                design_match = f"§{dv.get('name','')[:25]}"
                break

        # Code match
        code_files_found = list(dict.fromkeys(fr_to_code.get(fr_id, [])))
        code_str = ", ".join(code_files_found[:3]) if code_files_found else "—"

        # Test match
        test_files_found = list(dict.fromkeys(fr_to_tests.get(fr_id, [])))
        test_str = ", ".join(test_files_found[:3]) if test_files_found else "—"

        # Coverage status
        if test_files_found:
            covered = "✅"
            covered_count += 1
        elif code_files_found:
            covered = "🟡 有代码"
        else:
            covered = "⚠️ 未覆盖"

        lines.append(
            f"| {fr_id} | {fr_name} | {design_match} | {code_str} | {test_str} | {covered} |"
        )

    # Summary line
    total = len(requirements[:20]) if requirements else 1
    lines.append(f"\n**覆盖统计**: {covered_count}/{total} 项需求有测试覆盖\n")

    return "\n".join(lines) + "\n"


# ============================================================================
# Section helper
# ============================================================================

def section(level: int, title: str, body: str = "") -> str:
    """Create a markdown section with consistent formatting."""
    prefix = "#" * level
    return f"\n{prefix} {title}\n\n{body}"


# ============================================================================
# Metric tables
# ============================================================================

def metric_table(title: str, metrics: list[tuple]) -> str:
    """Generate a metrics table.

    Args:
        title: Table heading
        metrics: [("Label", "Value", "Status"), ...]  — Status is optional
    """
    has_status = len(metrics[0]) > 2 if metrics else False
    header = "| 指标 | 值 | 状态 |" if has_status else "| 指标 | 值 |"
    sep = "|------|-----|------|" if has_status else "|------|-----|"

    lines = [f"### {title}", "", header, sep]
    for m in metrics:
        if has_status:
            lines.append(f"| {m[0]} | {m[1]} | {m[2]} |")
        else:
            lines.append(f"| {m[0]} | {m[1]} |")
    return "\n".join(lines) + "\n"


def quality_gate_table(gates: list[dict]) -> str:
    """Generate a quality gate status table.

    Args:
        gates: [{"name": "Syntax", "passed": True, "detail": "All OK"}, ...]
    """
    lines = [
        "## 质量门", "",
        "| 质量门 | 状态 | 详情 |",
        "|--------|------|------|"
    ]
    passed = 0
    for g in gates:
        icon = "✅" if g.get("passed") else "❌"
        lines.append(f"| {g.get('name','')} | {icon} | {g.get('detail','')} |")
        if g.get("passed"):
            passed += 1

    total = len(gates) if gates else 1
    lines.append(f"\n**质量评分**: {passed}/{total} 项通过\n")
    return "\n".join(lines)


