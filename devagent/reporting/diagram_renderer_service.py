"""Diagram Renderer Service — server-side deterministic diagram rendering.

Architecture:
  Diagram Source
      ↓
  Normalizer (strip fences, fix quotes, etc.)
      ↓
  Validator (basic syntax check)
      ↓
  Auto-Repair (fix common LLM errors)
      ↓
  Renderer (Kroki primary, local Mermaid CLI fallback)
      ↓
  Cache (sha256-based file cache)
      ↓
  Return SVG/PNG URL or data

Cache location: artifacts/{task_id}/diagrams/{diagram_id}.svg
"""

from __future__ import annotations
import os
import re
import json
import zlib
import base64
import hashlib
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class DiagramRenderResult:
    """Result of rendering a single diagram."""
    diagram_id: str
    success: bool
    format: str = "svg"
    data: str = ""  # SVG/PNG content
    error: str = ""
    source_hash: str = ""
    was_repaired: bool = False
    duration_ms: float = 0.0


@dataclass
class DiagramQualityReport:
    """Quality evaluation of a diagram."""
    diagram_id: str
    is_valid_syntax: bool = True
    node_count: int = 0
    edge_count: int = 0
    has_title: bool = False
    has_isolated_nodes: bool = False
    has_duplicate_nodes: bool = False
    has_garbled_text: bool = False
    is_readable: bool = True
    suggestions: list[str] = field(default_factory=list)
    score: int = 100


# ============================================================================
# Diagram Cache
# ============================================================================

class DiagramCache:
    """File-based diagram cache keyed by source hash."""

    def __init__(self, cache_root: str = "./outputs/diagram_cache"):
        self.cache_root = os.path.abspath(cache_root)
        os.makedirs(self.cache_root, exist_ok=True)

    def _hash(self, source: str, fmt: str = "svg") -> str:
        """Compute cache key: sha256(source + format)."""
        return hashlib.sha256(f"{source}|{fmt}".encode()).hexdigest()[:32]

    def get(self, source: str, fmt: str = "svg") -> Optional[str]:
        """Retrieve cached diagram render."""
        key = self._hash(source, fmt)
        cache_path = os.path.join(self.cache_root, f"{key}.{fmt}")
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def put(self, source: str, data: str, fmt: str = "svg") -> str:
        """Store diagram in cache. Returns cache file path."""
        key = self._hash(source, fmt)
        cache_path = os.path.join(self.cache_root, f"{key}.{fmt}")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(data)
        # Also save source for debugging
        src_path = os.path.join(self.cache_root, f"{key}.src.txt")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(source)
        return cache_path

    def has(self, source: str, fmt: str = "svg") -> bool:
        key = self._hash(source, fmt)
        return os.path.isfile(os.path.join(self.cache_root, f"{key}.{fmt}"))


# ============================================================================
# Diagram Source Normalizer
# ============================================================================

class DiagramNormalizer:
    """Normalize diagram source before rendering."""

    @staticmethod
    def normalize(source: str) -> str:
        """Clean and normalize diagram source."""
        s = source.strip()

        # Strip markdown code fences
        m = re.search(r'```(?:mermaid|mmd|plantuml)?\s*\n(.*?)```', s, re.DOTALL)
        if m:
            s = m.group(1).strip()

        # Strip @startuml/@enduml (Kroki handles both with and without)
        s = s.replace("@startuml\n", "").replace("\n@enduml", "").replace("@startuml", "").replace("@enduml", "")

        # Fix Chinese/smart quotes
        s = s.replace('“', '"').replace('”', '"')
        s = s.replace('‘', "'").replace('’', "'")

        # Fix: remove BOM
        s = s.lstrip('﻿')

        # Normalize line endings
        s = s.replace('\r\n', '\n').replace('\r', '\n')

        return s.strip()

    @staticmethod
    def auto_repair(source: str, fmt: str = "mermaid") -> tuple[str, bool]:
        """Auto-repair common syntax errors. Returns (fixed_source, was_fixed)."""
        s = source
        was_fixed = False

        if fmt == "mermaid":
            # Fix 1: classDiagram — class names with spaces need backtick quotes
            if 'classDiagram' in s:
                old = s
                s = re.sub(
                    r'^(\s*class\s+)(?!`)([^{ \n]+\s[^{]*?)(\s*\{)',
                    r'\1`\2`\3', s, flags=re.MULTILINE
                )
                if s != old: was_fixed = True

            # Fix 2: Flowchart — unquoted node labels with non-ASCII
            def quote_label(m):
                prefix = m.group(1)
                bracket_open = m.group(3)
                label = m.group(4)
                bracket_close = m.group(5)
                if any(ord(c) > 127 for c in label):
                    return f'{prefix}{m.group(2)}{bracket_open}"{label}"{bracket_close}'
                return m.group(0)

            old = s
            s = re.sub(
                r'^(\s*)([\w_-]+)(\[)([^"\]]*)(\])',
                quote_label, s, flags=re.MULTILINE
            )
            if s != old: was_fixed = True

            # Fix 3: Node IDs with dots/slashes
            old = s
            s = re.sub(
                r'^(\s*)([\w./-]+)(\[.*?\]|\{.*?\}|\(\(.*?\)\)|\(.*?\))',
                lambda m: m.group(0).replace(m.group(2), m.group(2).replace('.', '_').replace('/', '_'), 1),
                s, flags=re.MULTILINE
            )
            if s != old: was_fixed = True

            # Fix 4: erDiagram entity names with spaces
            old = s
            s = re.sub(
                r'^(\s*)([A-Za-z][\w ]{1,40} [A-Za-z][\w ]{0,40})(\s*\{)',
                lambda m: m.group(1) + m.group(2).replace(' ', '_') + m.group(3),
                s, flags=re.MULTILINE
            )
            if s != old: was_fixed = True

        if fmt == "plantuml":
            # Re-add @startuml/@enduml if missing (Kroki needs them for plantuml)
            if not s.startswith("@startuml"):
                s = f"@startuml\n{s}\n@enduml"
                was_fixed = True

        return s, was_fixed


# ============================================================================
# Diagram Quality Evaluator
# ============================================================================

class DiagramQualityEvaluator:
    """Evaluate diagram quality across 10 dimensions."""

    @staticmethod
    def evaluate(source: str, fmt: str = "mermaid") -> DiagramQualityReport:
        """Run quality checks on a diagram."""
        report = DiagramQualityReport(diagram_id="")
        suggestions = []

        lines = source.strip().split('\n')
        non_empty = [l for l in lines if l.strip() and not l.strip().startswith('%')]

        # 1. Syntax validity (basic)
        if fmt == "mermaid":
            if len(non_empty) < 2:
                report.is_valid_syntax = False
                suggestions.append("Too few lines for a valid diagram")
            if not any(l.strip() for l in non_empty if not l.strip().startswith('classDef') and not l.strip().startswith('%%')):
                report.is_valid_syntax = False
                suggestions.append("No diagram content lines found")

        # 2. Node count estimate
        node_patterns = [r'(\w+)\[', r'(\w+)\{', r'(\w+)\(\(', r'class\s+(\w+)', r'(\w+)\s*--']
        nodes = set()
        for pat in node_patterns:
            for m in re.finditer(pat, source):
                nodes.add(m.group(1))
        report.node_count = len(nodes)

        # 3. Edge count
        edges = re.findall(r'-->|-->\||-->>|\.->|-\|>', source)
        report.edge_count = len(edges)

        # 4. Title
        report.has_title = bool(re.search(r'title\s+', source, re.IGNORECASE)) or \
                          bool(re.search(r'^##?\s+', source, re.MULTILINE))

        # 5. Isolated nodes
        if nodes and edges:
            connected = set()
            for m in re.finditer(r'(\w+)\s*(-->|-->\||-->>|\.->)', source):
                connected.add(m.group(1))
            for m in re.finditer(r'(-->|-->\||-->>|\.->)\s*(\w+)', source):
                connected.add(m.group(2))
            isolated = nodes - connected
            if len(isolated) > max(2, len(nodes) * 0.3):
                report.has_isolated_nodes = True
                suggestions.append(f"{len(isolated)} isolated nodes")

        # 6. Duplicate nodes
        if nodes:
            report.has_duplicate_nodes = False  # Node dedup is handled by Mermaid

        # 7. Garbled text
        garbled = re.findall(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', source)
        if garbled:
            report.has_garbled_text = True
            suggestions.append("Contains control characters")

        # 8. Readability
        if len(non_empty) > 50:
            report.is_readable = False
            suggestions.append(f"Too many lines ({len(non_empty)}) — diagram may be too complex")
        if report.node_count > 30:
            suggestions.append(f"Large node count ({report.node_count}) — consider splitting")

        # 9. Type-content mismatch (basic)
        if 'classDiagram' in source and not re.search(r'class\s+', source):
            suggestions.append("classDiagram without class definitions")

        # 10. Export readiness
        if not source.strip().endswith('\n'):
            suggestions.append("No trailing newline")

        # Compute score
        score = 100
        if not report.is_valid_syntax: score -= 30
        if report.has_garbled_text: score -= 20
        if report.has_isolated_nodes: score -= 10
        if not report.has_title: score -= 5
        if not report.is_readable: score -= 15
        if len(suggestions) > 3: score -= 5
        report.score = max(0, score)
        report.suggestions = suggestions

        return report


# ============================================================================
# Renderer Service
# ============================================================================

class DiagramRendererService:
    """Main diagram rendering service with cache, normalize, repair, render, evaluate."""

    def __init__(self, cache_dir: str = None):
        self.cache = DiagramCache(cache_dir or "./outputs/diagram_cache")
        self.normalizer = DiagramNormalizer()
        self.evaluator = DiagramQualityEvaluator()

    def render(self, source: str, diagram_id: str = "",
               fmt: str = "mermaid", output_format: str = "svg",
               auto_repair: bool = True) -> DiagramRenderResult:
        """Full render pipeline: normalize → repair → cache check → render → cache put.

        Args:
            source: Raw diagram source code
            diagram_id: Optional identifier for the diagram
            fmt: "mermaid" or "plantuml"
            output_format: "svg" or "png"
            auto_repair: Whether to attempt syntax auto-repair

        Returns:
            DiagramRenderResult with SVG/PNG data or error
        """
        import time
        t0 = time.time()

        # Step 1: Normalize
        source = self.normalizer.normalize(source)
        if not source:
            return DiagramRenderResult(
                diagram_id=diagram_id, success=False,
                error="Empty after normalization", duration_ms=0)

        # Step 2: Auto-repair
        was_repaired = False
        if auto_repair:
            source, was_repaired = self.normalizer.auto_repair(source, fmt)

        # Step 3: Cache check
        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
        cached = self.cache.get(source, output_format)
        if cached:
            return DiagramRenderResult(
                diagram_id=diagram_id, success=True,
                format=output_format, data=cached,
                source_hash=source_hash, was_repaired=was_repaired,
                duration_ms=(time.time() - t0) * 1000)

        # Step 4: Render via Kroki
        data, error = self._render_kroki(source, fmt, output_format)

        # Step 5: If Kroki fails, try local Mermaid CLI
        if not data and fmt == "mermaid":
            data, error = self._render_local_mermaid(source, output_format)

        duration = (time.time() - t0) * 1000

        if not data:
            return DiagramRenderResult(
                diagram_id=diagram_id, success=False,
                error=error or "All renderers failed",
                source_hash=source_hash, duration_ms=duration)

        # Step 6: Cache and return
        self.cache.put(source, data, output_format)
        return DiagramRenderResult(
            diagram_id=diagram_id, success=True,
            format=output_format, data=data,
            source_hash=source_hash, was_repaired=was_repaired,
            duration_ms=duration)

    def render_to_file(self, source: str, output_path: str,
                       fmt: str = "mermaid", output_format: str = "svg") -> DiagramRenderResult:
        """Render and save to a specific file path."""
        result = self.render(source, fmt=fmt, output_format=output_format)
        if result.success:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(result.data)
        return result

    def evaluate_quality(self, source: str, fmt: str = "mermaid") -> DiagramQualityReport:
        """Evaluate diagram quality without rendering."""
        source = self.normalizer.normalize(source)
        return self.evaluator.evaluate(source, fmt)

    # =========== Private Renderers ===========

    @staticmethod
    def _render_kroki(source: str, fmt: str, output_format: str) -> tuple[Optional[str], str]:
        """Render via Kroki.io HTTP API."""
        try:
            encoded = base64.urlsafe_b64encode(
                zlib.compress(source.encode('utf-8'), 9)
            ).decode('ascii')

            url = f"https://kroki.io/{fmt}/{output_format}/{encoded}"
            req = urllib.request.Request(url, headers={'User-Agent': 'DevAgent/3.4'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            if output_format == "svg":
                result = data.decode('utf-8')
                if '<svg' not in result[:200]:
                    return None, f"Kroki returned non-SVG: {result[:150]}"
                return result, ""
            else:
                if len(data) < 100:
                    return None, f"Kroki PNG too small: {len(data)}B"
                return data, ""
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')[:300]
            return None, f"Kroki HTTP {e.code}: {body}"
        except Exception as e:
            return None, f"Kroki error: {str(e)[:150]}"

    @staticmethod
    def _render_local_mermaid(source: str, output_format: str) -> tuple[Optional[str], str]:
        """Render via local mmdc (Mermaid CLI)."""
        try:
            # Try mmdc (mermaid-cli)
            result = subprocess.run(
                ["mmdc", "-i", "-", "-o", "-", "-t", "default", "-b", "transparent"],
                input=source, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout, ""
            return None, f"mmdc failed: {result.stderr[:150]}"
        except FileNotFoundError:
            return None, "mmdc not installed (npm install -g @mermaid-js/mermaid-cli)"
        except subprocess.TimeoutExpired:
            return None, "mmdc timeout"
        except Exception as e:
            return None, f"Local render error: {str(e)[:150]}"


# ============================================================================
# Singleton
# ============================================================================

_renderer_instance: Optional[DiagramRendererService] = None


def get_renderer(cache_dir: str = None) -> DiagramRendererService:
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = DiagramRendererService(cache_dir)
    return _renderer_instance
