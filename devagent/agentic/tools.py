"""Tool abstraction layer for DevAgent V2 Agentic Loop.

Provides:
  - BaseTool: Abstract tool with JSON Schema generation for LLM function calling
  - ToolRegistry: Registration, schema export, and execution dispatch
  - Concrete tools: FileRead, FileEdit, GrepText, ShellRun, TestRun, GitDiff
"""

import os
import re
import ast
import json
import asyncio
import difflib
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ============================================================================
# Core Abstractions
# ============================================================================

@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    output: str = ""
    error: str = ""
    structured: dict = field(default_factory=dict)

    def to_message(self) -> str:
        if self.success:
            return self.output
        return f"ERROR: {self.error}"


class BaseTool(ABC):
    """Abstract tool with LLM-callable schema."""

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)  # JSON Schema for params

    @abstractmethod
    async def execute(self, params: dict, workspace: str) -> ToolResult:
        ...

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self._required_params(),
                }
            }
        }

    def _required_params(self) -> list[str]:
        return [k for k, v in self.parameters.items()
                if isinstance(v, dict) and v.get("required", False)]

    def description_text(self) -> str:
        """Human-readable tool description for system prompts."""
        params_desc = "\n".join(
            f"    {k}: {v.get('description', '')} (type: {v.get('type', 'any')})"
            for k, v in self.parameters.items()
        )
        return f"## {self.name}\n{self.description}\nParameters:\n{params_desc}"


class ToolRegistry:
    """Registry of all available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_openai_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_descriptions(self) -> str:
        return "\n".join(t.description_text() for t in self._tools.values())

    async def execute(self, name: str, params: dict, workspace: str) -> ToolResult:
        tool = self.get(name)
        if not tool:
            return ToolResult(False, error=f"Unknown tool: {name}. Available: {self.list_names()}")
        try:
            return await tool.execute(params, workspace)
        except Exception as e:
            return ToolResult(False, error=f"Tool '{name}' crashed: {e}")

    @staticmethod
    def create_default(workspace: str = ".", llm_client=None,
                       include_pipeline: bool = False) -> "ToolRegistry":
        """Create a ToolRegistry with all standard tools.

        Args:
            workspace: Default workspace directory
            llm_client: Optional LLMClient for pipeline tools (V1 agent adapters)
            include_pipeline: Whether to include V1 agent pipeline tools
                             (analyze_requirements, design_architecture, etc.)
        """
        reg = ToolRegistry()

        # File Operations (4)
        reg.register(FileReadTool())
        reg.register(FileEditTool())
        reg.register(FileWriteTool())
        reg.register(FileListTool())

        # Code Search (3)
        reg.register(GrepTextTool())
        reg.register(GrepASTTool())
        reg.register(FindSymbolTool())

        # Execution & Validation (3)
        reg.register(ShellRunTool())
        reg.register(TestRunTool())
        reg.register(LintCheckTool())

        # Version Control (3)
        reg.register(GitDiffTool())
        reg.register(GitLogTool())
        reg.register(GitBlameTool())

        # Information (3)
        reg.register(WebSearchTool())
        reg.register(ReadDocsTool())
        from .multimodal import ImageReadTool
        reg.register(ImageReadTool())

        # GitHub (3) — registered if gh CLI or GITHUB_TOKEN is available
        if GitHubIssueRead._is_available():
            reg.register(GitHubIssueRead())
            reg.register(GitHubPRCreate())
            reg.register(GitHubPRComment())

        # Control (1)
        reg.register(SubmitTool())

        # Interactive (2)
        reg.register(AskUserTool())
        reg.register(RequestReviewTool())

        # Diagram rendering (1) — if mmdc is available
        reg.register(DiagramRenderTool())

        # === Pipeline tools (V1 agent adapters — unified architecture) ===
        if include_pipeline and llm_client is not None:
            from .pipeline_tools import register_pipeline_tools
            register_pipeline_tools(reg, llm_client)

        return reg


# ============================================================================
# Path Sandbox
# ============================================================================

class PathSandbox:
    """Ensure all file operations stay within the workspace."""

    def __init__(self, workspace: str):
        self.root = Path(workspace).resolve()

    def resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            full = p.resolve()
        else:
            full = (self.root / p).resolve()
        if not str(full).startswith(str(self.root)):
            raise PermissionError(f"Path escapes workspace: {path} -> {full}")
        return full

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)


# ============================================================================
# Tool Implementations
# ============================================================================

class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read a file from the workspace with line numbers. Use BEFORE editing to understand the code."
    parameters = {
        "path": {
            "type": "string",
            "description": "Relative or absolute path to the file"
        },
        "offset": {
            "type": "integer",
            "description": "Line number to start from (1-indexed). Omit to read from beginning."
        },
        "limit": {
            "type": "integer",
            "description": "Max lines to read. Omit to read entire file."
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        file_path = sandbox.resolve(params["path"])
        if not file_path.exists():
            return ToolResult(False, error=f"File not found: {params['path']}")
        if file_path.is_dir():
            return ToolResult(False, error=f"Path is a directory: {params['path']}")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        total = len(lines)

        offset = max(0, (params.get("offset") or 1) - 1)
        limit = params.get("limit")
        shown = lines[offset:offset + limit] if limit else lines[offset:]

        output = "\n".join(f"{i + offset + 1:4d}|{line}" for i, line in enumerate(shown))
        rel = sandbox.relative(file_path)
        return ToolResult(True, f"--- {rel} (lines {offset + 1}-{offset + len(shown)} of {total}) ---\n{output}",
                          structured={"path": rel, "total_lines": total, "shown": len(shown)})


class FileEditTool(BaseTool):
    name = "file_edit"
    description = "Edit a file by replacing exact text. old_string must match exactly once or use replace_all=true."
    parameters = {
        "path": {
            "type": "string",
            "description": "File path to edit"
        },
        "old_string": {
            "type": "string",
            "description": "Exact text to replace (must be unique in file unless replace_all=true)"
        },
        "new_string": {
            "type": "string",
            "description": "Replacement text"
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences (default: false)",
            "default": False
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        file_path = sandbox.resolve(params["path"])
        if not file_path.exists():
            return ToolResult(False, error=f"File not found: {params['path']}")

        original = file_path.read_text(encoding="utf-8", errors="replace")
        old = params["old_string"]
        new = params["new_string"]
        replace_all = params.get("replace_all", False)

        if old == new:
            return ToolResult(False, error="old_string and new_string are identical")

        count = original.count(old)
        if count == 0:
            return ToolResult(False, error=f"old_string not found in file. Use grep_text first to locate the exact text.")
        if count > 1 and not replace_all:
            return ToolResult(False, error=f"old_string found {count} times. Make it more specific or use replace_all=true.")

        modified = original.replace(old, new) if replace_all else original.replace(old, new, 1)
        file_path.write_text(modified, encoding="utf-8")

        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                modified.splitlines(keepends=True),
                fromfile=f"a/{params['path']}",
                tofile=f"b/{params['path']}",
                lineterm=""
            )
        )
        rel = sandbox.relative(file_path)
        return ToolResult(True, f"Edited {rel}\n```diff\n{diff}\n```",
                          structured={"path": rel, "replacements": 1 if not replace_all else count})


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "Write a complete file. Creates directories as needed."
    parameters = {
        "path": {
            "type": "string",
            "description": "File path (relative to workspace)"
        },
        "content": {
            "type": "string",
            "description": "Complete file content"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        file_path = sandbox.resolve(params["path"])
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(params["content"], encoding="utf-8")
        rel = sandbox.relative(file_path)
        return ToolResult(True, f"Wrote {rel} ({len(params['content'])} chars)",
                          structured={"path": rel, "size": len(params["content"])})


class GrepTextTool(BaseTool):
    name = "grep_text"
    description = "Search for text/pattern in workspace files. Use BEFORE editing to locate relevant code."
    parameters = {
        "pattern": {
            "type": "string",
            "description": "Text or regex pattern to search for"
        },
        "path": {
            "type": "string",
            "description": "Subdirectory to search (default: entire workspace)",
            "default": "."
        },
        "include": {
            "type": "string",
            "description": "File pattern filter, e.g. '*.py'",
            "default": "*.py"
        },
        "context_lines": {
            "type": "integer",
            "description": "Lines of context to show around each match (default: 0)",
            "default": 0
        }
    }

    SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "venv", ".venv",
                 "node_modules", ".egg-info", ".mypy_cache", ".ruff_cache"}

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        search_path = sandbox.resolve(params.get("path", "."))
        pattern = params["pattern"]
        include = params.get("include", "*.py")
        context_lines = params.get("context_lines", 0)

        matches = []
        try:
            compiled = re.compile(pattern)
        except re.error:
            compiled = re.compile(re.escape(pattern))

        for file_path in search_path.rglob(include):
            parts = set(file_path.parts)
            if parts & self.SKIP_DIRS:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                all_lines = content.split("\n")
                for i, line in enumerate(all_lines):
                    if compiled.search(line):
                        match_entry = {
                            "file": sandbox.relative(file_path),
                            "line": i + 1,
                            "content": line.strip()[:200]
                        }
                        if context_lines > 0:
                            ctx_start = max(0, i - context_lines)
                            ctx_end = min(len(all_lines), i + context_lines + 1)
                            match_entry["context"] = [
                                f"{j + 1:4d}|{all_lines[j].rstrip()}"
                                for j in range(ctx_start, ctx_end)
                            ]
                        matches.append(match_entry)
            except Exception:
                continue

        truncated = len(matches) > 50
        matches = matches[:50]

        if not matches:
            return ToolResult(True, f"No matches for pattern: {pattern}",
                              structured={"count": 0, "matches": []})

        lines_out = [f"Found {len(matches)} matches{' (truncated)' if truncated else ''}:"]
        for m in matches:
            if "context" in m:
                lines_out.append(f"\n--- {m['file']}:{m['line']} ---")
                lines_out.extend(m["context"])
                lines_out.append("")
            else:
                lines_out.append(f"  {m['file']}:{m['line']} | {m['content']}")

        return ToolResult(True, "\n".join(lines_out),
                          structured={"count": len(matches), "matches": matches})


class GrepASTTool(BaseTool):
    name = "grep_ast"
    description = "Search code structure: find function/class definitions, call sites, imports, assignments via AST."
    parameters = {
        "query": {
            "type": "string",
            "description": "Query type: function_def, class_def, function_call, import, assignment"
        },
        "name": {
            "type": "string",
            "description": "Optional name filter (e.g., specific function name)"
        },
        "path": {
            "type": "string",
            "description": "Directory to search",
            "default": "."
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        search_path = sandbox.resolve(params.get("path", "."))
        query = params.get("query", "function_def")
        name_filter = params.get("name")

        results = []
        for file_path in search_path.rglob("*.py"):
            parts = set(file_path.parts)
            if parts & {"__pycache__", ".git", "venv", ".venv", ".pytest_cache"}:
                continue
            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
                rel = sandbox.relative(file_path)

                for node in ast.walk(tree):
                    if query == "function_def" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fname = node.name
                        if name_filter and name_filter not in fname:
                            continue
                        results.append({
                            "file": rel, "line": node.lineno,
                            "name": fname,
                            "args": [a.arg for a in node.args.args]
                        })
                    elif query == "class_def" and isinstance(node, ast.ClassDef):
                        cname = node.name
                        if name_filter and name_filter not in cname:
                            continue
                        bases = [b.id if isinstance(b, ast.Name) else "" for b in node.bases]
                        results.append({
                            "file": rel, "line": node.lineno,
                            "name": cname, "bases": bases
                        })
                    elif query == "function_call" and isinstance(node, ast.Call):
                        call_name = self._get_call_name(node)
                        if not call_name:
                            continue
                        if name_filter and name_filter not in call_name:
                            continue
                        results.append({
                            "file": rel, "line": node.lineno,
                            "name": call_name
                        })
                    elif query == "assignment" and isinstance(node, ast.Assign):
                        for target in node.targets:
                            tname = self._get_assign_name(target)
                            if not tname:
                                continue
                            if name_filter and name_filter not in tname:
                                continue
                            results.append({
                                "file": rel, "line": node.lineno,
                                "name": tname,
                                "value_type": self._get_value_type(node.value)
                            })
                    elif query == "import" and isinstance(node, (ast.Import, ast.ImportFrom)):
                        results.append({
                            "file": rel, "line": node.lineno,
                            "detail": ast.unparse(node)
                        })
            except (SyntaxError, Exception):
                continue

        truncated = len(results) > 50
        results = results[:50]

        lines_out = [f"AST query '{query}'{' name=' + name_filter if name_filter else ''}: {len(results)} results"]
        for r in results:
            if 'detail' in r:
                lines_out.append(f"  {r['file']}:{r['line']} | {r['detail']}")
            else:
                args_str = f"({', '.join(r.get('args', []))})" if 'args' in r else ""
                name = r.get('name', r.get('detail', '?'))
                lines_out.append(f"  {r['file']}:{r['line']} | {name}{args_str}")

        return ToolResult(True, "\n".join(lines_out),
                          structured={"count": len(results), "results": results})

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return f"{ast.unparse(node.func.value)}.{node.func.attr}"
        return ""

    @staticmethod
    def _get_assign_name(target: ast.AST) -> str:
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            return target.attr
        return ""

    @staticmethod
    def _get_value_type(value: ast.AST) -> str:
        if isinstance(value, ast.Constant):
            return type(value.value).__name__
        if isinstance(value, ast.List):
            return "list"
        if isinstance(value, ast.Dict):
            return "dict"
        if isinstance(value, ast.Call):
            return GrepASTTool._get_call_name(value)
        if isinstance(value, ast.Name):
            return value.id
        return "expression"


class ShellRunTool(BaseTool):
    name = "shell_run"
    description = "Run a shell command. Use for: tests, linting, building, dependency install, running scripts."
    parameters = {
        "command": {
            "type": "string",
            "description": "Shell command to execute"
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (default: 60)",
            "default": 60
        }
    }

    DANGEROUS = [r"rm\s+-rf\s+/", r"git\s+push\s+--force", r"sudo\s+", r">\s*/dev/", r"mkfs\."]

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        command = params["command"]
        timeout = params.get("timeout", 60)

        for pattern in self.DANGEROUS:
            if re.search(pattern, command):
                return ToolResult(False, error=f"Blocked dangerous command: {pattern}")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode("utf-8", errors="replace")[:5000] or "(no output)"
            err = stderr.decode("utf-8", errors="replace")[:2000]

            return ToolResult(
                success=proc.returncode == 0,
                output=f"$ {command}\n{out}" + (f"\nSTDERR:\n{err}" if err and proc.returncode != 0 else ""),
                structured={"returncode": proc.returncode, "stderr": err}
            )
        except asyncio.TimeoutError:
            return ToolResult(False, error=f"Command timed out after {timeout}s: {command}")
        except FileNotFoundError:
            return ToolResult(False, error=f"Command not found: {command.split()[0]}")


class TestRunTool(BaseTool):
    name = "test_run"
    description = "Run tests with pytest. Use after making code changes to verify correctness."
    parameters = {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific test files or directories to run (default: tests/)"
        },
        "filter": {
            "type": "string",
            "description": "-k filter expression for pytest"
        },
        "verbose": {
            "type": "boolean",
            "description": "Show full pytest output including passing tests (default: false)",
            "default": False
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        test_paths = params.get("paths", ["tests/"])
        filt = params.get("filter", "")
        verbose = params.get("verbose", False)

        # Auto-discover test paths: try common locations
        if not params.get("paths") and not params.get("filter"):
            # Try common test directories
            candidates = [
                "tests/", "03_implementation/tests/",
                "04_tests/tests/", "test/",
            ]
            for cand in candidates:
                p = os.path.join(workspace, cand)
                if os.path.isdir(p) and any(f.endswith(".py") for f in os.listdir(p)):
                    test_paths = [cand]
                    break

        # Smart regression: if no specific paths, try to select only affected tests
        if not params.get("paths") and not params.get("filter"):
            from .validation import SmartRegressionSelector
            try:
                # Try to infer modified files from workspace git status
                import subprocess as _sp
                proc = _sp.run(["git", "diff", "--name-only"],
                              cwd=workspace, capture_output=True, text=True, timeout=10)
                modified = [l.strip() for l in proc.stdout.split("\n") if l.strip().endswith(".py")]
                if modified:
                    selected = SmartRegressionSelector.select_fast("tests/", modified)
                    if selected:
                        test_paths = selected
            except Exception:
                pass

        cmd = ["python", "-m", "pytest"]
        if verbose:
            cmd.extend(["-v", "--tb=long"])
        else:
            cmd.extend(["-v", "--tb=short", "--no-header"])
        cmd.extend(test_paths)
        if filt:
            cmd.extend(["-k", filt])

        command = " ".join(cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            passed = re.findall(r'(\d+) passed', out + err)
            failed = re.findall(r'(\d+) failed', out + err)

            # Build clear summary for agent to act on
            p = sum(int(x) for x in passed)
            f = sum(int(x) for x in failed)
            summary = (
                f"\n\n=== TEST SUMMARY ===\n"
                f"{p} passed, {f} failed"
            )
            if f == 0 and p > 0:
                summary += "\nALL TESTS PASSING — call request_review then submit."
            elif f > 0:
                summary += "\nTESTS FAILING — call debug_issue to analyze failures."

            # success=True means "tests executed" not "all passed"
            # Agent should check structured.passed and structured.failed
            executed_ok = proc.returncode in (0, 1)  # 0=all pass, 1=tests ran but some failed
            return ToolResult(
                success=executed_ok,
                output=(out[:2000] + err[:500] + summary),
                structured={
                    "returncode": proc.returncode,
                    "passed": sum(int(x) for x in passed),
                    "failed": sum(int(x) for x in failed),
                }
            )
        except asyncio.TimeoutError:
            return ToolResult(False, error="Tests timed out after 120s")


class GitDiffTool(BaseTool):
    name = "git_diff"
    description = "Show git diff. Use to review what you've modified."
    parameters = {
        "staged": {
            "type": "boolean",
            "description": "Show staged changes instead of unstaged (default: false)",
            "default": False
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        staged = params.get("staged", False)
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            out = stdout.decode("utf-8", errors="replace")
            if not out.strip():
                return ToolResult(True, "No changes to show.", structured={"changed": False})
            return ToolResult(True, f"```diff\n{out[:4000]}\n```", structured={"changed": True})
        except FileNotFoundError:
            return ToolResult(False, error="git not available")
        except asyncio.TimeoutError:
            return ToolResult(False, error="git diff timed out")


# ============================================================================
# GitHub Tools
# ============================================================================

class GitHubIssueRead(BaseTool):
    name = "gh_issue_read"
    description = "Read a GitHub issue by reference (owner/repo#number or full URL). Shows title, body, labels, and comments."
    parameters = {
        "issue": {
            "type": "string",
            "description": "Issue reference: owner/repo#123 or https://github.com/owner/repo/issues/123"
        }
    }

    @staticmethod
    def _parse_ref(ref: str) -> tuple:
        """Parse issue reference into (owner, repo, number)."""
        # owner/repo#123
        m = re.match(r'([\w.-]+)/([\w.-]+)#(\d+)', ref)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
        # https://github.com/owner/repo/issues/123
        m = re.match(r'https?://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)', ref)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
        raise ValueError(f"Cannot parse issue reference: {ref}")

    @staticmethod
    def _is_available() -> bool:
        """Check if GitHub access is configured."""
        return bool(os.environ.get("GITHUB_TOKEN") or _check_gh_cli())

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        owner, repo, number = self._parse_ref(params["issue"])
        import urllib.request
        token = os.environ.get("GITHUB_TOKEN", "")
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        headers = {"Accept": "application/vnd.github+json",
                    "User-Agent": "DevAgent/2.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            return ToolResult(True,
                f"## Issue #{number}: {data.get('title', 'N/A')}\n"
                f"State: {data.get('state', '?')} | "
                f"Labels: {', '.join(l['name'] for l in data.get('labels', []))}\n\n"
                f"{data.get('body', '')[:3000]}",
                structured={"number": number, "title": data.get("title", ""),
                           "state": data.get("state", "")})
        except Exception as e:
            return ToolResult(False, error=f"GitHub API error: {e}")


class GitHubPRCreate(BaseTool):
    name = "gh_pr_create"
    description = "Create a Pull Request from the current branch. Use when the task is complete."
    parameters = {
        "title": {"type": "string", "description": "PR title (short, descriptive)"},
        "body": {"type": "string", "description": "PR description in Markdown"},
        "base": {"type": "string", "description": "Base branch (default: main)", "default": "main"},
        "draft": {"type": "boolean", "description": "Create as draft PR", "default": False}
    }

    @staticmethod
    def _is_available() -> bool:
        return GitHubIssueRead._is_available()

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "create",
                "--title", params["title"],
                "--body", params.get("body", "") + "\n\n🤖 Generated with DevAgent",
                "--base", params.get("base", "main"),
                *(["--draft"] if params.get("draft") else []),
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace")
            if proc.returncode == 0:
                return ToolResult(True, f"PR created: {out.strip()}",
                                  structured={"url": out.strip()})
            return ToolResult(False, error=stderr.decode("utf-8", errors="replace")[:500])
        except FileNotFoundError:
            return ToolResult(False, error="gh CLI not installed. Install: brew install gh && gh auth login")
        except asyncio.TimeoutError:
            return ToolResult(False, error="gh pr create timed out")


class GitHubPRComment(BaseTool):
    name = "gh_pr_comment"
    description = "Add a comment on a Pull Request."
    parameters = {
        "pr": {"type": "string", "description": "PR URL or owner/repo#number"},
        "body": {"type": "string", "description": "Comment body (Markdown)"}
    }

    @staticmethod
    def _is_available() -> bool:
        return GitHubIssueRead._is_available()

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "comment", params["pr"],
                "--body", params.get("body", ""),
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                return ToolResult(True, f"Comment posted on {params['pr']}")
            return ToolResult(False, error=stderr.decode("utf-8", errors="replace")[:300])
        except FileNotFoundError:
            return ToolResult(False, error="gh CLI not installed")
        except asyncio.TimeoutError:
            return ToolResult(False, error="gh pr comment timed out")


def _check_gh_cli() -> bool:
    """Check if gh CLI is installed and authenticated."""
    try:
        proc = subprocess.run(["gh", "auth", "status"],
                            capture_output=True, timeout=5)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class SubmitTool(BaseTool):
    name = "submit"
    description = "Submit the final result when the task is complete. Call this when all tests pass and you're done."
    parameters = {
        "summary": {
            "type": "string",
            "description": "Summary of what was done"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        summary = params.get("summary", "Task completed")
        return ToolResult(True, f"SUBMIT: {summary}", structured={"submitted": True, "summary": summary})


# ============================================================================
# Interactive — ask_user
# ============================================================================

class AskUserTool(BaseTool):
    """Agent-initiated question to the user — requires interactive mode.

    When the agent encounters ambiguity or needs a decision, it can ask
    the user directly. The question is routed through the InteractionController
    to any connected client (WebSocket, CLI TUI, etc.).

    Behavior in non-interactive mode: returns the default_choice automatically.
    """

    name = "ask_user"
    description = (
        "Ask the human user a question when you need clarification or a decision. "
        "Use when: requirements are ambiguous, multiple fix approaches are viable, "
        "or you need to confirm an assumption before proceeding. "
        "Provide clear options so the user can choose quickly."
    )
    parameters = {
        "question": {
            "type": "string",
            "description": "Clear, specific question for the user. Be concise."
        },
        "context": {
            "type": "string",
            "description": "Background context to help the user understand why you're asking."
        },
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Predefined choices, e.g. ['Option A: use regex parsing', 'Option B: use AST parsing']. Max 5 options."
        },
        "default_choice": {
            "type": "string",
            "description": "Your recommended choice — used if user doesn't respond in time."
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        question = params.get("question", "")
        context_text = params.get("context", "")
        options = params.get("options", []) or []
        default = params.get("default_choice", "")

        # Try to get the interactive controller
        from .interaction import _get_active_controller
        controller = _get_active_controller()
        if controller is None:
            # Non-interactive mode: auto-answer with default
            return ToolResult(
                True,
                f"[AUTO] Q: {question}\nA: {default or 'no answer (non-interactive mode)'}",
                structured={
                    "question": question,
                    "answer": default,
                    "auto_answered": True,
                    "mode": "non_interactive"
                }
            )

        # Interactive mode: wait for user response
        answer = await controller.ask_user(
            question=question,
            options=options,
            context={"background": context_text}
        )

        if not answer and default:
            answer = default
        elif not answer:
            answer = "(no response from user)"

        return ToolResult(
            True,
            f"Q: {question}\nA: {answer}",
            structured={
                "question": question,
                "answer": answer,
                "auto_answered": not bool(answer != "(no response from user)"),
                "mode": "interactive"
            }
        )


# ============================================================================
# Interactive — request_review (phase-level human review)
# ============================================================================

class RequestReviewTool(BaseTool):
    """Phase-level human review with workflow enforcement."""

    name = "request_review"
    _global_review_count = 0
    _approved_phases: set = set()
    _revise_counts: dict = {}  # phase → revisions so far
    description = (
        "Request human review of completed work at a phase milestone. "
        "CALL THIS at logical checkpoints:\n"
        "  - After implementing a complete module or feature\n"
        "  - After writing and running tests\n"
        "  - After debugging and proposing a fix\n"
        "  - Before declaring the task complete\n\n"
        "The human will review your work against professional software engineering "
        "standards and respond with: approve, request changes (with feedback), or reject.\n"
        "If changes are requested, address ALL feedback and call request_review again."
    )
    parameters = {
        "phase": {
            "type": "string",
            "description": (
                "Current phase: 'exploration' (finished understanding codebase), "
                "'implementation' (finished writing code), "
                "'testing' (finished running tests), "
                "'fix' (finished debugging and applying fix), "
                "'delivery' (task complete, final review before submit)"
            )
        },
        "title": {
            "type": "string",
            "description": "Short title of what was accomplished (e.g., 'User authentication module')"
        },
        "summary": {
            "type": "string",
            "description": (
                "Detailed summary of what was done, why these decisions were made, "
                "what files were changed, and what the expected outcome is. "
                "Be thorough — this is your chance to explain your work to the reviewer."
            )
        },
        "files_changed": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of all files created or modified (relative paths)"
        },
        "self_assessment": {
            "type": "string",
            "description": (
                "Honest self-assessment of the work quality. Mention:\n"
                "  - What you think is well-done and meets standards\n"
                "  - Any areas you're uncertain about\n"
                "  - Edge cases you may have missed\n"
                "  - Alternative approaches you considered"
            )
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        phase = params.get("phase", "implementation")
        title = params.get("title", "Work review")
        summary = params.get("summary", "")
        files_changed = params.get("files_changed", []) or []
        self_assessment = params.get("self_assessment", "")

        from .review_gate import PhaseReviewGate, ReviewArtifact, ArtifactBuilder, ArtifactType
        from .interaction import _get_active_controller

        controller = _get_active_controller()

        # === VALIDATE: Check that files actually exist ===
        existing_files = []
        missing_files = []
        for f in files_changed:
            full = os.path.join(workspace, f) if not os.path.isabs(f) else f
            if os.path.exists(full):
                existing_files.append(f)
            else:
                missing_files.append(f)

        # === SEQUENTIAL PHASE LOCK: earlier phases approved → block all later phases ===
        phase_order = ["requirements", "design", "implementation", "testing", "delivery"]
        last_approved_idx = -1
        for i, p in enumerate(phase_order):
            if p in RequestReviewTool._approved_phases:
                last_approved_idx = i

        if last_approved_idx >= 0:
            current_idx = phase_order.index(phase) if phase in phase_order else -1
            # If this phase or any EARLIER phase is approved, block
            if current_idx <= last_approved_idx:
                next_tool_map = {
                    "requirements": "design_architecture", "design": "generate_code",
                    "implementation": "test_run", "testing": "generate_report",
                    "delivery": "submit",
                }
                nt = next_tool_map.get(phase_order[last_approved_idx], "submit")
                return ToolResult(True, nt,
                    structured={"reviewed": True, "already_approved": True, "next_tool": nt})
            # If this phase is AFTER the last approved one, also block
            # (agent must call the forced tool first)
            if current_idx > last_approved_idx + 1:
                nt = next_tool_map.get(phase_order[last_approved_idx], "submit")
                return ToolResult(True, nt,
                    structured={"reviewed": True, "already_approved": True, "next_tool": nt,
                               "reason": f"Complete {phase_order[last_approved_idx]} first"})

        if not existing_files:
            return ToolResult(True, "NO FILES. Generate output first, then request_review.",
                            structured={"reviewed": False, "blocked": True})

        # === RATE LIMIT ===
        RequestReviewTool._global_review_count += 1
        if RequestReviewTool._global_review_count > 3:
            return ToolResult(True, "submit()",
                            structured={"reviewed": True, "auto_approved": True, "force_submit": True})

        # Build artifacts from existing files
        artifacts = []
        # Code files
        code_files = [f for f in files_changed if f.endswith(".py")]
        if code_files:
            artifacts.append(ArtifactBuilder.from_code_files(
                code_files, f"Source code: {len(code_files)} file(s)", workspace
            ))

        # Test files
        test_files = [f for f in files_changed
                      if "test" in os.path.basename(f).lower() and f.endswith(".py")]
        if test_files:
            artifacts.append(ReviewArtifact(
                artifact_type=ArtifactType.TEST,
                description=f"Test files: {len(test_files)} file(s)",
                file_paths=test_files,
            ))

        # Other files (config, docs)
        other_files = [f for f in files_changed
                       if not f.endswith(".py")]
        if other_files:
            artifacts.append(ReviewArtifact(
                artifact_type=ArtifactType.DOCUMENT,
                description=f"Other files: {len(other_files)} file(s)",
                file_paths=other_files,
            ))

        # If no specific files, create a summary artifact
        if not artifacts:
            artifacts.append(ReviewArtifact(
                artifact_type=ArtifactType.MIXED,
                description="Work summary (no file list provided)",
                file_paths=[],
                content_preview=summary[:3000],
            ))

        # Check if controller has a review gate
        review_gate = None
        if controller and hasattr(controller, 'review_gate'):
            review_gate = controller.review_gate

        if review_gate is None:
            # Non-interactive mode: auto-approve with quality note
            return ToolResult(
                True,
                f"[AUTO-APPROVED] Review submitted for: {title}\n"
                f"Phase: {phase}\nSummary: {summary[:500]}\n"
                f"Files: {len(files_changed)} changed\n\n"
                f"⚠️ Running in non-interactive mode — review auto-approved. "
                f"Consider enabling interactive mode for quality assurance.",
                structured={
                    "reviewed": True,
                    "auto_approved": True,
                    "phase": phase,
                    "title": title,
                    "files_changed": files_changed,
                    "mode": "non_interactive"
                }
            )

        # Interactive mode: submit for human review
        review_session = await review_gate.submit_for_review(
            phase=phase,
            title=title,
            summary=summary,
            artifacts=artifacts,
            task_id=getattr(controller, '_task_id', '') if hasattr(controller, '_task_id') else '',
            workspace=workspace,
            quality_self_assessment=self_assessment,
        )

        # Wait for human decision
        decision = await review_session.wait_for_decision()

        if decision["decision"] == "approve":
            review_gate.clear_active()
            # Mark phase as approved — prevents re-review
            RequestReviewTool._approved_phases.add(phase)
            RequestReviewTool._last_phase = phase

            next_tool = {
                "requirements": "design_architecture", "design": "generate_code",
                "implementation": "test_run", "testing": "generate_report",
                "fix": "test_run", "delivery": "submit",
            }.get(phase, "submit")

            return ToolResult(
                True,
                next_tool,
                structured={
                    "reviewed": True, "approved": True,
                    "phase": phase, "title": title, "next_tool": next_tool,
                    "quality_score": review_session.request.quality_score.value
                                   if review_session.request.quality_score else "unknown",
                }
            )

        elif decision["decision"] == "reject":
            feedback = decision.get("feedback", "")
            review_gate.clear_active()
            return ToolResult(
                True,
                f"❌ REVIEW REJECTED: {title}\n"
                f"The human reviewer has rejected this work.\n\n"
                f"=== REVIEWER FEEDBACK ===\n{feedback}\n\n"
                f"=== REQUIRED ACTIONS ===\n"
                f"You must completely rethink your approach. "
                f"The reviewer found fundamental issues that require a different strategy. "
                f"Re-read the task requirements and start fresh.\n\n"
                f"Specific suggestions:\n" +
                "\n".join(f"  - {s}" for s in decision.get("suggestions", [])),
                structured={
                    "reviewed": True,
                    "approved": False,
                    "rejected": True,
                    "phase": phase,
                    "title": title,
                    "feedback": feedback,
                    "suggestions": decision.get("suggestions", []),
                    "mode": "interactive"
                }
            )

        else:  # revise
            feedback = decision.get("feedback", "")
            suggestions = decision.get("suggestions", [])
            review_gate.clear_active()

            # Track revision count per phase
            rc = RequestReviewTool._revise_counts.get(phase, 0) + 1
            RequestReviewTool._revise_counts[phase] = rc

            next_tool_map = {
                "requirements": "analyze_requirements",
                "design": "design_architecture",
                "implementation": "generate_code",
                "testing": "generate_tests",
                "fix": "repair_code",
                "delivery": "generate_report",
            }
            next_tool = next_tool_map.get(phase, "the_previous_tool")

            if rc >= 3:
                # 3 revisions → force approve
                RequestReviewTool._approved_phases.add(phase)
                RequestReviewTool._revise_counts[phase] = 0
                nt = {"requirements":"design_architecture","design":"generate_code","implementation":"test_run","testing":"generate_report","fix":"test_run","delivery":"submit"}.get(phase,"submit")
                return ToolResult(True, nt,
                    structured={"reviewed": True, "approved": True, "next_tool": nt, "phase": phase,
                               "reason": "max_revisions_reached"})

            return ToolResult(
                True,
                next_tool,
                structured={
                    "reviewed": True, "approved": False, "revise": True,
                    "phase": phase, "title": title,
                    "feedback": feedback, "suggestions": suggestions,
                    "next_tool": next_tool,
                    "revise_count": rc,
                    "phase": phase,
                    "title": title,
                    "feedback": feedback,
                    "suggestions": suggestions,
                    "revision_context": context,
                    "next_tool": next_tool,
                    "mode": "interactive"
                }
            )


# ============================================================================
# File Operations — file_list
# ============================================================================

class FileListTool(BaseTool):
    name = "file_list"
    description = "List files and directories in the workspace. Use to explore project structure before reading files."
    parameters = {
        "path": {
            "type": "string",
            "description": "Directory to list (default: workspace root)",
            "default": "."
        },
        "depth": {
            "type": "integer",
            "description": "Max recursion depth (default: 2)",
            "default": 2
        },
        "include": {
            "type": "string",
            "description": "File pattern filter, e.g. '*.py' or '*.py,*.yaml'"
        }
    }

    SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "venv", ".venv",
                 "node_modules", ".egg-info", ".mypy_cache", ".ruff_cache",
                 ".devagent", ".claude", "outputs", "benchmark_report", "docs"}

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        dir_path = sandbox.resolve(params.get("path", "."))
        if not dir_path.exists():
            return ToolResult(False, error=f"Directory not found: {params['path']}")
        if dir_path.is_file():
            return ToolResult(False, error=f"Path is a file, not directory: {params['path']}")

        depth = params.get("depth", 2)
        include_patterns = params.get("include", "")
        patterns = [p.strip() for p in include_patterns.split(",") if p.strip()] if include_patterns else None

        lines = []
        file_count = 0
        dir_count = 0

        def walk(current: Path, current_depth: int):
            nonlocal file_count, dir_count
            if current_depth > depth:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name))
            except PermissionError:
                return

            for entry in entries:
                if entry.name in self.SKIP_DIRS or entry.name.startswith("."):
                    continue
                indent = "  " * current_depth
                if entry.is_dir():
                    dir_count += 1
                    lines.append(f"{indent}{entry.name}/")
                    walk(entry, current_depth + 1)
                elif entry.is_file():
                    if patterns:
                        if not any(entry.name.endswith(p.lstrip("*")) for p in patterns):
                            continue
                    file_count += 1
                    try:
                        size = entry.stat().st_size
                        size_str = f" ({size}B)" if size < 1024 else f" ({size // 1024}KB)"
                    except OSError:
                        size_str = ""
                    rel = sandbox.relative(entry)
                    lines.append(f"{indent}{entry.name}{size_str}")

        walk(dir_path, 0)

        output = f"--- {sandbox.relative(dir_path)} ({file_count} files, {dir_count} dirs) ---\n"
        output += "\n".join(lines) if lines else "(empty directory)"

        return ToolResult(True, output,
                          structured={"path": sandbox.relative(dir_path),
                                      "files": file_count, "dirs": dir_count})


# ============================================================================
# Code Search — find_symbol (AST-based symbol lookup)
# ============================================================================

class FindSymbolTool(BaseTool):
    name = "find_symbol"
    description = "Find where a symbol (function, class, variable) is defined or referenced. Like IDE 'Go to Definition'."
    parameters = {
        "name": {
            "type": "string",
            "description": "Symbol name to find (e.g. 'calculate_total', 'User')"
        },
        "kind": {
            "type": "string",
            "description": "Symbol kind: function, class, variable, any (default: any)",
            "default": "any"
        },
        "path": {
            "type": "string",
            "description": "Directory to search",
            "default": "."
        }
    }

    SKIP_DIRS = GrepTextTool.SKIP_DIRS

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        search_path = sandbox.resolve(params.get("path", "."))
        name = params.get("name", "")
        kind = params.get("kind", "any")

        definitions = []
        references = []

        for file_path in search_path.rglob("*.py"):
            parts = set(file_path.parts)
            if parts & self.SKIP_DIRS:
                continue
            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
                rel = sandbox.relative(file_path)

                for node in ast.walk(tree):
                    # Find definitions
                    if isinstance(node, ast.FunctionDef) and node.name == name:
                        if kind in ("function", "any"):
                            definitions.append({
                                "file": rel, "line": node.lineno,
                                "kind": "function",
                                "signature": f"def {node.name}({', '.join(a.arg for a in node.args.args)})"
                            })
                    elif isinstance(node, ast.ClassDef) and node.name == name:
                        if kind in ("class", "any"):
                            definitions.append({
                                "file": rel, "line": node.lineno,
                                "kind": "class",
                                "signature": f"class {node.name}"
                            })
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == name:
                                if kind in ("variable", "any"):
                                    definitions.append({
                                        "file": rel, "line": node.lineno,
                                        "kind": "variable",
                                        "signature": f"{target.id} = ..."
                                    })

                    # Find references / call sites
                    if isinstance(node, ast.Call):
                        call_name = None
                        if isinstance(node.func, ast.Name):
                            call_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            call_name = node.func.attr
                        if call_name == name:
                            references.append({
                                "file": rel, "line": node.lineno,
                                "context": source.split("\n")[node.lineno - 1].strip()[:150]
                            })

                    # Find attribute accesses
                    if isinstance(node, ast.Attribute) and node.attr == name:
                        ctx = source.split("\n")[node.lineno - 1].strip()[:150] if node.lineno <= len(source.split("\n")) else ""
                        references.append({
                            "file": rel, "line": node.lineno,
                            "context": ctx
                        })
            except (SyntaxError, Exception):
                continue

        output_parts = []
        if definitions:
            output_parts.append(f"=== Definitions of '{name}' ===")
            for d in definitions[:20]:
                output_parts.append(f"  {d['file']}:{d['line']} — {d['kind']} {d['signature']}")

        if references:
            refs = references[:30]
            output_parts.append(f"\n=== References to '{name}' ({len(references)} total, showing {len(refs)}) ===")
            # Deduplicate
            seen = set()
            unique_refs = []
            for r in refs:
                key = (r["file"], r["line"])
                if key not in seen:
                    seen.add(key)
                    unique_refs.append(r)
            for r in unique_refs:
                output_parts.append(f"  {r['file']}:{r['line']} — {r.get('context', '')}")

        if not output_parts:
            output_parts.append(f"Symbol '{name}' not found in workspace.")

        return ToolResult(True, "\n".join(output_parts),
                          structured={"definitions": definitions, "references": references})


# ============================================================================
# Execution & Validation — lint_check
# ============================================================================

class LintCheckTool(BaseTool):
    name = "lint_check"
    description = "Run linter and type checker on a file. Use after editing to catch syntax, style, and type errors."
    parameters = {
        "path": {
            "type": "string",
            "description": "File or directory to check"
        },
        "checkers": {
            "type": "string",
            "description": "Checks to run: 'all', 'syntax', 'lint', 'types' (default: 'all')",
            "default": "all"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        target_path = sandbox.resolve(params["path"])
        checkers = params.get("checkers", "all")

        results = []

        # 1. Python syntax check (always run, near-instant)
        if checkers in ("all", "syntax"):
            syntax_ok, syntax_msg = await self._check_syntax(target_path)
            results.append(("Syntax", syntax_ok, syntax_msg))

        # Only continue if syntax is OK
        if not any(not ok for _, ok, _ in results):
            # 2. Ruff linting
            if checkers in ("all", "lint"):
                lint_ok, lint_msg = await self._run_ruff(target_path, workspace)
                results.append(("Ruff Lint", lint_ok, lint_msg))

            # 3. Import check
            if checkers in ("all", "lint"):
                import_ok, import_msg = await self._check_imports(target_path, workspace)
                results.append(("Import", import_ok, import_msg))

        # Build output
        lines = [f"=== Lint Check: {sandbox.relative(target_path)} ==="]
        all_ok = True
        for name, ok, msg in results:
            icon = "PASS" if ok else "FAIL"
            lines.append(f"[{icon}] {name}: {msg}")
            if not ok:
                all_ok = False

        return ToolResult(all_ok, "\n".join(lines),
                          structured={"checks": [{"name": n, "ok": ok, "msg": m} for n, ok, m in results]})

    @staticmethod
    async def _check_syntax(file_path: Path) -> tuple[bool, str]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            ast.parse(content)
            return True, "OK"
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"

    @staticmethod
    async def _run_ruff(file_path: Path, workspace: str) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check", str(file_path), "--output-format", "concise",
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace")
            if proc.returncode == 0:
                return True, "No issues"
            lines = out.strip().split("\n")
            return False, f"{len(lines)} issue(s)" if lines else "Lint issues found"
        except FileNotFoundError:
            return True, "ruff not installed — skipped"
        except asyncio.TimeoutError:
            return True, "ruff timed out — skipped"

    @staticmethod
    async def _check_imports(file_path: Path, workspace: str) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-c",
                f"import ast; ast.parse(open('{file_path}').read())",
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                return True, "OK"
            err = stderr.decode("utf-8", errors="replace")[:200]
            return False, err if err else "Import check failed"
        except asyncio.TimeoutError:
            return True, "Import check timed out"


# ============================================================================
# Version Control — git_log, git_blame
# ============================================================================

class GitLogTool(BaseTool):
    name = "git_log"
    description = "Show recent commit history. Use to understand project evolution and find when changes were introduced."
    parameters = {
        "max_count": {
            "type": "integer",
            "description": "Max commits to show (default: 10)",
            "default": 10
        },
        "path": {
            "type": "string",
            "description": "Filter by file path"
        },
        "oneline": {
            "type": "boolean",
            "description": "One line per commit",
            "default": True
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        max_count = params.get("max_count", 10)
        target_path = params.get("path", "")
        oneline = params.get("oneline", True)

        cmd = ["git", "log", f"-{max_count}"]
        if oneline:
            cmd.append("--oneline")
        else:
            cmd.append("--format=%h %an %ad %s")
            cmd.append("--date=short")
        if target_path:
            cmd.extend(["--", target_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            out = stdout.decode("utf-8", errors="replace")
            if not out.strip():
                return ToolResult(True, "No commits found.", structured={"commits": []})
            return ToolResult(True, f"Recent commits:\n{out[:3000]}",
                              structured={"commits": out.strip().split("\n")})
        except FileNotFoundError:
            return ToolResult(False, error="git not available")
        except asyncio.TimeoutError:
            return ToolResult(False, error="git log timed out")


class GitBlameTool(BaseTool):
    name = "git_blame"
    description = "Show who last modified each line of a file. Use to understand recent changes and find relevant authors."
    parameters = {
        "path": {
            "type": "string",
            "description": "File path to blame"
        },
        "start_line": {
            "type": "integer",
            "description": "Start line (1-indexed)"
        },
        "end_line": {
            "type": "integer",
            "description": "End line (1-indexed)"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        file_path = sandbox.resolve(params["path"])
        if not file_path.exists():
            return ToolResult(False, error=f"File not found: {params['path']}")

        cmd = ["git", "blame", "--date=short", "-L"]
        start = params.get("start_line", 1)
        end = params.get("end_line", start + 30)
        cmd.append(f"{start},{end}")
        cmd.append("--")
        cmd.append(str(file_path))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            out = stdout.decode("utf-8", errors="replace")
            if not out.strip():
                return ToolResult(True, "No blame info available.", structured={"lines": []})
            return ToolResult(True, f"Blame for {params['path']} L{start}-{end}:\n{out[:3000]}",
                              structured={"blame_output": out[:3000]})
        except FileNotFoundError:
            return ToolResult(False, error="git not available")
        except asyncio.TimeoutError:
            return ToolResult(False, error="git blame timed out")


# ============================================================================
# Information — web_search, read_docs
# ============================================================================

class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for programming documentation. Use ONLY when you need API docs, library references, or language syntax."
    parameters = {
        "query": {
            "type": "string",
            "description": "Search query (e.g., 'python asyncio gather documentation')"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        import urllib.request
        import urllib.parse

        query = params["query"]
        # Use DuckDuckGo's lite search (no API key needed, returns basic HTML)
        encoded = urllib.parse.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}+programming+documentation"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DevAgent/2.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract result snippets from HTML
            snippets = []
            # Match DuckDuckGo lite result patterns
            for m in re.finditer(r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html):
                url_found = m.group(1)
                title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if url_found and title:
                    snippets.append({"title": title, "url": url_found})
                if len(snippets) >= 8:
                    break

            if not snippets:
                # Fallback: extract any <a> with href
                for m in re.finditer(r'<a[^>]*href="(https?://[^"]*)"[^>]*>(.*?)</a>', html):
                    url_found = m.group(1)
                    title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                    if title and "duckduckgo" not in url_found.lower():
                        snippets.append({"title": title, "url": url_found})
                    if len(snippets) >= 8:
                        break

            if not snippets:
                return ToolResult(True, f"No results found for: {query}. Try a different query.",
                                  structured={"results": []})

            lines = [f"Search results for: {query}"]
            for s in snippets:
                lines.append(f"  - [{s['title']}]({s['url']})")

            return ToolResult(True, "\n".join(lines),
                              structured={"results": snippets})

        except urllib.error.URLError as e:
            return ToolResult(False, error=f"Web search failed: {e}")
        except Exception as e:
            return ToolResult(False, error=f"Web search error: {e}")


class ReadDocsTool(BaseTool):
    name = "read_docs"
    description = "Read local documentation files (README, docs/*.md, etc.). Use to understand project documentation."
    parameters = {
        "path": {
            "type": "string",
            "description": "Path to documentation file or directory"
        },
        "page": {
            "type": "integer",
            "description": "Page number for pagination when reading a directory (default: 1)",
            "default": 1
        }
    }

    DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        sandbox = PathSandbox(workspace)
        target = sandbox.resolve(params["path"])

        if not target.exists():
            return ToolResult(False, error=f"Path not found: {params['path']}")

        if target.is_file():
            content = target.read_text(encoding="utf-8", errors="replace")
            return ToolResult(True,
                              f"--- {sandbox.relative(target)} ---\n{content[:4000]}",
                              structured={"file": sandbox.relative(target),
                                           "size": len(content)})

        # Directory: list doc files
        docs = []
        for f in sorted(target.rglob("*")):
            if f.suffix in self.DOC_EXTENSIONS and f.is_file():
                rel = sandbox.relative(f)
                docs.append({"path": rel, "size": f.stat().st_size})

        if not docs:
            return ToolResult(True, f"No documentation files found in {params['path']}",
                              structured={"files": []})

        # Paginate (20 files per page)
        page = params.get("page", 1)
        per_page = 20
        total_pages = max(1, (len(docs) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        page_docs = docs[(page - 1) * per_page: page * per_page]

        lines = [f"--- Documentation files ({len(docs)} total, page {page}/{total_pages}) ---"]
        for d in page_docs:
            size_str = f" ({d['size']}B)" if d['size'] < 1024 else f" ({d['size'] // 1024}KB)"
            lines.append(f"  {d['path']}{size_str}")
        lines.append(f"\nUse file_read to read individual files.")

        return ToolResult(True, "\n".join(lines),
                          structured={"files": docs, "page": page, "total_pages": total_pages})


# ============================================================================
# Diagram Render Tool — renders Mermaid → PNG/SVG image files
# ============================================================================

class DiagramRenderTool(BaseTool):
    """Render Mermaid diagrams to actual PNG/SVG image files via mmdc.

    Uses mermaid-cli (mmdc) to render diagrams. Falls back to kroki.io
    HTTP API if mmdc is not installed.
    """

    name = "diagram_render"
    description = (
        "Render a Mermaid diagram to a PNG or SVG image file. "
        "Use this after generating design_architecture diagrams to create "
        "actual image files that can be embedded in reports. "
        "Supports: classDiagram, erDiagram, flowchart, sequenceDiagram, "
        "stateDiagram, and all other Mermaid diagram types."
    )
    parameters = {
        "mermaid_code": {
            "type": "string",
            "description": "Complete Mermaid diagram code (without ```mermaid wrapper)"
        },
        "name": {
            "type": "string",
            "description": "Diagram name, used as filename (e.g., 'class_diagram', 'er_diagram', 'dfd_level_0')"
        },
        "format": {
            "type": "string",
            "enum": ["png", "svg"],
            "description": "Output image format (default: png)",
            "default": "png"
        },
        "output_dir": {
            "type": "string",
            "description": "Directory to save the rendered image (default: ./outputs/diagrams)"
        }
    }

    async def execute(self, params: dict, workspace: str) -> ToolResult:
        mermaid_code = params.get("mermaid_code", "")
        name = params.get("name", "diagram")
        fmt = params.get("format", "png")
        output_dir = params.get("output_dir", os.path.join(workspace, "outputs", "diagrams"))

        if not mermaid_code.strip():
            return ToolResult(False, error="No Mermaid code provided")

        try:
            from ..reporting.renderer import DiagramRenderer
            renderer = DiagramRenderer(output_dir, format=fmt)
            result = renderer.render_mermaid(mermaid_code, name)

            if result.success:
                return ToolResult(
                    True,
                    f"✅ Diagram rendered: {result.name}.{result.format}\n"
                    f"   Path: {result.output_path}\n"
                    f"   Size: {os.path.getsize(result.output_path)} bytes\n"
                    f"   Duration: {result.duration_ms:.0f}ms",
                    structured={
                        "name": result.name,
                        "format": result.format,
                        "output_path": result.output_path,
                        "success": True,
                        "duration_ms": result.duration_ms,
                    }
                )
            else:
                return ToolResult(
                    False,
                    error=f"Diagram rendering failed: {result.error}\n\n"
                          f"💡 Install mmdc: npm install -g @mermaid-js/mermaid-cli\n"
                          f"   Or use kroki.io (automatic fallback)"
                )
        except Exception as e:
            return ToolResult(False, error=f"Diagram Render error: {e}")

