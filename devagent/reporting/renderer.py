"""Diagram Renderer — renders diagrams to real PNG/SVG image files.

Uses kroki.io HTTP API as the sole rendering backend:
  - Supports Mermaid natively (no PlantUML conversion needed)
  - Supports PlantUML natively
  - No local installation required (just HTTP)

Mermaid types are passed directly to kroki as "mermaid".
PlantUML types are passed directly to kroki as "plantuml".
NO format conversion is performed — kroki handles both natively.

Design philosophy:
  - One backend, two diagram languages
  - Validate output: PNG magic bytes + minimum size
  - Report clear errors with debug information
"""

import os
import re
import zlib
import base64
import time
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Kroki encoding
# ============================================================================

KROKI_SERVER = "https://kroki.io"


def _kroki_encode(text: str) -> str:
    """Encode for kroki.io: zlib compress → URL-safe base64."""
    return base64.urlsafe_b64encode(
        zlib.compress(text.encode("utf-8"), 9)
    ).decode("ascii")


# ============================================================================
# Data types
# ============================================================================

@dataclass
class RenderResult:
    """Result of rendering a single diagram."""
    name: str
    format: str
    success: bool
    output_path: str = ""
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class RenderBatchResult:
    """Result of rendering a batch of diagrams."""
    diagrams: list[RenderResult] = field(default_factory=list)
    output_dir: str = ""
    total_count: int = 0
    success_count: int = 0
    total_duration_ms: float = 0.0


# ============================================================================
# PlantUML source generators — build correct PlantUML syntax from structured data
# ============================================================================

def _plantuml_class(modules: list[dict]) -> str:
    lines = ["@startuml", "skinparam classAttributeIconSize 0", ""]
    for mod in (modules or []):
        lines.append(f"' Module: {mod.get('name','Module')}")
        for cls in mod.get("classes", mod.get("key_classes", [])):
            if isinstance(cls, str):
                lines.append(f"class {cls} {{")
                lines.append("}")
            else:
                cname = cls.get("name", "Unknown")
                lines.append(f"class {cname} {{")
                for attr in cls.get("attributes", []):
                    if isinstance(attr, dict):
                        lines.append(f"  +{attr.get('type','?')} {attr.get('name','')}")
                    elif isinstance(attr, tuple):
                        lines.append(f"  +{attr[1]} {attr[0]}")
                    else:
                        lines.append(f"  +{attr}")
                for meth in cls.get("methods", []):
                    if isinstance(meth, dict):
                        ret = meth.get("returns", "void")
                        args = ", ".join(meth.get("args", []))
                        lines.append(f"  +{ret} {meth.get('name','')}({args})")
                    elif isinstance(meth, tuple):
                        lines.append(f"  +{meth[1] or 'void'} {meth[0]}()")
                    else:
                        lines.append(f"  +{meth}()")
                lines.append("}")
        lines.append("")
    if len(lines) == 4:  # Only header, no content
        return "@startuml\nclass Placeholder { +placeholder() }\n@enduml"
    lines.append("@enduml")
    return "\n".join(lines)


def _plantuml_er(tables: list[dict]) -> str:
    if not tables:
        return ""
    lines = ["@startuml", "", "' Entity-Relationship Diagram", ""]
    for table in tables:
        tname = table.get("table", "unknown").upper()
        lines.append(f"entity {tname} {{")
        for col in table.get("columns", []):
            cname = col.get("name", "?")
            ctype = col.get("type", "VARCHAR(255)")
            pk = " <<PK>>" if "PRIMARY" in str(col.get("constraints", "")).upper() else ""
            fk = " <<FK>>" if "FOREIGN" in str(col.get("constraints", "")).upper() else ""
            lines.append(f"  {ctype} {cname}{pk}{fk}")
        lines.append("}")
        lines.append("")
    for table in tables:
        tname = table.get("table", "unknown").upper()
        for rel in table.get("relationships", []):
            ref = rel.get("references", "")
            ref_table = ref.split("(")[0].upper() if "(" in ref else "UNKNOWN"
            cmap = {"one-to-one": "||--||", "one-to-many": "||--o{",
                    "many-to-one": "}o--||", "many-to-many": "}o--o{"}
            arrow = cmap.get(rel.get("cardinality", "many-to-one"), "}o--||")
            desc = rel.get("description", "")[:60]
            lines.append(f"{tname} {arrow} {ref_table} : {desc}")
    lines.append("@enduml")
    return "\n".join(lines)


def _plantuml_sequence(participants: list[str], steps: list[dict],
                       title: str = "") -> str:
    if not participants:
        return ""
    lines = ["@startuml"]
    if title:
        lines.append(f"title {title}")
    for p in participants:
        p_safe = re.sub(r'[^a-zA-Z0-9]', '', p)
        lines.append(f"participant \"{p}\" as {p_safe}" if p != p_safe else f"participant {p}")
    lines.append("")
    for step in steps:
        src = re.sub(r'[^a-zA-Z0-9]', '', step.get("from", ""))
        tgt = re.sub(r'[^a-zA-Z0-9]', '', step.get("to", ""))
        msg = step.get("action", step.get("message", ""))
        stype = step.get("type", "request")
        if stype in ("response", "return"):
            lines.append(f"{src} --> {tgt}: {msg}")
        elif stype == "note":
            lines.append(f"note over {src}, {tgt}: {msg}")
        else:
            lines.append(f"{src} -> {tgt}: {msg}")
    lines.append("@enduml")
    return "\n".join(lines)


def _plantuml_state(states: list[str], transitions: list[dict],
                   title: str = "") -> str:
    lines = ["@startuml"]
    if title: lines.append(f"title {title}")
    for t in transitions:
        src = t.get("from", "[*]")
        if src == "*": src = "[*]"
        tgt = t.get("to", "[*]")
        trigger = t.get("trigger", "")
        guard = t.get("guard", "")
        label = trigger
        if guard: label = f"{trigger} [{guard}]"
        lines.append(f"{src} --> {tgt}: {label}" if label else f"{src} --> {tgt}")
    lines.append("@enduml")
    return "\n".join(lines)


# ============================================================================
# Mermaid source helpers — clean and validate
# ============================================================================

def _clean_mermaid(content: str) -> str:
    """Extract clean Mermaid code from markdown code blocks."""
    content = content.strip()
    if content.startswith("```mermaid"):
        content = content[10:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _get_nested(d: dict, keys: list) -> str:
    val = d
    for k in keys:
        if not isinstance(val, dict): return ""
        val = val.get(k, "")
    return val if isinstance(val, str) else ""


# ============================================================================
# Sequence + State parsers from Mermaid text
# ============================================================================

def _parse_mermaid_seq_parts(mermaid: str) -> list[str]:
    parts = re.findall(r'participant\s+(\w+)', mermaid)
    if not parts:
        parts = list(set(re.findall(r'(\w+)\s*[->]{2,}', mermaid)))
    return parts


def _parse_mermaid_seq_steps(mermaid: str) -> list[dict]:
    steps = []
    for m in re.finditer(r'(\w+)\s*(->>|-->>|->|-->)\s*(\w+)\s*:\s*(.+)', mermaid):
        src, arrow, tgt, msg = m.groups()
        stype = "response" if "-->>" in arrow else "request"
        steps.append({"from": src, "to": tgt, "action": msg.strip(), "type": stype})
    return steps


def _parse_mermaid_state(mermaid: str) -> tuple:
    states = set()
    transitions = []
    for m in re.finditer(r'(\S+)\s*-->\s*(\S+)\s*:\s*(.+)', mermaid):
        src, tgt, trigger = m.groups()
        states.add(src); states.add(tgt)
        transitions.append({"from": src, "to": tgt, "trigger": trigger.strip()})
    for m in re.finditer(r'(\S+)\s*-->\s*(\S+)', mermaid):
        src, tgt = m.groups()
        if not any(t["from"] == src and t["to"] == tgt for t in transitions):
            states.add(src); states.add(tgt)
            transitions.append({"from": src, "to": tgt, "trigger": ""})
    return list(states), transitions


# ============================================================================
# Renderer
# ============================================================================

class DiagramRenderer:
    """Renders diagrams via kroki.io (supports both Mermaid AND PlantUML natively).

    Two public methods:
      render_mermaid(mermaid_code, name)   → Mermaid → kroki → PNG
      render_plantuml(plantuml_code, name)  → PlantUML → kroki → PNG

    No conversion between formats. kroki handles both.
    """

    DIAGRAM_DIR = "diagrams"

    def __init__(self, output_dir: str, fmt: str = "png", format: str = None):
        self.output_dir = output_dir
        self.fmt = format or fmt
        self._diagram_dir = os.path.join(output_dir, self.DIAGRAM_DIR)
        os.makedirs(self._diagram_dir, exist_ok=True)

    @property
    def diagram_dir(self) -> str:
        os.makedirs(self._diagram_dir, exist_ok=True)
        return self._diagram_dir

    # ==================================================================
    # Public
    # ==================================================================

    def render_mermaid(self, source: str, name: str) -> RenderResult:
        """Render Mermaid source directly via kroki (no conversion needed)."""
        return self._render(source, name, "mermaid")

    def render_plantuml(self, source: str, name: str) -> RenderResult:
        """Render PlantUML source directly via kroki."""
        if "@startuml" not in source:
            source = f"@startuml\n{source}\n@enduml"
        return self._render(source, name, "plantuml")

    def render_from_design(self, design: dict) -> RenderBatchResult:
        """Render ALL diagrams from a DesignAgent output dict."""
        t0 = time.time()
        results = []

        # === Structured → PlantUML ===

        # Class
        modules = design.get("module_division", [])
        if modules:
            results.append(self.render_plantuml(_plantuml_class(modules), "class_diagram"))

        # ER
        tables = design.get("database_schema", [])
        if tables:
            results.append(self.render_plantuml(_plantuml_er(tables), "er_diagram"))

        # Sequence (from Mermaid)
        for i, sd in enumerate(design.get("sequence_diagrams", [])):
            mermaid = sd.get("diagram_mermaid", "")
            sname = sd.get("name", f"sequence_{i+1}")
            if mermaid:
                parts = _parse_mermaid_seq_parts(mermaid)
                steps = _parse_mermaid_seq_steps(mermaid)
                if steps:
                    pu = _plantuml_sequence(parts, steps, title=sname)
                    results.append(self.render_plantuml(pu, _safe_name(f"sequence_{sname}")))
                else:
                    # Can't parse — pass Mermaid directly
                    results.append(self.render_mermaid(mermaid, _safe_name(f"sequence_{sname}")))

        # State (from Mermaid)
        for i, sm in enumerate(design.get("state_machine_diagrams", [])):
            mermaid = sm.get("diagram_mermaid", "")
            entity = sm.get("entity", f"state_{i+1}")
            if mermaid:
                states, transitions = _parse_mermaid_state(mermaid)
                if transitions:
                    pu = _plantuml_state(states, transitions, title=entity)
                    results.append(self.render_plantuml(pu, _safe_name(f"state_{entity}")))
                else:
                    results.append(self.render_mermaid(mermaid, _safe_name(f"state_{entity}")))

        # === Mermaid → Mermaid (no conversion) ===

        mermaid_fields = [
            ("context_diagram", ["architecture_overview", "context_diagram_mermaid"]),
            ("container_diagram", ["architecture_overview", "container_diagram_mermaid"]),
            ("dfd_level_0", ["data_flow_diagrams", "level_0_mermaid"]),
            ("dfd_level_1", ["data_flow_diagrams", "level_1_mermaid"]),
            ("deployment_diagram", ["deployment_diagram_mermaid"]),
        ]
        for name, path in mermaid_fields:
            code = _get_nested(design, path)
            if code:
                code = _clean_mermaid(code)
                if code:
                    results.append(self.render_mermaid(code, name))

        # === Activity Diagrams (PlantUML) ===
        activity_diagrams = design.get("activity_diagrams", [])
        for i, ad in enumerate(activity_diagrams):
            pu_content = ad.get("diagram_plantuml", "")
            sname = ad.get("name", f"activity_{i+1}")
            if pu_content:
                results.append(self.render_plantuml(pu_content,
                               _safe_name(f"activity_{sname}")))
            # If structured nodes provided, generate PlantUML from them
            nodes = ad.get("nodes", [])
            if nodes:
                from ..reporting.diagrams import activity_diagram as _gen_activity
                gen_pu = _gen_activity(sname, nodes)
                if gen_pu:
                    results.append(self.render_plantuml(gen_pu,
                                       _safe_name(f"activity_{sname}_gen")))

        # === Use Case Diagram (PlantUML) ===
        uc_puml = design.get("usecase_diagram_plantuml", "")
        if uc_puml:
            results.append(self.render_plantuml(uc_puml, "usecase_diagram"))
        # Also generate from structured actors + use cases
        actors_data = design.get("actors") or (design.get("requirements", {}) if isinstance(design.get("requirements"), dict) else {}).get("actors", [])
        use_cases_data = design.get("use_cases") or (design.get("requirements", {}) if isinstance(design.get("requirements"), dict) else {}).get("use_cases", [])
        if actors_data and use_cases_data and not uc_puml:
            from ..reporting.diagrams import usecase_diagram as _gen_uc
            gen_uc = _gen_uc(actors_data, use_cases_data)
            if gen_uc:
                results.append(self.render_plantuml(gen_uc, "usecase_diagram"))

        # Fallback: raw class/er Mermaid
        cls_mmd = design.get("class_diagram_mermaid", "")
        if cls_mmd and not modules:
            results.append(self.render_mermaid(_clean_mermaid(cls_mmd), "class_diagram"))
        er_mmd = design.get("er_diagram_mermaid", "")
        if er_mmd and not tables:
            results.append(self.render_mermaid(_clean_mermaid(er_mmd), "er_diagram"))

        # Filter out Nones
        results = [r for r in results if r is not None]

        ms = (time.time() - t0) * 1000
        return RenderBatchResult(
            diagrams=results, output_dir=self._diagram_dir,
            total_count=len(results),
            success_count=sum(1 for r in results if r.success),
            total_duration_ms=ms,
        )

    # ==================================================================
    # Core rendering
    # ==================================================================

    def _render(self, source: str, name: str, lang: str) -> RenderResult:
        """Render a diagram via kroki.io.

        Args:
            source: Complete diagram source code
            name: Output filename (without extension)
            lang: "mermaid" or "plantuml"

        Returns RenderResult with success status and output path.
        """
        t0 = time.time()
        fmt = self.fmt
        out_path = os.path.join(self.diagram_dir, f"{name}.{fmt}")

        if not source.strip():
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error="Empty source", duration_ms=0)

        # Prepare body for kroki
        body = source.strip()
        if lang == "plantuml":
            # Kroki wants PlantUML WITHOUT @startuml/@enduml
            body = body.replace("@startuml", "").replace("@enduml", "").strip()
        if not body:
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error="Empty after cleanup", duration_ms=0)

        # Request
        try:
            encoded = _kroki_encode(body)
            url = f"{KROKI_SERVER}/{lang}/{fmt}/{encoded}"
            req = urllib.request.Request(url, headers={"User-Agent": "DevAgent/3.4"})
            data = None
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as e:
                data = e.read()
        except urllib.error.URLError as e:
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error=f"Network: {e}",
                              duration_ms=(time.time()-t0)*1000)

        duration = (time.time() - t0) * 1000

        if not data:
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error="Empty response", duration_ms=duration)

        # Validate: must be PNG with minimum size
        if len(data) < 200:
            err = data[:200].decode("utf-8", errors="replace")
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error=f"Small response ({len(data)}B): {err[:100]}",
                              duration_ms=duration)

        if data[:4] != b'\x89PNG':
            err = data[:200].decode("utf-8", errors="replace")
            return RenderResult(name=name, format=fmt, success=False,
                              output_path="", error=f"Not PNG: {data[:20].hex()} — {err[:60]}",
                              duration_ms=duration)

        # Write validated PNG
        with open(out_path, "wb") as f:
            f.write(data)

        return RenderResult(name=name, format=fmt, success=True,
                          output_path=out_path, error="", duration_ms=duration)


def _safe_name(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_一-鿿-]', '_', s)[:64]


# ============================================================================
# Convenience
# ============================================================================

def render_design_diagrams(design: dict, output_dir: str,
                           fmt: str = "png") -> RenderBatchResult:
    """Render all diagrams from a DesignAgent output dict."""
    r = DiagramRenderer(output_dir, fmt=fmt)
    return r.render_from_design(design)
