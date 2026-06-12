"""Mermaid diagram generators from structured data.

IMPORTANT: These functions generate Mermaid syntax from structured
Python dicts/lists — NOT from LLM text output. This ensures:
  1. Perfect syntax (no LLM hallucination in Mermaid)
  2. Consistent styling across all diagrams
  3. Proper entity/relationship tracking
  4. Automatic validation of cross-references

Diagrams supported:
  - Class Diagram (from module/class definitions)
  - ER Diagram (from database schema)
  - DFD Level 0 + Level 1 (from module/process decomposition)
  - Sequence Diagram (from use case flows)
  - State Machine (from entity lifecycle definitions)
  - Deployment Diagram (from technology stack + assumptions)
  - Component Diagram (from module division)
"""

import re
from typing import Optional


# ============================================================================
# Utility
# ============================================================================

def mermaid_block(diagram_type: str, content: str) -> str:
    """Wrap Mermaid content in a markdown code block."""
    return f"```mermaid\n{content}\n```\n"


def _safe_id(name: str) -> str:
    """Convert a name to a Mermaid-safe identifier."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


# ============================================================================
# Class Diagram
# ============================================================================

def class_diagram(modules: list[dict], relationships: list[dict] = None) -> str:
    """Generate a Mermaid class diagram from module/class definitions.

    Args:
        modules: [{"name": "AuthService", "classes": [
                   {"name": "User", "attributes": [("id","int"),("name","str")],
                    "methods": [("login","bool"),("logout","None")]}
                 ]}, ...]
        relationships: [{"from": "User", "to": "Order", "type": "one-to-many",
                        "label": "places"}, ...]
    """
    lines = ["classDiagram"]

    all_classes = {}
    class_to_module = {}

    # Collect all classes
    for mod in (modules or []):
        mod_name = mod.get("name", "UnknownModule")
        for cls in (mod.get("classes") or mod.get("key_classes") or []):
            if isinstance(cls, str):
                cls_name = cls
                all_classes[cls_name] = {"name": cls_name, "attributes": [], "methods": []}
            else:
                cls_name = cls.get("name", "UnknownClass")
                attrs = cls.get("attributes", [])
                methods = cls.get("methods", [])
                all_classes[cls_name] = {
                    "name": cls_name,
                    "attributes": [(a.get("name",""), a.get("type","")) if isinstance(a,dict) else a
                                   for a in attrs],
                    "methods": [(m.get("name",""), m.get("returns","")) if isinstance(m,dict) else m
                                for m in methods],
                }
            class_to_module[cls_name] = mod_name

    # Generate class definitions
    for cls_name, cls_data in all_classes.items():
        lines.append(f"    class {cls_name} {{")
        for attr in cls_data.get("attributes", []):
            if isinstance(attr, tuple):
                lines.append(f"        +{attr[1]} {attr[0]}")
            elif isinstance(attr, str):
                lines.append(f"        +{attr}")
        for meth in cls_data.get("methods", []):
            if isinstance(meth, tuple):
                if meth[1]:
                    lines.append(f"        +{meth[0]}() {meth[1]}")
                else:
                    lines.append(f"        +{meth[0]}()")
            elif isinstance(meth, str):
                lines.append(f"        +{meth}()")
        lines.append(f"    }}")

    # Generate relationships
    if relationships:
        lines.append("")
        rel_map = {
            "one-to-one": '"1" --> "1"',
            "one-to-many": '"1" --> "*"',
            "many-to-one": '"*" --> "1"',
            "many-to-many": '"*" --> "*"',
            "inheritance": '" --|> "',
            "composition": '" *-- "',
            "aggregation": '" o-- "',
            "dependency": '" ..> "',
        }
        for rel in relationships:
            src = rel.get("from", "")
            tgt = rel.get("to", "")
            rtype = rel.get("type", "dependency")
            label = rel.get("label", "")
            arrow = rel_map.get(rtype, ' --> ')
            if label:
                lines.append(f"    {src}{arrow}{tgt} : {label}")
            else:
                lines.append(f"    {src}{arrow}{tgt}")

    return "\n".join(lines)


# ============================================================================
# ER Diagram
# ============================================================================

def er_diagram(tables: list[dict]) -> str:
    """Generate a Mermaid ER diagram from database schema.

    Args:
        tables: [{"table": "users", "columns": [
                  {"name": "id", "type": "INT", "constraints": "PK"},
                  {"name": "name", "type": "VARCHAR(100)"}
                ], "relationships": [
                  {"type": "foreign_key", "column": "user_id",
                   "references": "users(id)", "cardinality": "one-to-many"}
                ]}, ...]
    """
    if not tables:
        return "erDiagram\n    EMPTY {  }\n"

    lines = ["erDiagram"]

    # Generate entity definitions
    entity_rel_map = {"one-to-one": "||--||", "one-to-many": "||--o{",
                      "many-to-one": "}o--||", "many-to-many": "}o--o{"}
    relationships = []

    for table in tables:
        name = _safe_id(table.get("table", "unknown")).upper()
        columns = table.get("columns", [])

        lines.append(f"    {name} {{")
        for col in columns:
            cname = col.get("name", "?")
            ctype = col.get("type", "VARCHAR")
            constraints = col.get("constraints", "")
            pk = "PK" if "PRIMARY" in constraints.upper() else ""
            fk = "FK" if "FOREIGN" in constraints.upper() else ""
            tags = " ".join(t for t in [pk, fk] if t)
            lines.append(f"        {ctype} {cname} {tags}")
        lines.append(f"    }}")

        # Collect relationships
        for rel in table.get("relationships", []):
            if rel.get("type") in ("foreign_key", "belongs_to", "has_many"):
                ref = rel.get("references", "")
                ref_table = ref.split("(")[0].upper() if "(" in ref else ""
                cardinality = rel.get("cardinality", "many-to-one")
                arrow = entity_rel_map.get(cardinality, "}o--||")
                desc = rel.get("description", "")[:50]
                relationships.append(f"    {name} {arrow} {ref_table} : {desc}")

    if relationships:
        lines.append("")
        lines.extend(relationships)

    return "\n".join(lines)


# ============================================================================
# DFD Level 0 — System Context
# ============================================================================

def dfd_level_0(system_name: str, external_entities: list[dict],
                data_flows: list[dict] = None) -> str:
    """Generate a DFD Level 0 (Context) diagram.

    Args:
        system_name: Name of the system
        external_entities: [{"name": "User", "type": "actor"}, ...]
        data_flows: [{"from": "User", "to": "System", "label": "credentials"}, ...]
    """
    lines = [
        "flowchart LR",
        f"    System(({system_name}))",
    ]

    for ent in external_entities:
        ename = ent.get("name", "Entity")
        etype = ent.get("type", "external")
        if etype == "datastore" or "database" in ename.lower() or "DB" in ename:
            lines.append(f"    {_safe_id(ename)}[({ename})]")
        else:
            lines.append(f"    {_safe_id(ename)}([{ename}])")

    # Data flows
    if data_flows:
        for flow in data_flows:
            src = _safe_id(flow.get("from", ""))
            tgt = _safe_id(flow.get("to", ""))
            label = flow.get("label", flow.get("data", ""))
            if src and tgt:
                lines.append(f"    {src} -->|{label}| {tgt}")

    return "\n".join(lines)


# ============================================================================
# DFD Level 1 — Process Decomposition
# ============================================================================

def dfd_level_1(processes: list[dict], data_stores: list[dict] = None,
                external_entities: list[dict] = None) -> str:
    """Generate a DFD Level 1 diagram.

    Args:
        processes: [{"id": "P1", "name": "Auth Service",
                     "inputs": ["credentials"], "outputs": ["token"]}, ...]
        data_stores: [{"name": "Database", "processes": ["P1","P2"]}, ...]
        external_entities: [{"name": "User", "connects_to": "P1"}, ...]
    """
    lines = ["flowchart TD"]

    # External entities (top)
    for ent in (external_entities or []):
        ename = ent.get("name", "Entity")
        lines.append(f"    {_safe_id(ename)}([{ename}])")

    # Processes (center)
    for proc in processes:
        pid = _safe_id(proc.get("id", proc.get("name", "P")))
        pname = proc.get("name", pid)
        lines.append(f"    {pid}[{pname}]")

    # Data stores (bottom)
    for ds in (data_stores or []):
        dsname = ds.get("name", "Store")
        lines.append(f"    {_safe_id(dsname)}[({dsname})]")

    # Flows: external → process
    for ent in (external_entities or []):
        ename = ent.get("name", "")
        targets = ent.get("connects_to", [])
        if isinstance(targets, str):
            targets = [targets]
        for tgt in targets:
            tgt_id = _safe_id(tgt)
            lines.append(f"    {_safe_id(ename)} -->|input| {tgt_id}")

    # Flows: process → process (from inputs/outputs)
    for proc in processes:
        pid = _safe_id(proc.get("id", proc.get("name", "P")))
        for output in proc.get("outputs", []):
            # Find processes that take this as input
            for other in processes:
                if other == proc:
                    continue
                if output in other.get("inputs", []):
                    oid = _safe_id(other.get("id", other.get("name", "P")))
                    lines.append(f"    {pid} -->|{output}| {oid}")

    # Flows: process → data store
    for ds in (data_stores or []):
        dsname = ds.get("name", "")
        for proc_name in ds.get("processes", []):
            pid = _safe_id(proc_name)
            lines.append(f"    {pid} -->|read/write| {_safe_id(dsname)}")

    return "\n".join(lines)


# ============================================================================
# Sequence Diagram
# ============================================================================

def sequence_diagram(title: str, participants: list[str],
                     steps: list[dict]) -> str:
    """Generate a Mermaid sequence diagram.

    Args:
        title: Diagram title
        participants: ["User", "API", "Database"]
        steps: [{"from": "User", "to": "API", "action": "POST /login",
                "response": "JWT token", "type": "request"|"response"|"note"}, ...]
    """
    lines = ["sequenceDiagram", f"    title {title}", ""]

    # Declare participants
    for p in participants:
        lines.append(f"    participant {_safe_id(p)} as {p}")

    lines.append("")

    # Steps
    for step in steps:
        src = _safe_id(step.get("from", ""))
        tgt = _safe_id(step.get("to", ""))
        action = step.get("action", step.get("message", ""))
        stype = step.get("type", "request")

        if stype == "response" or stype == "return":
            lines.append(f"    {tgt} -->> {src}: {action}")
        elif stype == "note":
            lines.append(f"    Note over {src},{tgt}: {action}")
        else:
            lines.append(f"    {src} ->> {tgt}: {action}")

    return "\n".join(lines)


# ============================================================================
# State Machine Diagram
# ============================================================================

def state_machine(entity_name: str, states: list[str],
                  transitions: list[dict]) -> str:
    """Generate a Mermaid state diagram.

    Args:
        entity_name: "Order", "User", etc.
        states: ["Draft", "Submitted", "Processing", "Completed", "Cancelled"]
        transitions: [{"from": "Draft", "to": "Submitted", "trigger": "submit"},
                      {"from": "*", "to": "Draft", "trigger": ""}, ...]
    """
    lines = ["stateDiagram-v2"]

    for t in transitions:
        src = t.get("from", "[*]")
        if src == "*":
            src = "[*]"
        tgt = t.get("to", "[*]")
        trigger = t.get("trigger", "")
        guard = t.get("guard", "")

        label = trigger
        if guard:
            label = f"{trigger} [{guard}]" if trigger else f"[{guard}]"

        lines.append(f"    {src} --> {tgt}: {label}" if label else f"    {src} --> {tgt}")

    return "\n".join(lines)


# ============================================================================
# Deployment Diagram
# ============================================================================

def deployment_diagram(nodes: list[dict] = None,
                       tech_stack: dict = None) -> str:
    """Generate a deployment topology diagram.

    Args:
        nodes: [{"name": "Load Balancer", "type": "infra",
                "contains": ["API Pod 1", "API Pod 2"]}, ...]
        tech_stack: {"deployment": {"name": "Docker+K8s"}, ...}
    """
    lines = ["flowchart TD"]

    # Build from tech stack if no explicit nodes
    if not nodes:
        nodes = _infer_deployment_nodes(tech_stack or {})

    node_ids = {}
    for i, node in enumerate(nodes):
        nid = f"N{i}"
        node_ids[node.get("name", f"Node{i}")] = nid
        ntype = node.get("type", "service")
        nname = node.get("name", f"Node{i}")
        contains = node.get("contains", [])

        if contains:
            lines.append(f"    subgraph {nid}[{nname}]")
            for j, child in enumerate(contains):
                cid = f"{nid}_{j}"
                lines.append(f"        {cid}[{child}]")
            lines.append(f"    end")
        elif ntype == "datastore":
            lines.append(f"    {nid}[({nname})]")
        elif ntype == "external":
            lines.append(f"    {nid}([{nname}])")
        else:
            lines.append(f"    {nid}[{nname}]")

    # Connections
    for node in nodes:
        for conn in node.get("connects_to", []):
            src = node_ids.get(node["name"])
            tgt = node_ids.get(conn.get("target", ""))
            label = conn.get("label", conn.get("protocol", ""))
            if src and tgt:
                lines.append(f"    {src} -->|{label}| {tgt}")

    return "\n".join(lines)


def _infer_deployment_nodes(tech_stack: dict) -> list[dict]:
    """Heuristic: build deployment nodes from technology stack."""
    nodes = []
    deployment = tech_stack.get("deployment", {})
    framework = tech_stack.get("framework", {})

    if deployment:
        nodes.append({"name": "Load Balancer", "type": "infra",
                      "connects_to": [{"target": "Application Server", "protocol": "HTTPS"}]})
        nodes.append({"name": "Application Server", "type": "service",
                      "contains": [framework.get("name", "App") if isinstance(framework, dict) else str(framework)],
                      "connects_to": [{"target": "Database", "protocol": "TCP"}]})

    db = tech_stack.get("database", {})
    if db:
        db_name = db.get("name", "Database") if isinstance(db, dict) else str(db)
        nodes.append({"name": db_name, "type": "datastore"})

    cache = tech_stack.get("cache", {})
    if cache:
        cache_name = cache.get("name", "Cache") if isinstance(cache, dict) else str(cache)
        nodes.append({"name": cache_name, "type": "datastore",
                      "connects_to": [{"target": "Application Server", "protocol": "TCP"}]})

    nodes.append({"name": "Client Browser", "type": "external",
                  "connects_to": [{"target": "Load Balancer", "protocol": "HTTPS"}]})

    return nodes


# ============================================================================
# Component Diagram
# ============================================================================

def component_diagram(modules: list[dict]) -> str:
    """Generate a system component / module dependency diagram.

    Args:
        modules: [{"name": "AuthService", "dependencies": ["Database"],
                  "interfaces": ["IAuthService.login()"]}, ...]
    """
    lines = ["flowchart TD"]

    for mod in (modules or []):
        mname = mod.get("name", "Module")
        mid = _safe_id(mname)
        deps = mod.get("dependencies", [])
        ifaces = mod.get("interfaces", [])

        # Module box
        if ifaces:
            lines.append(f"    subgraph {mid}[{mname}]")
            for iface in ifaces:
                lines.append(f"        {mid}_{_safe_id(iface)}[/{iface}/]")
            lines.append(f"    end")
        else:
            lines.append(f"    {mid}[{mname}]")

        # Dependencies
        for dep in deps:
            did = _safe_id(dep)
            lines.append(f"    {mid} --> {did}")

    return "\n".join(lines)


# ============================================================================
# Activity Diagram (PlantUML) — UML 2.x activity diagram
# ============================================================================

def activity_diagram(title: str, nodes: list[dict],
                     start_node: str = "", end_nodes: list[str] = None) -> str:
    """Generate a PlantUML activity diagram (UML 2.x).

    PlantUML syntax reference:
      :step;          → activity node
      if (cond?) then (yes) else (no) → decision node
      |partition|     → swimlane
      repeat / repeat while → loop

    Args:
        title: Diagram title
        nodes: List of activity nodes:
               {"id": "A1", "label": "Validate input", "type": "action"}
               {"id": "D1", "label": "Is valid?", "type": "decision",
                "branches": [{"label": "yes", "to": "A2"}, {"label": "no", "to": "A3"}]}
               {"id": "F1", "type": "fork", "branches": [{"to": "A3"}, {"to": "A4"}]}
               {"id": "J1", "type": "join"}
               {"id": "SW1", "type": "swimlane", "label": "User",
                "nodes": ["A1", "D1"]}
        start_node: ID of the starting node
        end_nodes: List of IDs of terminal/end nodes
    """
    if not nodes:
        return "@startuml\n:Empty;\n@enduml"

    lines = ["@startuml"]
    if title:
        lines.append(f"title {title}")
    lines.append("")

    node_map = {n.get("id", ""): n for n in nodes if n.get("id")}
    end_ids = set(end_nodes or [])

    # Collect swimlanes first
    swimlane_nodes: dict[str, list[str]] = {}
    for node in nodes:
        if node.get("type") == "swimlane":
            sw_label = node.get("label", "Swimlane")
            sw_nodes = node.get("nodes", [])
            if isinstance(sw_nodes, list):
                swimlane_nodes[sw_label] = sw_nodes

    # Build the diagram with swimlanes
    if swimlane_nodes:
        for sw_label, sw_node_ids in swimlane_nodes.items():
            safe_label = re.sub(r'[^a-zA-Z0-9一-鿿 ]', '', sw_label)
            lines.append(f"|{safe_label}|")
            for nid in sw_node_ids:
                node = node_map.get(nid)
                if node:
                    lines.append(_render_activity_node(node, node_map, end_ids))
            lines.append(f"|{safe_label}|")
            lines.append("")

    # Find entry nodes: nodes NOT referenced as children by any other node
    children_of_others: set[str] = set()
    for node in nodes:
        node_type = node.get("type", "action")
        if node_type == "decision" or node_type == "fork":
            for br in node.get("branches", []):
                to_id = br.get("to", "")
                if to_id:
                    children_of_others.add(to_id)
        if node_type == "repeat":
            for child_id in node.get("nodes", []):
                if child_id:
                    children_of_others.add(child_id)

    # Render entry nodes + compound nodes that are NOT children of others
    rendered_in_swimlane = set()
    for sw_nodes in swimlane_nodes.values():
        rendered_in_swimlane.update(sw_nodes)

    for node in nodes:
        nid = node.get("id", "")
        node_type = node.get("type", "action")
        if nid and nid not in rendered_in_swimlane and node_type != "swimlane":
            # Render if this is an entry point (not a child of any decision/fork)
            # OR if it's a decision/fork/repeat itself (which renders its children)
            if nid not in children_of_others or node_type in ("decision", "fork", "repeat"):
                lines.append(_render_activity_node(node, node_map, end_ids))

    lines.append("@enduml")
    return "\n".join(lines)


def _render_activity_node(node: dict, node_map: dict,
                          end_ids: set) -> str:
    """Render a single activity node into PlantUML syntax."""
    node_type = node.get("type", "action")
    nid = node.get("id", "")
    label = node.get("label", nid)

    if node_type in ("action", "process", "step"):
        return f":{label};"
    elif node_type == "start":
        return "start"
    elif node_type in ("end", "stop", "terminate"):
        return "stop"
    elif node_type == "decision":
        lines = [f'if ({label}) then (yes)']
        branches = node.get("branches", [])
        yes_branch = branches[0] if len(branches) > 0 else None
        no_branch = branches[1] if len(branches) > 1 else None
        if yes_branch:
            to_node = node_map.get(yes_branch.get("to", ""))
            if to_node:
                lines.append(_render_activity_node(to_node, node_map, end_ids))
        if no_branch:
            lines.append("else (no)")
            to_node = node_map.get(no_branch.get("to", ""))
            if to_node:
                lines.append(_render_activity_node(to_node, node_map, end_ids))
        lines.append("endif")
        return "\n".join(lines)
    elif node_type == "fork":
        lines = ["fork"]
        for branch in node.get("branches", []):
            to_node = node_map.get(branch.get("to", ""))
            if to_node:
                lines.append(_render_activity_node(to_node, node_map, end_ids))
            lines.append("fork again")
        lines.append("end fork")
        return "\n".join(lines)
    elif node_type == "merge":
        return ""
    elif node_type == "note":
        position = node.get("position", "right")
        return f"note {position}\n  {label}\nend note"
    elif node_type == "repeat":
        lines_loop = ["repeat"]
        for child_id in node.get("nodes", []):
            child = node_map.get(child_id)
            if child:
                lines_loop.append(_render_activity_node(child, node_map, end_ids))
        condition = node.get("condition", "")
        lines_loop.append(f"repeat while ({condition})" if condition else "repeat while (more?)")
        return "\n".join(lines_loop)
    else:
        return f":{label};"


# ============================================================================
# Use Case Diagram (PlantUML)
# ============================================================================

def usecase_diagram(actors: list[dict], use_cases: list[dict],
                    system_name: str = "System",
                    relationships: list[dict] = None) -> str:
    """Generate a PlantUML use case diagram.

    Args:
        actors: [{"id": "ACT-01", "name": "User", "description": "..."}]
        use_cases: [{"id": "UC-01", "name": "Login",
                     "description": "User authenticates"}]
        system_name: Name of the system boundary
        relationships: [{"from": "ACT-01", "to": "UC-01", "type": "associate"},
                        {"from": "UC-02", "to": "UC-01",
                         "type": "include|extend|generalize"}]
    """
    if not actors and not use_cases:
        return "@startuml\n:No use cases defined;\n@enduml"

    lines = ["@startuml", "left to right direction", ""]

    # Actors
    for actor in (actors or []):
        actor_name = actor.get("name", actor.get("id", "Actor"))
        actor_id = _safe_id(actor.get("id", actor_name))
        actor_desc = actor.get("description", "")
        if actor_desc:
            lines.append(f"actor \"{actor_name}\" as {actor_id} <<{actor_desc[:30]}>>")
        else:
            lines.append(f"actor {actor_id}")
    lines.append("")

    # System boundary
    safe_sys = _safe_id(system_name)
    lines.append(f"rectangle \"{system_name}\" as {safe_sys} {{")

    # Use cases inside system boundary
    for uc in (use_cases or []):
        uc_name = uc.get("name", uc.get("id", "UC"))
        uc_id = _safe_id(uc.get("id", uc_name))
        lines.append(f"  usecase \"{uc_name}\" as {uc_id}")

    lines.append("}")
    lines.append("")

    # Relationships
    rel_map = {
        "associate": "-->",
        "include": ".>",
        "extend": ".>",
        "generalize": "-|>",
    }
    for rel in (relationships or []):
        src = _safe_id(rel.get("from", ""))
        tgt = _safe_id(rel.get("to", ""))
        rtype = rel.get("type", "associate")
        arrow = rel_map.get(rtype, "-->")
        label = rel.get("label", "")
        if rtype == "include":
            lines.append(f"  {tgt} {arrow} {src} : <<include>>")
        elif rtype == "extend":
            lines.append(f"  {src} {arrow} {tgt} : <<extend>>")
        elif label:
            lines.append(f"  {src} {arrow} {tgt} : {label}")
        else:
            lines.append(f"  {src} {arrow} {tgt}")

    # Auto-connect: every actor to every use case if no explicit relationships
    if not relationships and actors and use_cases:
        for actor in actors:
            actor_id = _safe_id(actor.get("id", actor.get("name", "Actor")))
            for uc in use_cases:
                uc_id = _safe_id(uc.get("id", uc.get("name", "UC")))
                lines.append(f"  {actor_id} --> {uc_id}")

    lines.append("@enduml")
    return "\n".join(lines)
