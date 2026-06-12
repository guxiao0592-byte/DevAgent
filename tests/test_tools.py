"""Tests for tool modules."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.tools.file_tool import FileTool
from devagent.tools.diagram_validator import DiagramValidator
from devagent.tools.patch_tool import PatchTool


class TestFileTool:
    def setup_method(self):
        self.tool = FileTool()
        self.tmpdir = tempfile.mkdtemp()

    def test_write_and_read_text(self):
        path = os.path.join(self.tmpdir, "test.txt")
        self.tool.write_text(path, "Hello, World!")
        content = self.tool.read_text(path)
        assert content == "Hello, World!"

    def test_write_json(self):
        path = os.path.join(self.tmpdir, "test.json")
        self.tool.write_json(path, {"key": "value"})
        import json
        with open(path) as f:
            assert json.load(f) == {"key": "value"}

    def test_ensure_dir(self):
        new_dir = os.path.join(self.tmpdir, "a", "b", "c")
        created = self.tool.ensure_dir(new_dir)
        assert os.path.exists(new_dir)

    def test_list_files(self):
        self.tool.write_text(os.path.join(self.tmpdir, "a.py"), "")
        self.tool.write_text(os.path.join(self.tmpdir, "b.py"), "")
        self.tool.write_text(os.path.join(self.tmpdir, "c.txt"), "")
        files = self.tool.list_files(self.tmpdir, ".py")
        assert len(files) == 2

    def test_read_nonexistent(self):
        try:
            self.tool.read_text("/nonexistent/file.txt")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_safe_path_valid(self):
        path = self.tool.safe_path(self.tmpdir, "subdir", "file.txt")
        assert path.startswith(self.tmpdir)

    def test_safe_path_traversal(self):
        try:
            self.tool.safe_path(self.tmpdir, "..", "..", "etc", "passwd")
            assert False, "Should have raised PermissionError"
        except PermissionError:
            pass


class TestDiagramValidator:
    def test_validate_mermaid_class(self):
        valid, msg = DiagramValidator.validate_mermaid("classDiagram\n    class Student {\n        +str name\n    }")
        assert valid is True

    def test_validate_mermaid_empty(self):
        valid, msg = DiagramValidator.validate_mermaid("")
        assert valid is False

    def test_validate_plantuml_valid(self):
        content = "@startuml\nclass Student {}\n@enduml"
        valid, msg = DiagramValidator.validate_plantuml(content)
        assert valid is True

    def test_validate_plantuml_no_end(self):
        content = "@startuml\nclass Student {}"
        valid, msg = DiagramValidator.validate_plantuml(content)
        assert valid is False

    def test_detect_mermaid(self):
        fmt = DiagramValidator.detect_format("graph TD\nA-->B")
        assert fmt == "mermaid"

    def test_detect_plantuml(self):
        fmt = DiagramValidator.detect_format("@startuml\n@enduml")
        assert fmt == "plantuml"


class TestPatchTool:
    def test_generate_diff(self):
        original = "line1\nline2\n"
        repaired = "line1\nline2_modified\n"
        diff = PatchTool.generate_patch_from_text(original, repaired, "test.py")
        assert "test.py" in diff
        assert "line2" in diff

    def test_generate_patch_report(self):
        report = PatchTool.format_patch_report(
            "diff content", "orig.py", ["fixed.py"], True
        )
        assert report["patch_applied"] is True
        assert report["original_file"] == "orig.py"
        assert report["regression_pass"] is True
