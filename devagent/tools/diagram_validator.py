"""Validator for Mermaid and PlantUML diagram syntax."""

import re


class DiagramValidator:
    """Validates diagram syntax for Mermaid and PlantUML formats."""

    @staticmethod
    def validate_mermaid(content: str) -> tuple[bool, str]:
        """Basic Mermaid syntax validation."""
        if not content or not content.strip():
            return False, "Empty diagram content"

        # Check for basic Mermaid elements
        has_graph = 'graph' in content.lower() or 'flowchart' in content.lower()
        has_class = 'classDiagram' in content
        has_state = 'stateDiagram' in content.lower()

        if not (has_graph or has_class or has_state):
            return False, "No recognizable Mermaid diagram type found (graph/flowchart/classDiagram/stateDiagram)"

        # Check for common syntax issues (multi-line aware)
        lines = content.split('\n')
        issues = []

        # Check total bracket balance across entire content
        for pair in [('(', ')'), ('[', ']'), ('{', '}')]:
            total_open = content.count(pair[0])
            total_close = content.count(pair[1])
            if total_open != total_close:
                issues.append(f"Mismatched {pair[0]}{pair[1]}: {total_open} open vs {total_close} close")

        # Check for unclosed quotes per line
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('%') or stripped.startswith('%%'):
                continue
            if stripped.count('"') % 2 != 0:
                issues.append(f"Line {i}: Unclosed double quote")

        if issues:
            return False, f"Syntax issues: {'; '.join(issues[:3])}"

        return True, "Mermaid syntax appears valid"

    @staticmethod
    def validate_plantuml(content: str) -> tuple[bool, str]:
        """Basic PlantUML syntax validation."""
        if not content or not content.strip():
            return False, "Empty diagram content"

        # Check for start/end tags
        has_start = '@startuml' in content
        has_end = '@enduml' in content

        if not has_start:
            return False, "Missing @startuml tag"
        if not has_end:
            return False, "Missing @enduml tag"

        # Check for common elements
        has_class = 'class ' in content
        has_relation = any(op in content for op in ['-->', '..>', '--', '..|>', '--|>', 'o--'])

        if not (has_class or has_relation):
            return False, "No class definitions or relationships found"

        return True, "PlantUML syntax appears valid"

    @staticmethod
    def detect_format(content: str) -> str:
        """Detect diagram format (mermaid or plantuml)."""
        if '@startuml' in content:
            return 'plantuml'
        # Mermaid typically uses graph, flowchart, classDiagram, etc.
        mermaid_keywords = ['graph ', 'flowchart ', 'classDiagram', 'stateDiagram']
        for kw in mermaid_keywords:
            if kw in content:
                return 'mermaid'
        return 'unknown'

    @staticmethod
    def validate(content: str, format_hint: str = None) -> tuple[bool, str]:
        """Auto-detect format and validate."""
        fmt = format_hint or DiagramValidator.detect_format(content)
        if fmt == 'plantuml':
            return DiagramValidator.validate_plantuml(content)
        elif fmt == 'mermaid':
            return DiagramValidator.validate_mermaid(content)
        else:
            return False, f"Unknown diagram format: {fmt}"
