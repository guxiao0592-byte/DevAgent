"""Tests for the DevAgent V2 Agentic tool system."""

import os
import sys
import tempfile
import asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.tools import (
    ToolRegistry, ToolResult,
    FileReadTool, FileEditTool, FileWriteTool, FileListTool,
    GrepTextTool, GrepASTTool, FindSymbolTool,
    ShellRunTool, TestRunTool, LintCheckTool,
    GitDiffTool, GitLogTool, GitBlameTool,
    WebSearchTool, ReadDocsTool, SubmitTool,
    PathSandbox,
)


# ============================================================================
# Helpers
# ============================================================================

def run_async(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.run(coro)


def make_workspace():
    """Create a temporary workspace with test files."""
    tmp = tempfile.mkdtemp()
    # Create a sample Python file
    src_dir = os.path.join(tmp, "src")
    os.makedirs(src_dir, exist_ok=True)
    sample_py = os.path.join(src_dir, "calc.py")
    with open(sample_py, "w") as f:
        f.write('''"""Simple calculator module."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def divide(a: float, b: float) -> float:
    """Divide a by b. Raises on zero."""
    if b == 0:
        raise ValueError("Division by zero")
    return a / b


class Calculator:
    """A simple calculator class."""

    def __init__(self, initial: int = 0):
        self.value = initial

    def add(self, n: int) -> int:
        self.value += n
        return self.value

    def get_value(self) -> int:
        return self.value


TOTAL_COUNT = 0
''')
    # Create a test file
    tests_dir = os.path.join(tmp, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    test_py = os.path.join(tests_dir, "test_calc.py")
    with open(test_py, "w") as f:
        f.write('''import pytest
from src.calc import add, divide, Calculator


def test_add():
    assert add(2, 3) == 5


def test_divide():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(10, 0)


def test_calculator():
    c = Calculator(10)
    assert c.add(5) == 15
    assert c.get_value() == 15
''')
    return tmp


# ============================================================================
# Test PathSandbox
# ============================================================================

class TestPathSandbox:
    def test_resolve_relative(self):
        sandbox = PathSandbox("/tmp/test")
        resolved = sandbox.resolve("src/file.py")
        # macOS /tmp is a symlink to /private/tmp
        assert resolved.name == "file.py"
        assert str(resolved).endswith("/tmp/test/src/file.py")

    def test_resolve_traversal_blocked(self):
        sandbox = PathSandbox("/tmp/test")
        with pytest.raises(PermissionError):
            sandbox.resolve("../etc/passwd")

    def test_relative(self):
        sandbox = PathSandbox("/tmp/test")
        path = sandbox.resolve("src/file.py")
        assert sandbox.relative(path) == "src/file.py"


# ============================================================================
# Test ToolRegistry
# ============================================================================

class TestToolRegistry:
    def test_create_default(self):
        reg = ToolRegistry.create_default()
        # 16 base tools + up to 3 GitHub tools (if gh CLI or GITHUB_TOKEN available)
        assert len(reg.list_names()) >= 16

    def test_get_existing_tool(self):
        reg = ToolRegistry.create_default()
        tool = reg.get("file_read")
        assert isinstance(tool, FileReadTool)

    def test_get_missing_tool(self):
        reg = ToolRegistry.create_default()
        assert reg.get("nonexistent") is None

    def test_execute_missing_tool(self):
        reg = ToolRegistry.create_default()
        result = run_async(reg.execute("nonexistent", {}, "/tmp"))
        assert result.success is False
        assert "Unknown tool" in result.error

    def test_openai_schemas(self):
        reg = ToolRegistry.create_default()
        schemas = reg.get_openai_schemas()
        assert len(schemas) >= 16
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]

    def test_descriptions(self):
        reg = ToolRegistry.create_default()
        desc = reg.get_descriptions()
        assert "file_read" in desc
        assert "grep_text" in desc
        assert "shell_run" in desc


# ============================================================================
# Test FileReadTool
# ============================================================================

class TestFileReadTool:
    def test_read_file(self):
        ws = make_workspace()
        tool = FileReadTool()
        result = run_async(tool.execute({"path": "src/calc.py"}, ws))
        assert result.success
        assert "def add" in result.output
        assert result.structured["total_lines"] > 10

    def test_read_with_offset_limit(self):
        ws = make_workspace()
        tool = FileReadTool()
        result = run_async(tool.execute({"path": "src/calc.py", "offset": 3, "limit": 3}, ws))
        assert result.success
        assert result.structured["shown"] == 3

    def test_read_missing_file(self):
        ws = make_workspace()
        tool = FileReadTool()
        result = run_async(tool.execute({"path": "nonexistent.py"}, ws))
        assert result.success is False
        assert "not found" in result.error


# ============================================================================
# Test FileEditTool
# ============================================================================

class TestFileEditTool:
    def test_edit_single_occurrence(self):
        ws = make_workspace()
        tool = FileEditTool()
        result = run_async(tool.execute({
            "path": "src/calc.py",
            "old_string": "TOTAL_COUNT = 0",
            "new_string": "TOTAL_COUNT = 100"
        }, ws))
        assert result.success
        assert "TOTAL_COUNT = 100" in open(os.path.join(ws, "src/calc.py")).read()

    def test_edit_not_found(self):
        ws = make_workspace()
        tool = FileEditTool()
        result = run_async(tool.execute({
            "path": "src/calc.py",
            "old_string": "this text does not exist anywhere",
            "new_string": "replacement"
        }, ws))
        assert result.success is False
        assert "not found" in result.error

    def test_edit_multiple_no_replace_all(self):
        ws = make_workspace()
        tool = FileEditTool()
        result = run_async(tool.execute({
            "path": "src/calc.py",
            "old_string": "return",
            "new_string": "return"
        }, ws))
        assert result.success is False
        assert "identical" in result.error.lower() or "same" in result.error.lower()

    def test_edit_same_string(self):
        ws = make_workspace()
        tool = FileEditTool()
        result = run_async(tool.execute({
            "path": "src/calc.py",
            "old_string": "TOTAL_COUNT = 0",
            "new_string": "TOTAL_COUNT = 0"
        }, ws))
        assert result.success is False
        assert "identical" in result.error.lower()


# ============================================================================
# Test FileWriteTool
# ============================================================================

class TestFileWriteTool:
    def test_write_new_file(self):
        ws = make_workspace()
        tool = FileWriteTool()
        result = run_async(tool.execute({
            "path": "new_file.py",
            "content": "print('hello')"
        }, ws))
        assert result.success
        assert os.path.exists(os.path.join(ws, "new_file.py"))

    def test_write_creates_dirs(self):
        ws = make_workspace()
        tool = FileWriteTool()
        result = run_async(tool.execute({
            "path": "deeply/nested/dir/file.py",
            "content": "# test"
        }, ws))
        assert result.success
        assert os.path.exists(os.path.join(ws, "deeply/nested/dir/file.py"))


# ============================================================================
# Test FileListTool
# ============================================================================

class TestFileListTool:
    def test_list_root(self):
        ws = make_workspace()
        tool = FileListTool()
        result = run_async(tool.execute({"path": "."}, ws))
        assert result.success
        assert "src/" in result.output
        assert "tests/" in result.output

    def test_list_with_filter(self):
        ws = make_workspace()
        tool = FileListTool()
        result = run_async(tool.execute({"path": "src", "include": "*.py"}, ws))
        assert result.success
        assert "calc.py" in result.output


# ============================================================================
# Test GrepTextTool
# ============================================================================

class TestGrepTextTool:
    def test_grep_simple(self):
        ws = make_workspace()
        tool = GrepTextTool()
        result = run_async(tool.execute({"pattern": "def divide"}, ws))
        assert result.success
        assert "def divide" in result.output

    def test_grep_regex(self):
        ws = make_workspace()
        tool = GrepTextTool()
        result = run_async(tool.execute({"pattern": r"def \w+"}, ws))
        assert result.success
        assert result.structured["count"] >= 2

    def test_grep_no_match(self):
        ws = make_workspace()
        tool = GrepTextTool()
        result = run_async(tool.execute({"pattern": "nonexistent_function_xyz"}, ws))
        assert result.success
        assert "No matches" in result.output

    def test_grep_with_context(self):
        ws = make_workspace()
        tool = GrepTextTool()
        result = run_async(tool.execute({"pattern": "def divide", "context_lines": 2}, ws))
        assert result.success
        # Should show surrounding lines
        assert "def add" in result.output or "Raises" in result.output or "ValueError" in result.output


# ============================================================================
# Test GrepASTTool
# ============================================================================

class TestGrepASTTool:
    def test_find_function_defs(self):
        ws = make_workspace()
        tool = GrepASTTool()
        result = run_async(tool.execute({"query": "function_def"}, ws))
        assert result.success
        assert "add" in result.output
        assert "divide" in result.output

    def test_find_class_defs(self):
        ws = make_workspace()
        tool = GrepASTTool()
        result = run_async(tool.execute({"query": "class_def"}, ws))
        assert result.success
        assert "Calculator" in result.output

    def test_find_function_calls(self):
        ws = make_workspace()
        tool = GrepASTTool()
        result = run_async(tool.execute({"query": "function_call", "name": "add"}, ws))
        assert result.success

    def test_find_imports(self):
        ws = make_workspace()
        tool = GrepASTTool()
        result = run_async(tool.execute({"query": "import"}, ws))
        assert result.success

    def test_find_assignments(self):
        ws = make_workspace()
        tool = GrepASTTool()
        result = run_async(tool.execute({"query": "assignment", "name": "TOTAL_COUNT"}, ws))
        assert result.success
        assert "TOTAL_COUNT" in result.output


# ============================================================================
# Test FindSymbolTool
# ============================================================================

class TestFindSymbolTool:
    def test_find_function_symbol(self):
        ws = make_workspace()
        tool = FindSymbolTool()
        result = run_async(tool.execute({"name": "divide"}, ws))
        assert result.success
        assert "division" in result.output.lower() or "Definitions" in result.output

    def test_find_class_symbol(self):
        ws = make_workspace()
        tool = FindSymbolTool()
        result = run_async(tool.execute({"name": "Calculator", "kind": "class"}, ws))
        assert result.success
        assert "Calculator" in result.output

    def test_find_nonexistent(self):
        ws = make_workspace()
        tool = FindSymbolTool()
        result = run_async(tool.execute({"name": "nonexistent_symbol_xyz"}, ws))
        assert result.success
        assert "not found" in result.output


# ============================================================================
# Test ShellRunTool
# ============================================================================

class TestShellRunTool:
    def test_run_echo(self):
        tool = ShellRunTool()
        result = run_async(tool.execute({"command": "echo hello world"}, "/tmp"))
        assert result.success
        assert "hello world" in result.output

    def test_run_python_version(self):
        tool = ShellRunTool()
        result = run_async(tool.execute({"command": "python --version"}, "/tmp"))
        assert result.success
        assert "Python" in result.output

    def test_run_invalid_command(self):
        tool = ShellRunTool()
        result = run_async(tool.execute({"command": "nonexistent_cmd_xyz_123"}, "/tmp"))
        assert result.success is False

    def test_dangerous_blocked(self):
        tool = ShellRunTool()
        result = run_async(tool.execute({"command": "sudo rm -rf /"}, "/tmp"))
        assert result.success is False
        assert "Blocked" in result.error


# ============================================================================
# Test TestRunTool
# ============================================================================

class TestTestRunTool:
    def test_run_passing_tests(self):
        ws = make_workspace()
        tool = TestRunTool()
        result = run_async(tool.execute({"paths": ["tests/"]}, ws))
        assert result.success
        assert result.structured["passed"] == 4
        assert result.structured["failed"] == 0

    def test_run_with_filter(self):
        ws = make_workspace()
        tool = TestRunTool()
        result = run_async(tool.execute({"paths": ["tests/"], "filter": "test_add"}, ws))
        assert result.success
        assert result.structured["passed"] == 1


# ============================================================================
# Test LintCheckTool
# ============================================================================

class TestLintCheckTool:
    def test_syntax_check_pass(self):
        ws = make_workspace()
        tool = LintCheckTool()
        result = run_async(tool.execute({"path": "src/calc.py", "checkers": "syntax"}, ws))
        assert result.success

    def test_syntax_check_fail(self):
        ws = make_workspace()
        # Create a file with syntax error
        bad_file = os.path.join(ws, "bad.py")
        with open(bad_file, "w") as f:
            f.write("def broken(:\n    pass\n")
        tool = LintCheckTool()
        result = run_async(tool.execute({"path": "bad.py", "checkers": "syntax"}, ws))
        assert result.success is False


# ============================================================================
# Test Git Tools
# ============================================================================

class TestGitTools:
    def test_git_diff(self):
        ws = make_workspace()
        # Initialize git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=ws, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"],
                      cwd=ws, capture_output=True)
        # Make a change
        with open(os.path.join(ws, "src/calc.py"), "a") as f:
            f.write("\n# comment\n")

        tool = GitDiffTool()
        result = run_async(tool.execute({}, ws))
        assert result.success
        assert result.structured["changed"] is True

    def test_git_log(self):
        ws = make_workspace()
        # Already initialized from git_diff test or re-init
        if not os.path.exists(os.path.join(ws, ".git")):
            import subprocess
            subprocess.run(["git", "init"], cwd=ws, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"],
                          cwd=ws, capture_output=True)

        tool = GitLogTool()
        result = run_async(tool.execute({"max_count": 5}, ws))
        assert result.success
        assert "init" in result.output.lower()

    def test_git_blame(self):
        ws = make_workspace()
        if not os.path.exists(os.path.join(ws, ".git")):
            import subprocess
            subprocess.run(["git", "init"], cwd=ws, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init", "--no-gpg-sign"],
                          cwd=ws, capture_output=True)

        tool = GitBlameTool()
        result = run_async(tool.execute({"path": "src/calc.py", "start_line": 1, "end_line": 5}, ws))
        assert result.success


# ============================================================================
# Test Information Tools
# ============================================================================

class TestInfoTools:
    def test_read_docs_find_readme(self):
        ws = make_workspace()
        with open(os.path.join(ws, "README.md"), "w") as f:
            f.write("# Test Project\n\nThis is a test.")
        tool = ReadDocsTool()
        result = run_async(tool.execute({"path": "README.md"}, ws))
        assert result.success
        assert "# Test Project" in result.output

    def test_read_docs_directory(self):
        ws = make_workspace()
        with open(os.path.join(ws, "README.md"), "w") as f:
            f.write("# Test")
        with open(os.path.join(ws, "CHANGELOG.md"), "w") as f:
            f.write("# Changes")
        tool = ReadDocsTool()
        result = run_async(tool.execute({"path": "."}, ws))
        assert result.success
        assert "README.md" in result.output or "CHANGELOG.md" in result.output


# ============================================================================
# Test SubmitTool
# ============================================================================

class TestSubmitTool:
    def test_submit(self):
        tool = SubmitTool()
        result = run_async(tool.execute({"summary": "Fixed the bug"}, "/tmp"))
        assert result.success
        assert result.structured["submitted"] is True
        assert "Fixed the bug" in result.output


# ============================================================================
# Test ToolResult
# ============================================================================

class TestToolResult:
    def test_success_message(self):
        tr = ToolResult(True, "File edited successfully")
        assert "File edited" in tr.to_message()

    def test_error_message(self):
        tr = ToolResult(False, error="File not found")
        assert "ERROR" in tr.to_message()
        assert "File not found" in tr.to_message()
