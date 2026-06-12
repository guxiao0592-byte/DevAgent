"""Diagram IR Compiler — converts structured Diagram IR to Mermaid syntax.

Guarantees valid Mermaid output because the compiler (not LLM) generates the DSL text.
Each compile_* function takes a typed IR object and returns a Mermaid code string.
"""

from __future__ import annotations
import re
from typing import Any


def _sid(s: str) -> str:
    """Make a Mermaid-safe identifier."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', s).strip('_') or 'n'


def compile_component(data: dict) -> str:
    """Component/architecture diagram → Mermaid flowchart."""
    lines = ['flowchart TD']
    nmap = {}
    for i, n in enumerate(data.get('nodes', [])):
        nid = _sid(n.get('id', f'n{i}'))
        nmap[n['id']] = nid
        kind = n.get('kind', 'service')
        if kind == 'datastore':
            lines.append(f'    {nid}[("{n["label"]}")]')
        elif kind == 'external' or kind == 'actor':
            lines.append(f'    {nid}(["{n["label"]}"])')
        else:
            lines.append(f'    {nid}["{n["label"]}"]')

    for e in data.get('edges', []):
        src = nmap.get(e.get('from', ''), _sid(e.get('from', '')))
        tgt = nmap.get(e.get('to', ''), _sid(e.get('to', '')))
        lbl = e.get('label', '')
        style = e.get('type', 'solid')
        arrow = '-.->' if style == 'dashed' else '-->'
        lines.append(f'    {src} {arrow}|"{lbl}"| {tgt}')
    return '\n'.join(lines)


def compile_sequence(data: dict) -> str:
    """Sequence diagram IR → Mermaid sequenceDiagram."""
    lines = ['sequenceDiagram']
    if data.get('title'):
        lines.append(f'    title {data["title"]}')
    lines.append('')
    pmap = {}
    for p in data.get('participants', []):
        pid = _sid(p)
        pmap[p] = pid
        lines.append(f'    participant {pid} as {p}')
    lines.append('')
    for s in data.get('steps', []):
        src = pmap.get(s.get('from', ''), _sid(s.get('from', '')))
        tgt = pmap.get(s.get('to', ''), _sid(s.get('to', '')))
        action = s.get('action', s.get('message', ''))
        typ = s.get('type', 'request')
        if typ in ('response', 'return'):
            lines.append(f'    {tgt} -->> {src}: {action}')
        elif typ == 'note':
            lines.append(f'    Note over {src},{tgt}: {action}')
        else:
            lines.append(f'    {src} ->> {tgt}: {action}')
    return '\n'.join(lines)


def compile_class(data: dict) -> str:
    """Class diagram IR → Mermaid classDiagram."""
    lines = ['classDiagram']
    for mod in data.get('modules', []):
        for cls in mod.get('classes', mod.get('key_classes', [])):
            name = cls if isinstance(cls, str) else cls.get('name', '')
            if not name:
                continue
            lines.append(f'    class `{name}` {{')
            attrs = [] if isinstance(cls, str) else cls.get('attributes', [])
            methods = [] if isinstance(cls, str) else cls.get('methods', [])
            for a in attrs:
                an, at = (a if isinstance(a, (list, tuple)) else (a.get('name', a), a.get('type', '')))
                lines.append(f'        +{at} {an}' if at else f'        +{an}')
            for m in methods:
                mn, mr = (m if isinstance(m, (list, tuple)) else (m.get('name', m), m.get('returns', '')))
                lines.append(f'        +{mn}(){mr if mr else ""}')
            lines.append('    }')

    if data.get('relationships'):
        rm = {'inheritance': ' <|-- ', 'composition': ' *-- ', 'aggregation': ' o-- ',
              'dependency': ' ..> ', 'one-to-one': '"1" --> "1"', 'one-to-many': '"1" --> "*"',
              'many-to-one': '"*" --> "1"', 'many-to-many': '"*" --> "*"'}
        for r in data['relationships']:
            arrow = rm.get(r.get('type', ''), ' --> ')
            lbl = f' : {r["label"]}' if r.get('label') else ''
            lines.append(f'    `{r["from"]}`{arrow}`{r["to"]}`{lbl}')
    return '\n'.join(lines)


def compile_er(data: dict) -> str:
    """ER diagram IR → Mermaid erDiagram."""
    lines = ['erDiagram']
    for t in data.get('tables', []):
        name = _sid(t.get('table', t.get('name', 'TABLE'))).upper()
        lines.append(f'    {name} {{')
        for c in t.get('columns', []):
            ct = c.get('type', 'VARCHAR')
            cn = c.get('name', '?')
            tags = ''
            if 'PRIMARY' in (c.get('constraints', '') or '').upper():
                tags += ' PK'
            if 'FOREIGN' in (c.get('constraints', '') or '').upper():
                tags += ' FK'
            lines.append(f'        {ct} {cn}{tags}')
        lines.append('    }')

    for t in data.get('tables', []):
        tn = _sid(t.get('table', t.get('name', ''))).upper()
        for r in t.get('relationships', []):
            cm = {'one-to-one': '||--||', 'one-to-many': '||--o{',
                  'many-to-one': '}o--||', 'many-to-many': '}o--o{'}
            arrow = cm.get(r.get('cardinality', ''), '}o--||')
            ref = _sid((r.get('references', '') or '').split('(')[0]).upper()
            desc = r.get('description', r.get('label', ''))
            lines.append(f'    {tn} {arrow} {ref} : {desc}')
    return '\n'.join(lines)


def compile_state(data: dict) -> str:
    """State machine IR → Mermaid stateDiagram-v2."""
    lines = ['stateDiagram-v2']
    for t in data.get('transitions', []):
        src = '[*]' if not t.get('from') or t['from'] == '*' else _sid(t['from'])
        dst = '[*]' if not t.get('to') or t['to'] == '*' else _sid(t['to'])
        trigger = t.get('trigger', '')
        guard = f' [{t["guard"]}]' if t.get('guard') else ''
        label = trigger + guard
        lines.append(f'    {src} --> {dst}{": " + label if label else ""}')
    return '\n'.join(lines)


def compile_flowchart(data: dict) -> str:
    """Flowchart IR → Mermaid flowchart."""
    lines = ['flowchart TD']
    if data.get('title'):
        nmap = {}
        for i, n in enumerate(data.get('nodes', [])):
            nid = _sid(n.get('id', f'n{i}'))
            nmap[n['id']] = nid
            ntype = n.get('type', 'action')
            if ntype in ('start', 'end'):
                lines.append(f'    {nid}(("{n["label"]}"))')
            elif ntype == 'decision':
                lines.append(f'    {nid}{{"{n["label"]}"}}')
            else:
                lines.append(f'    {nid}["{n["label"]}"]')

        for e in data.get('edges', []):
            src = nmap.get(e.get('from', ''), _sid(e.get('from', '')))
            tgt = nmap.get(e.get('to', ''), _sid(e.get('to', '')))
            lbl = e.get('label', '')
            lines.append(f'    {src} -->|"{lbl}"| {tgt}')
    return '\n'.join(lines)


def compile_deployment(data: dict) -> str:
    """Deployment diagram IR → Mermaid flowchart."""
    lines = ['flowchart TD']
    nmap = {}
    for i, n in enumerate(data.get('nodes', [])):
        nid = _sid(n.get('name', f'n{i}'))
        nmap[n.get('name', '')] = nid
        contains = n.get('contains', [])
        if contains:
            lines.append(f'    subgraph {nid}["{n["name"]}"]')
            for j, c in enumerate(contains):
                lines.append(f'        {nid}_{j}["{c}"]')
            lines.append('    end')
        elif n.get('type') == 'datastore':
            lines.append(f'    {nid}[("{n["name"]}")]')
        elif n.get('type') == 'external':
            lines.append(f'    {nid}(["{n["name"]}"])')
        else:
            lines.append(f'    {nid}["{n["name"]}"]')

    for n in data.get('nodes', []):
        for c in n.get('connects_to', []):
            tgt_id = nmap.get(c.get('target', c) if isinstance(c, dict) else c, _sid(c if isinstance(c, str) else ''))
            proto = c.get('protocol', '') if isinstance(c, dict) else ''
            lines.append(f'    {nmap[n["name"]]} -->|"{proto}"| {tgt_id}')
    return '\n'.join(lines)


# ============================================================================
# Compiler Registry
# ============================================================================

COMPILERS = {
    'component_diagram': compile_component,
    'sequence_diagram': compile_sequence,
    'class_diagram': compile_class,
    'er_diagram': compile_er,
    'state_diagram': compile_state,
    'flowchart': compile_flowchart,
    'deployment_diagram': compile_deployment,
    'component': compile_component,  # alias
    'sequence': compile_sequence,
    'class': compile_class,
    'er': compile_er,
    'state': compile_state,
    'deployment': compile_deployment,
}


def compile(data: dict, diagram_type: str = None) -> str:
    """Compile IR data to Mermaid syntax. Auto-detects type from 'type' field."""
    if diagram_type is None:
        diagram_type = data.get('type', 'component_diagram')

    compiler = COMPILERS.get(diagram_type)
    if compiler:
        compiled = compiler(data)
        # Add title if present in IR
        if data.get('title') and 'title' not in compiled.split('\n')[0]:
            pass  # title is embedded in individual compilers
        return compiled

    raise ValueError(f"No compiler for diagram type: {diagram_type}. Valid: {list(COMPILERS.keys())}")


def compile_and_validate(data: dict, diagram_type: str = None) -> tuple[str, list[str]]:
    """Compile IR and return (mermaid_code, [warnings])."""
    warnings = []
    try:
        code = compile(data, diagram_type)
    except Exception as e:
        return "", [f"Compilation error: {e}"]

    # Basic validation
    lines = code.strip().split('\n')
    if len(lines) < 2:
        warnings.append("Empty or too-short diagram generated")

    node_count = len(re.findall(r'\["[^"]*"\]', code))
    if node_count == 0:
        warnings.append("No nodes detected in output")
    elif node_count > 40:
        warnings.append(f"Large diagram ({node_count} nodes) — may be hard to read")

    return code, warnings
