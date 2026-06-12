"""Context management for DevAgent V2 — cache-aware, hallucination-resistant.

Architecture:
  ┌─────────────────────────────────────────────────┐
  │ MESSAGE 1: SYSTEM (static, fully cacheable)     │
  │   - Agent identity, rules, output format        │
  │   - Tool catalog (stable across session)        │
  ├─────────────────────────────────────────────────┤
  │ MESSAGE 2: REPO MAP (semi-static, rare changes) │
  │   - Directory tree + symbols + dependencies     │
  │   - Incremental update on file add/remove       │
  ├─────────────────────────────────────────────────┤
  │ MESSAGE 3: DYNAMIC CONTEXT (per-turn)           │
  │   - Phase-aware budget allocation               │
  │   - Focused file content + relevant signatures  │
  │   - Recent action/observation pairs             │
  │   - Compressed early history                    │
  │   - Grounding anchors (file:line references)    │
  │   - Test failure verbatim output                │
  └─────────────────────────────────────────────────┘

Cache strategy:
  - Messages 1+2 form a STABLE PREFIX that LLM providers (Anthropic, OpenAI)
    can cache across turns, reducing latency and cost by ~50%.
  - Message 3 is the only variable part that changes each iteration.
  - RepoMap uses incremental mtime-based re-generation.

Hallucination guards:
  - Every code reference includes exact file:line anchors
  - Test failures are shown verbatim, never summarized
  - Phase-adaptive tool scoping reduces decision space
  - Ambiguity is explicitly marked with [UNCERTAIN] tags
  - Read-before-edit enforced at prompt level
"""

import os
import ast
import json
import re
import hashlib
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Context Budget
# ============================================================================

@dataclass
class ContextBudget:
    """Dynamic token budget allocation based on execution phase."""

    # Token budgets per layer (approximate, 1 token ≈ 3 chars for English)
    system_prompt: int = 600       # fixed
    repo_map: int = 600            # fixed
    tool_catalog: int = 400        # fixed
    task_description: int = 300    # fixed
    relevant_files: int = 2000     # phase-dependent
    focus_context: int = 2500      # phase-dependent
    recent_history: int = 2000     # phase-dependent
    compressed_history: int = 600  # phase-dependent
    status_header: int = 200       # fixed

    total_max: int = 8000

    def allocate(self, phase: str) -> dict:
        """Return per-layer caps based on current phase."""
        base = {
            "system_prompt": self.system_prompt,
            "repo_map": self.repo_map,
            "tool_catalog": self.tool_catalog,
            "task_description": self.task_description,
            "status_header": self.status_header,
        }
        if phase == "exploration":
            # More budget for search results and file discovery
            return {**base,
                    "relevant_files": 3000, "focus_context": 1500,
                    "recent_history": 1000, "compressed_history": 500}
        elif phase == "editing":
            # More budget for the file being edited and its dependencies
            return {**base,
                    "focus_context": 3500, "relevant_files": 1000,
                    "recent_history": 1500, "compressed_history": 500}
        elif phase == "verification":
            # More budget for test output and error details
            return {**base,
                    "recent_history": 2500, "focus_context": 1500,
                    "relevant_files": 1000, "compressed_history": 600}
        else:
            return {**base,
                    "relevant_files": 2000, "focus_context": 2500,
                    "recent_history": 2000, "compressed_history": 600}

    def remaining(self, phase: str, used: int) -> int:
        alloc = self.allocate(phase)
        cap = sum(alloc.values())
        return max(0, min(cap, self.total_max) - used)


# ============================================================================
# Phase Detector
# ============================================================================

class PhaseDetector:
    """Detects the agent's current execution phase from recent actions."""

    EXPLORATION_TOOLS = {"grep_text", "grep_ast", "find_symbol", "file_list", "read_docs"}
    EDITING_TOOLS = {"file_read", "file_edit", "file_write"}
    VERIFICATION_TOOLS = {"test_run", "lint_check", "git_diff", "git_blame", "shell_run"}

    @classmethod
    def detect(cls, state: "AgentLoopState") -> str:
        """Determine current phase from recent action history."""
        if not state.action_history:
            return "exploration"

        # Look at last 3 actions for phase detection
        recent = state.action_history[-3:]
        tool_counts = defaultdict(int)
        for a in recent:
            tool_counts[a.get("tool", "")] += 1

        exploration_score = sum(tool_counts[t] for t in cls.EXPLORATION_TOOLS)
        editing_score = sum(tool_counts[t] for t in cls.EDITING_TOOLS)
        verification_score = sum(tool_counts[t] for t in cls.VERIFICATION_TOOLS)

        # Check last action for strong phase signal
        last_tool = state.action_history[-1].get("tool", "")
        if last_tool in ("test_run", "lint_check"):
            return "verification"
        if last_tool in ("file_edit", "file_write"):
            # Check if we just had a test failure (re-enter exploration)
            if state.test_results and state.test_results.get("failed", 0) > 0:
                return "exploration"
            return "editing"
        if last_tool in ("grep_text", "grep_ast", "find_symbol", "file_list"):
            return "exploration"
        if last_tool == "file_read":
            return "editing"

        # Fallback to highest score
        best = max(("exploration", exploration_score),
                    ("editing", editing_score),
                    ("verification", verification_score),
                    key=lambda x: x[1])
        return best[0] if best[1] > 0 else "exploration"


# ============================================================================
# Cache utilities
# ============================================================================

class CacheManager:
    """Manages content-addressed caches for stable prefix optimization."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self._file_mtimes: dict[str, float] = {}          # path → last seen mtime
        self._file_signatures: dict[str, str] = {}        # path → parsed signature text
        self._file_imports: dict[str, list[str]] = {}     # path → internal import targets
        self._repo_map_text: Optional[str] = None
        self._repo_map_hash: Optional[str] = None
        self._system_prompt_text: Optional[str] = None
        self._system_prompt_hash: Optional[str] = None

    def is_file_fresh(self, rel_path: str) -> bool:
        """Check if a cached file signature is still valid."""
        full_path = self.repo_path / rel_path
        try:
            current_mtime = full_path.stat().st_mtime
        except OSError:
            return False
        cached_mtime = self._file_mtimes.get(rel_path)
        return cached_mtime == current_mtime

    def get_or_parse_signature(self, rel_path: str) -> str:
        """Get cached signature or re-parse from file."""
        full_path = self.repo_path / rel_path
        try:
            current_mtime = full_path.stat().st_mtime
        except OSError:
            return ""

        if self._file_mtimes.get(rel_path) == current_mtime:
            return self._file_signatures.get(rel_path, "")

        # Re-parse
        try:
            tree = ast.parse(full_path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return ""

        symbols = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                args = [a.arg for a in node.args.args]
                symbols.append(f"def {node.name}({', '.join(args)})")
            elif isinstance(node, ast.ClassDef):
                bases_str = f"({', '.join(b.id if isinstance(b, ast.Name) else '' for b in node.bases)})" if node.bases else ""
                symbols.append(f"class {node.name}{bases_str}")
            elif isinstance(node, ast.AsyncFunctionDef):
                args = [a.arg for a in node.args.args]
                symbols.append(f"async def {node.name}({', '.join(args)})")

        sig = "\n".join(symbols)
        self._file_mtimes[rel_path] = current_mtime
        self._file_signatures[rel_path] = sig

        # Also cache imports
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.append(node.module.split(".")[0])
        self._file_imports[rel_path] = imports

        return sig

    def get_cached_imports(self, rel_path: str) -> list[str]:
        """Get cached internal imports for a file."""
        self.get_or_parse_signature(rel_path)  # ensure cache is populated
        return self._file_imports.get(rel_path, [])

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def set_repo_map(self, text: str):
        self._repo_map_text = text
        self._repo_map_hash = self.hash_text(text)

    def get_repo_map(self) -> Optional[str]:
        return self._repo_map_text

    def set_system_prompt(self, text: str):
        self._system_prompt_text = text
        self._system_prompt_hash = self.hash_text(text)

    def get_system_prompt(self) -> Optional[str]:
        return self._system_prompt_text

    def invalidate_file(self, rel_path: str):
        """Invalidate cache for a modified file."""
        self._file_mtimes.pop(rel_path, None)
        self._file_signatures.pop(rel_path, None)
        self._file_imports.pop(rel_path, None)
        self._repo_map_text = None  # force repo map rebuild


# ============================================================================
# L1: Repo Map (incremental)
# ============================================================================

class RepoMap:
    """Generates a compact, cache-friendly repo map with incremental updates."""

    SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "venv", ".venv",
                 "node_modules", ".egg-info", ".mypy_cache", ".ruff_cache",
                 "outputs", "benchmark_report", ".devagent", "docs", ".claude"}

    def __init__(self, cache: CacheManager):
        self.cache = cache

    def generate(self, repo_path: str, max_depth: int = 3) -> str:
        """Generate repo map, using incremental cache where possible."""
        root = Path(repo_path).resolve()
        sections = []

        tree = self._generate_tree(root, max_depth)
        sections.append(tree)

        deps = self._generate_dependencies(root)
        if deps:
            sections.append(deps)

        symbols = self._generate_symbols(root)
        if symbols:
            sections.append(symbols)

        tests = self._map_tests(root)
        if tests:
            sections.append(tests)

        result = "\n".join(sections)
        self.cache.set_repo_map(result)
        return result

    def _generate_tree(self, root: Path, max_depth: int) -> str:
        lines = ["## Repo Map", "```"]
        self._tree_recurse(root, root, 0, max_depth, lines)
        lines.append("```")
        return "\n".join(lines)

    def _tree_recurse(self, root: Path, current: Path, depth: int,
                      max_depth: int, lines: list):
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name))
        except PermissionError:
            return

        for entry in entries:
            if entry.name in self.SKIP_DIRS or entry.name.startswith("."):
                continue
            indent = "  " * depth
            if entry.is_dir():
                lines.append(f"{indent}{entry.name}/")
                self._tree_recurse(root, entry, depth + 1, max_depth, lines)
            elif entry.suffix in (".py", ".yaml", ".yml", ".cfg", ".toml", ".md", ".json"):
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                rel = str(entry.relative_to(root))
                if entry.suffix == ".py" and depth <= max_depth:
                    sig = self.cache.get_or_parse_signature(rel)
                    sig_lines = sig.split("\n") if sig else []
                    first_line = sig_lines[0] if sig_lines else ""
                    rest_count = len(sig_lines) - 1
                    lines.append(f"{indent}{entry.name} — {first_line}" +
                                 (f" (+{rest_count} more)" if rest_count > 0 else ""))
                else:
                    size_str = f" ({size}B)" if size < 1024 else f" ({size//1024}KB)"
                    lines.append(f"{indent}{entry.name}{size_str}")

    def _generate_dependencies(self, root: Path) -> str:
        internal_modules = set()
        dep_graph = defaultdict(set)

        for py_file in root.rglob("*.py"):
            if any(s in py_file.parts for s in self.SKIP_DIRS):
                continue
            rel = str(py_file.relative_to(root))
            internal_modules.add(rel)
            imports = self.cache.get_cached_imports(rel)
            for imp in imports:
                dep_graph[imp].add(rel)

        # Filter to internal deps only
        internal_deps = {
            k: {d for d in v if d in internal_modules}
            for k, v in dep_graph.items()
        }
        non_empty = {k: v for k, v in internal_deps.items() if v}

        if not non_empty:
            return ""

        lines = ["## Module Dependencies"]
        for mod, dependents in sorted(non_empty.items()):
            short_deps = sorted({d.split("/")[-1].replace(".py", "") for d in dependents})
            lines.append(f"  {mod} ← {' ,'.join(short_deps[:5])}")
            if len(short_deps) > 5:
                lines[-1] += f" (+{len(short_deps) - 5} more)"
        return "\n".join(lines)

    def _generate_symbols(self, root: Path) -> str:
        symbols = []
        for py_file in root.rglob("*.py"):
            if any(s in py_file.parts for s in self.SKIP_DIRS):
                continue
            rel = str(py_file.relative_to(root))
            sig = self.cache.get_or_parse_signature(rel)
            if not sig:
                continue
            for line in sig.split("\n"):
                if line.strip():
                    symbols.append(f"  {rel}:{line.strip()}")

        if not symbols:
            return ""
        lines = ["## Key Symbols"]
        lines.extend(symbols[:50])
        if len(symbols) > 50:
            lines.append(f"  ... and {len(symbols) - 50} more")
        return "\n".join(lines)

    def _map_tests(self, root: Path) -> str:
        test_dir = root / "tests"
        if not test_dir.exists():
            return ""
        mappings = []
        for test_file in sorted(test_dir.rglob("test_*.py")):
            try:
                name = test_file.stem
                target = name[5:]
                # Check if target files exist
                matches = list(root.rglob(f"*{target}*.py"))
                matches = [m for m in matches
                          if not any(s in m.parts for s in self.SKIP_DIRS)
                          and "test" not in str(m.relative_to(root)).lower()]
                if matches:
                    for m in matches[:2]:
                        mappings.append(
                            f"  tests/{test_file.name} → {m.relative_to(root)}"
                        )
            except Exception:
                continue
        if mappings:
            return "\n".join(["## Test → Source"] + mappings[:15])
        return ""


# ============================================================================
# L2: Relevant File Retriever
# ============================================================================

class RelevantFileRetriever:
    """Multi-signal file retrieval."""

    SKIP_DIRS = RepoMap.SKIP_DIRS

    def retrieve(self, task: str, repo_path: str, top_k: int = 5,
                 modified_files: list[str] = None) -> list[str]:
        root = Path(repo_path).resolve()
        scores = defaultdict(float)
        keywords = self._extract_keywords(task)

        for py_file in root.rglob("*.py"):
            if any(s in py_file.parts for s in self.SKIP_DIRS):
                continue
            rel = str(py_file.relative_to(root))
            try:
                # First: fast path check
                path_lower = rel.lower()
                path_match = any(kw in path_lower for kw in keywords)

                if path_match:
                    score = self._relevance_score(rel, "", keywords)
                else:
                    # Content check (slower, only if path didn't match)
                    content = py_file.read_text(encoding="utf-8", errors="replace")[:4000]
                    score = self._relevance_score(rel, content, keywords)

                # Signal: recently modified files get boosted
                if modified_files and rel in modified_files:
                    score += 5.0

                if score > 0:
                    scores[rel] = score
            except Exception:
                continue

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [f for f, _ in ranked[:top_k]]

    @staticmethod
    def _extract_keywords(task: str) -> list[str]:
        words = re.findall(r'[a-zA-Z_]\w+', task.lower())
        stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                'in', 'on', 'at', 'to', 'for', 'of', 'with', 'and', 'or',
                'this', 'that', 'it', 'its', 'from', 'by', 'as', 'not',
                'should', 'would', 'could', 'will', 'can', 'must', 'need',
                'has', 'have', 'had', 'do', 'does', 'did', 'fix', 'please'}
        return [w for w in words if w not in stop and len(w) > 2]

    def _relevance_score(self, file_path: str, content: str, keywords: list[str]) -> float:
        """Score a file's relevance by matching keywords against path AND content."""
        path_lower = file_path.lower()
        content_lower = content.lower()
        score = 0.0
        for kw in keywords:
            if kw in path_lower:
                score += 5.0  # strong signal: keyword in filename
            elif kw in content_lower:
                score += 1.5  # moderate signal: keyword in content
        if "test" in path_lower:
            score *= 0.7  # de-prioritize test files
        return score


# ============================================================================
# L3: Focus Context Builder
# ============================================================================

class FocusContextBuilder:
    """Builds ground-truthed context around target files with exact line numbers."""

    def __init__(self, cache: CacheManager):
        self.cache = cache

    def build(self, focus_file: str, repo_path: str) -> str:
        root = Path(repo_path).resolve()
        focus_path = root / focus_file
        sections = []

        if focus_path.exists():
            content = focus_path.read_text(encoding="utf-8", errors="replace")
            # Show full content with line numbers as grounding anchors
            numbered = self._add_line_numbers(content, focus_file)
            sections.append(f"### {focus_file} (L1-{len(content.split(chr(10)))}) — FULL\n```python\n{numbered}\n```")

            # Direct dependencies (signatures only)
            imports = self.cache.get_cached_imports(focus_file)
            for imp in imports:
                imp_path = self._resolve_import(root, imp)
                if imp_path and imp_path.exists():
                    rel = str(imp_path.relative_to(root))
                    sig = self.cache.get_or_parse_signature(rel)
                    if sig:
                        sections.append(f"### {rel} (imported) — signatures\n```python\n{sig}\n```")
        else:
            sections.append(f"### {focus_file} — FILE NOT FOUND")

        return "\n\n".join(sections)

    @staticmethod
    def _add_line_numbers(content: str, file_path: str) -> str:
        """Add line number prefixes for grounding."""
        lines = content.split("\n")
        width = max(4, len(str(len(lines))))
        return "\n".join(f"{i+1:{width}d}|{line}" for i, line in enumerate(lines))

    @staticmethod
    def _resolve_import(root: Path, module: str) -> Optional[Path]:
        parts = module.split(".")
        pkg = root.joinpath(*parts)
        if pkg.is_dir():
            init = pkg / "__init__.py"
            if init.exists():
                return init
        py_file = root.joinpath(*parts[:-1], f"{parts[-1]}.py")
        if py_file.exists():
            return py_file
        return None


# ============================================================================
# L4: History Compressor
# ============================================================================

class HistoryCompressor:
    """Compresses early rounds into factually-grounded summaries."""

    def compress(self, actions: list[dict], observations: list[dict],
                 recent_rounds: int = 5) -> str:
        if len(actions) <= recent_rounds:
            return ""

        to_compress = actions[:-recent_rounds]
        obs_to_compress = observations[:-recent_rounds]

        # Group by factual categories — preserve exact values, never summarize test output
        files_read: set[str] = set()
        files_modified: dict[str, str] = {}  # file → last action
        test_history: list[dict] = []
        errors: list[str] = []
        key_discoveries: list[str] = []

        for a, o in zip(to_compress, obs_to_compress):
            tool = a.get("tool", "")
            if tool == "file_read":
                p = a.get("params", {}).get("path", "")
                if p:
                    files_read.add(p)
            elif tool in ("file_edit", "file_write"):
                p = a.get("params", {}).get("path", "")
                if p:
                    files_modified[p] = tool
            elif tool == "test_run":
                sr = o.get("structured", {})
                test_history.append({
                    "passed": sr.get("passed", "?"),
                    "failed": sr.get("failed", "?"),
                    "iteration": a.get("iteration", "?")
                })
            elif tool in ("grep_text", "grep_ast", "find_symbol"):
                mc = o.get("structured", {}).get("count", 0)
                if mc > 0:
                    pat = str(a.get("params", {}).get("pattern",
                              a.get("params", {}).get("query",
                              a.get("params", {}).get("name", ""))))
                    key_discoveries.append(f"Found {mc} results for '{pat}'")

            if not o.get("success", True):
                errors.append(f"[Iter {a.get('iteration', '?')}] {tool}: {o.get('error', '')[:150]}")

        lines = ["## Earlier Rounds Summary"]
        lines.append(f"(Rounds 1–{len(to_compress)}, compressed for space)")
        if files_read:
            lines.append(f"Files read: {', '.join(sorted(files_read))}")
        if files_modified:
            items = [f"{p} ({action})" for p, action in sorted(files_modified.items())]
            lines.append(f"Files modified: {', '.join(items)}")
        if test_history:
            lines.append("Test results history:")
            for th in test_history:
                lines.append(f"  [Iter {th['iteration']}] passed={th['passed']}, failed={th['failed']}")
        if key_discoveries:
            lines.append("Key discoveries:")
            lines.extend(f"  - {d}" for d in key_discoveries[:8])
        if errors:
            lines.append("Errors encountered:")
            lines.extend(f"  - {e}" for e in errors[:5])

        return "\n".join(lines) + "\n"


# ============================================================================
# Hallucination Guards
# ============================================================================

class HallucinationGuard:
    """Validates LLM actions against ground truth to prevent hallucination."""

    @staticmethod
    def validate_edit_target(file_path: str, workspace: str) -> tuple[bool, str]:
        """Verify that a file the agent wants to edit actually exists."""
        full = Path(workspace) / file_path
        if not full.exists():
            return False, f"HALLUCINATION GUARD: File '{file_path}' does not exist. Use grep_text or file_list to locate the correct file first."
        return True, ""

    @staticmethod
    def validate_function_name(
        file_path: str, function_name: str, workspace: str
    ) -> tuple[bool, str]:
        """Verify a function name the agent references exists in the file."""
        full = Path(workspace) / file_path
        if not full.exists():
            return False, f"File '{file_path}' not found."

        try:
            tree = ast.parse(full.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, Exception):
            return True, ""  # Don't block on parse errors

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                return True, ""
        return False, (f"HALLUCINATION GUARD: Function '{function_name}' not found "
                        f"in '{file_path}'. Check grep_ast for the correct name.")

    @staticmethod
    def build_grounding_context(state: "AgentLoopState") -> str:
        """Build anti-hallucination grounding signals for the prompt."""
        signals = []

        # Signal 1: What files the agent has actually seen
        files_seen = set()
        for a in state.action_history:
            if a.get("tool") == "file_read":
                p = a.get("params", {}).get("path", "")
                if p:
                    files_seen.add(p)

        if files_seen:
            signals.append(
                "### Files You Have Read (you may edit these)\n" +
                "\n".join(f"  - {f}" for f in sorted(files_seen))
            )
        else:
            signals.append(
                "### Files You Have Read: NONE\n"
                "You have NOT read any files yet. Use file_read or grep_text "
                "BEFORE attempting any edits. NEVER guess file contents."
            )

        # Signal 2: Files the agent has modified
        if state.modified_files:
            signals.append(
                "### Files You Have Modified\n" +
                "\n".join(f"  - {f}" for f in state.modified_files)
            )

        # Signal 3: Last test result (verbatim)
        if state.test_results:
            tr = state.test_results
            passed = tr.get("passed", 0)
            failed = tr.get("failed", 0)
            if failed > 0:
                signals.append(
                    f"### LAST TEST RESULT: {failed} FAILED, {passed} passed\n"
                    "Tests are FAILING. Fix the code and run test_run again."
                )
            else:
                signals.append(
                    f"### LAST TEST RESULT: ALL {passed} PASSED\n"
                    "All tests are passing. If the task is complete, call submit."
                )

        # Signal 4: Explicit ambiguity markers
        if not state.modified_files and state.current_iteration > 5:
            signals.append(
                "### [UNCERTAIN] You have been exploring for 5+ iterations "
                "without making changes. Consider reading a file and making a focused edit."
            )

        return "\n\n".join(signals) if signals else ""


# ============================================================================
# Shared Prompt Segments — injected into system prompt and agent prompts
# ============================================================================

SECURITY_STANDARDS_BLOCK = """
## 🔒 SECURITY STANDARDS (OWASP Top 10 + Best Practices)

### Injection Prevention
1. SQL: NEVER concatenate user input into SQL strings. Use parameterized queries / ORM.
2. Shell: Use `subprocess.run([cmd, arg])` with list args. NEVER `shell=True` with user input.
3. HTML/JS: Auto-escape all user-provided data in templates.

### Authentication & Authorization
4. Passwords: Hash with bcrypt/argon2 ONLY. No MD5/SHA for passwords.
5. Tokens: Use `secrets.token_urlsafe()` for session tokens. JWTs MUST have expiration.
6. Access Control: Verify authorization on EVERY endpoint. Role checks at service layer.

### Secret Management
7. NO hardcoded secrets (API keys, passwords, tokens) in source code.
8. Use environment variables or .env files. .env MUST be in .gitignore.
9. Generate .env.example with placeholder values (not real secrets).

### Common Vulnerabilities to PREVENT
10. Path Traversal: Validate/sanitize all file paths. Use `os.path.realpath()` checks.
11. Insecure Deserialization: NEVER `pickle.loads()` on untrusted data.
12. XSS: Escape user input in HTML contexts. Set Content-Security-Policy headers.
13. CSRF: API endpoints with cookie auth require CSRF tokens.
14. Open Redirect: Validate redirect URLs against a whitelist.

### FORBIDDEN Patterns — DO NOT USE
- `eval()` or `exec()` on any input
- `shell=True` in subprocess
- SQL string concatenation with user data
- `pickle.loads()` on untrusted data
- Hardcoded cryptographic keys or passwords
- `assert` for security validation (disabled with -O flag)
- `==` for secret/hash comparison (timing attack)
"""

OBSERVABILITY_STANDARDS_BLOCK = """
## 📊 OBSERVABILITY REQUIREMENTS

### Structured Logging
1. Use `logging` module with JSON formatter or `structlog`.
2. Every log entry MUST include: timestamp, level, logger, message, request_id.
3. Log at service boundaries: request received, response sent, dependency calls.
4. NEVER log: passwords, tokens, session IDs, PII (emails, phones, SSNs).

### Health Checks
5. Every service MUST have a `/health` endpoint returning:
   `{"status": "ok"/"degraded", "version": "x.y.z", "dependencies": {"db": "ok", ...}}`

### Metrics
6. Track: request count, latency (avg/p95/p99), error rate, dependency health.
7. Use `prometheus_client` library for Python services.

### Tracing
8. Propagate `X-Request-ID` header across service boundaries.
9. Include request_id in all log entries for correlation.
"""

CITATION_REQUIREMENTS_BLOCK = """
## 🔗 TRACEABILITY & CITATION REQUIREMENTS

### Cross-Reference Format
1. **Requirements → Design**: Every design decision MUST cite the requirement ID it addresses.
   Example: "Module AuthService implements FR-01, FR-02 (User Authentication)"
2. **Design → Code**: Every module/class docstring MUST cite the design section.
   Example: "Implements Section 3.2 Auth Module from Architecture Design Spec"
3. **Code → Test**: Every test function docstring MUST cite the requirement ID [FR-XX].
   Example: "Tests FR-01: User login with valid credentials"
4. **Use exact IDs**: FR-01, NFR-SEC-01, UC-03 — NOT vague descriptions.

### Traceability Matrix (Required in Final Report)
| Requirement ID | Design Section | Code File(s) | Test File(s) | Status |
|---------------|---------------|-------------|-------------|--------|
| FR-01 | §3.2 Auth | src/auth.py | tests/test_auth.py | ✅ |

### Document Control (All Formal Documents)
Every generated document (.md) MUST include a header:
```markdown
| Field | Value |
|-------|-------|
| Document ID | XXX-v1.0 |
| Version | 1.0 |
| Date | YYYY-MM-DD |
| Author | DevAgent (AI-assisted) |
| Status | Draft / Final |
```
"""

ANTI_PATTERN_BLOCK = """
## ⚠️ ANTI-PATTERNS TO DETECT AND AVOID

### Code Anti-Patterns
1. **God Class** (>300 lines, >10 public methods) → Split by responsibility
2. **Long Method** (>50 lines) → Extract helper methods with clear names
3. **Primitive Obsession** (str/int for domain concepts) → Create value objects / types
4. **Feature Envy** (method accessing another object's data heavily) → Move the method
5. **Shotgun Surgery** (one change touches 5+ files) → Consolidate scattered logic
6. **Magic Numbers** → Replace with named constants (UPPER_CASE at module level)
7. **Deep Nesting** (>3 levels of if/for/try) → Extract methods, use early returns

### Architecture Anti-Patterns
8. **Circular Imports** → Extract shared interface/abc module
9. **Direct DB from Controller** → Repository/DAO pattern
10. **Hard-coded Config** → Environment variables + pydantic-settings
11. **Singleton Abuse** → Dependency injection instead

### Testing Anti-Patterns
12. **Testing Implementation** (not behavior) → Test public API only
13. **Interdependent Tests** → Each test isolated with fixtures
14. **Mocking Everything** → Only mock external I/O, not domain logic
15. **assert True / assert 1 == 1** → Meaningful assertions with expected values
16. **No Error-Path Tests** → Every function: 1 happy + 1 error + 1 edge case

### Security Anti-Patterns (Critical)
17. `except:` (bare except) → Always specify exception type
18. `except Exception: pass` (silent swallow) → At minimum, log the error
19. `subprocess.run(cmd, shell=True)` → Use list args
20. `open(user_input_path)` without validation → Sanitize paths first
"""

MEASURABLE_QUALITY_CHECKLIST = """
## ✅ MEASURABLE QUALITY CHECKLIST

Before calling request_review or submit, VERIFY:

### Code
- [ ] All public functions have type hints (parameters AND return)
- [ ] All public classes/functions have docstrings (Google style)
- [ ] 0 bare `except:` clauses
- [ ] 0 `print()` statements (use `logging` instead)
- [ ] No lines > 120 characters
- [ ] No functions > 50 lines
- [ ] ruff/flake8 lint: 0 errors

### Tests
- [ ] Every public function has ≥1 test
- [ ] Test pass rate: 100%
- [ ] Tests use pytest fixtures (not global state)
- [ ] Tests have meaningful assertion messages

### Design Documents
- [ ] All 7 artifacts present (context, container, class, ER, sequence, API, modules)
- [ ] DFD Level 0 and Level 1 diagrams included
- [ ] Every Mermaid diagram is syntactically valid
- [ ] Every design decision has a rationale (ADR format preferred)

### Requirements Documents
- [ ] All FRs have measurable acceptance criteria
- [ ] All NFRs have target metrics
- [ ] Every use case has main flow + alternative flows
- [ ] Domain model: all entities have typed attributes + relationships
"""


# ============================================================================
# System Prompts (cache-stable)
# ============================================================================

BASE_SYSTEM_PROMPT = """You are DevAgent — a unified autonomous software engineering agent.

You operate in a ReAct loop (Think → Act → Observe) and have access to professional software engineering tools for the full development lifecycle.

## LANGUAGE RULES — IMPORTANT
- Input requirements may be in Chinese or English. Read and understand them regardless of language.
- ALL generated documents, reports, architecture specifications, and final deliverables MUST be in CHINESE (Simplified Chinese, 简体中文).
- Code and code comments should be in English (standard practice).
- Docstrings can be in Chinese or bilingual.

## Your Task
{task}

## Workspace
{workspace_info}
""" + SECURITY_STANDARDS_BLOCK + OBSERVABILITY_STANDARDS_BLOCK + CITATION_REQUIREMENTS_BLOCK + ANTI_PATTERN_BLOCK + MEASURABLE_QUALITY_CHECKLIST + """

## Execution Mode

Based on the task, determine which mode you are in:

### FULL PIPELINE MODE (requirements → design → code → test → deliver)
When asked to build something from scratch or given a requirements document:
1. **plan_task** — Decompose the task into an ordered execution plan
2. **analyze_requirements** — Extract structured requirements with domain model, use cases
3. **design_architecture** — Create C4 architecture, class diagrams, API contracts, DFDs
4. **generate_code** — Generate production-grade source code from the design
5. **generate_tests** — Create comprehensive pytest test suites and execute them
6. **[if tests fail]** → **debug_issue** → **repair_code** → re-test
7. **generate_report** — Create executive summary report
8. **request_review** (phase='delivery') → **submit**

At each milestone (after steps 2, 4, 5, 6, 7), call request_review to get human feedback.

### REPAIR MODE (fix bugs, debug issues)
When asked to fix a bug or debug an issue:
1. Explore with grep_text, file_read to understand the codebase
2. **generate_tests** or **test_run** to reproduce the issue
3. **debug_issue** to analyze root cause
4. **repair_code** to apply minimal fixes
5. **request_review** (phase='fix') → **submit**

### EXPLORATION MODE (understand code, analyze)
When asked to understand or analyze existing code:
1. Explore with grep_text, grep_ast, file_list, file_read
2. **ask_user** if you need clarification
3. Summarize findings and **request_review** or **submit**

## Output Format — CRITICAL
You MUST respond with exactly:
THOUGHT: <your reasoning about the current state and what to do next>
ACTION: <tool_name>
PARAMS: <valid JSON parameters>

## ANTI-HALLUCINATION RULES
1. NEVER invent file paths. Use grep_text or file_list to locate files.
2. ALWAYS read a file BEFORE editing it.
3. ALWAYS include exact line numbers when referencing code.
4. When a test fails, SHOW the exact error before attempting a fix.
5. If uncertain about a file location, search first — do NOT guess.
6. Only reference files that appear in the "Files You Have Read" section.
7. Make ONE focused change at a time, then verify.
8. If a fix fails, READ the new error carefully — do NOT repeat the same fix.
9. Every document MUST have a Document Control table (ID, Version, Date, Author, Status).
10. Every code reference MUST cite the source file and line (e.g., `src/auth.py:42`).

## QUALITY STANDARDS — Professional Software Engineering Level
Your work will be reviewed by a human against these standards:

### Code Quality
- **Correctness**: Logic must correctly solve the problem. Handle edge cases explicitly.
- **Error Handling**: Use try/except with specific exception types. Provide meaningful error messages.
- **Type Safety**: All function signatures must have complete type hints (parameters AND return type).
- **Documentation**: Every public function/class must have a Google-style docstring explaining purpose, args, returns, and raises.
- **Structure**: Single responsibility per function. Clean interfaces. No god classes (>300 lines).
- **Testing**: Every public function should have: 1 happy-path test, 1 error-path test, 1 edge-case test.
- **Security**: Follow the SECURITY STANDARDS above. Use parameterized queries, hash passwords, validate input.

### Report / Document Quality
- **Completeness**: No placeholders or "TODO" items. Every section filled with concrete content.
- **Specificity**: Use real code references, actual file paths, and concrete values. No vague statements.
- **Professionalism**: Correct technical terminology. Proper formatting. Actionable and precise.
- **Traceability**: Every design element cites its source requirement. Every test cites its target requirement.
- **Document Control**: Every formal document must have a control header (ID, version, date, status).

## REVIEW RULES (MUST OBEY)

1. After EACH pipeline tool, call request_review ONCE with real file paths from file_list.
2. request_review returns a SINGLE TOOL NAME like "design_architecture" or "submit" — that is your NEXT action. Execute it immediately.
3. If request_review returns the SAME tool name: you got REVISE. Redo that tool with fixes, then request_review again.
4. If request_review says "NO FILES": generate output first, do NOT call request_review again without files.
5. Follow the SEQUENCE: analyze_requirements → request_review → design_architecture → request_review → generate_code → request_review → test_run → request_review → submit
"""

TOOL_CATALOG_HEADER = """## Tools
{tool_descriptions}

### Key pipeline tools (use in order):
- plan_task → analyze_requirements → design_architecture → generate_code → generate_tests → test_run → submit
- debug_issue → repair_code → test_run (for bug fixing)

### Interactive tools:
- request_review — submit work for human review (call ONCE per phase, then follow response)
- ask_user — ask human a question
- submit — finish the task (ONLY after delivery review approved)

### Code tools:
- file_read, file_edit, file_write, file_list
- grep_text, grep_ast, find_symbol
- shell_run, test_run, lint_check
- git_diff, git_log, git_blame
- web_search, read_docs
"""

EXPLORATION_HINT = """
## Current Phase: EXPLORATION
You are locating relevant code. Use grep_text, grep_ast, find_symbol, and file_list.
DO NOT edit files yet — first understand the codebase and locate the problem.
"""

EDITING_HINT = """
## Current Phase: EDITING
You have located the relevant code. Use file_read to see full context, then file_edit.
Make minimal, focused changes. Run lint_check after editing.
"""

VERIFICATION_HINT = """
## Current Phase: VERIFICATION
You have made changes. Run test_run to verify. If tests fail, analyze the output and
return to EXPLORATION to locate the root cause. If tests pass, call submit.
"""


# ============================================================================
# Unified Context Manager (cache-aware multi-message API)
# ============================================================================

class ContextManager:
    """Orchestrates context with cache-optimized multi-message structure."""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.cache = CacheManager(repo_path)
        self.repo_map_gen = RepoMap(self.cache)
        self.retriever = RelevantFileRetriever()
        self.focus_builder = FocusContextBuilder(self.cache)
        self.compressor = HistoryCompressor()
        self.budget = ContextBudget()
        self.guard = HallucinationGuard()

    # ==================================================================
    # Public helpers
    # ==================================================================

    def get_repo_map(self) -> str:
        """Get the current repo map, generating it if needed."""
        repo_map = self.cache.get_repo_map()
        if repo_map is None:
            repo_map = self.repo_map_gen.generate(self.repo_path)
        return repo_map

    # ==================================================================
    # Multi-message context builder (cache-optimized)
    # ==================================================================

    def build_messages(self,
                       task: str,
                       state: "AgentLoopState",
                       tool_descriptions: str) -> list[dict]:
        """Build a cache-optimized list of messages.

        Message 1: System (static prefix — cacheable)
        Message 2: Repo map (semi-static — rarely changes)
        Message 3: Dynamic context (per-turn — never cached)
        """
        phase = PhaseDetector.detect(state)
        messages = []

        # === MESSAGE 1: System (fully cacheable) ===
        system_text = self._build_system_message(task, state, tool_descriptions, phase)
        messages.append({"role": "system", "content": system_text})

        # === MESSAGE 2: Repo Map (semi-static, rare invalidation) ===
        repo_map = self.cache.get_repo_map()
        if repo_map is None:
            repo_map = self.repo_map_gen.generate(self.repo_path)
        messages.append({"role": "system", "content": f"## Project Structure\n{repo_map}"})

        # === MESSAGE 3: Dynamic Context (per-turn) ===
        dynamic = self._build_dynamic_message(task, state, phase)
        messages.append({"role": "user", "content": dynamic})

        return messages

    def _build_system_message(self, task: str, state: "AgentLoopState",
                               tool_descriptions: str, phase: str) -> str:
        """Build the cache-stable system prompt."""
        cached = self.cache.get_system_prompt()
        if cached:
            return cached

        workspace_info = f"- Workspace: {state.workspace}\n- Language: {state.language}\n- Iteration limit: {state.max_iterations}"

        parts = [BASE_SYSTEM_PROMPT.replace("{task}", task).replace("{workspace_info}", workspace_info)]
        parts.append(TOOL_CATALOG_HEADER.format(tool_descriptions=tool_descriptions))

        # Phase hint (changes with phase, but small enough to not break cache much)
        if phase == "exploration":
            parts.append(EXPLORATION_HINT)
        elif phase == "editing":
            parts.append(EDITING_HINT)
        elif phase == "verification":
            parts.append(VERIFICATION_HINT)

        result = "\n".join(parts)
        self.cache.set_system_prompt(result)
        return result

    def _build_dynamic_message(self, task: str, state: "AgentLoopState",
                                 phase: str) -> str:
        """Build the per-turn dynamic context with budget-aware allocation."""
        alloc = self.budget.allocate(phase)
        parts = []

        # === Grounding signals (anti-hallucination) ===
        grounding = self.guard.build_grounding_context(state)
        if grounding:
            parts.append(grounding)

        # === Relevant files (exploration phase) ===
        if phase == "exploration":
            relevant = self.retriever.retrieve(
                task, self.repo_path, top_k=5,
                modified_files=state.modified_files
            )
            if relevant:
                parts.append(f"## Relevant Files to Investigate\n{', '.join(relevant)}")

        # === Focus context (editing phase: show recently read/modified files) ===
        if phase in ("editing", "verification") and state.modified_files:
            for f in state.modified_files[-2:]:  # last 2 modified files
                focus = self.focus_builder.build(f, self.repo_path)
                if focus:
                    parts.append(focus)
        # Show the last-read file even if not modified
        elif phase == "editing":
            last_read = self._last_read_file(state)
            if last_read:
                focus = self.focus_builder.build(last_read, self.repo_path)
                if focus:
                    parts.append(focus)

        # === History ===
        recent_rounds = state.recent_history_rounds
        actions, obs = state.get_recent_history(recent_rounds)

        # Compress early history
        if len(state.action_history) > recent_rounds:
            summary = self.compressor.compress(
                state.action_history, state.observation_history, recent_rounds
            )
            if summary:
                parts.append(summary)

        # Recent rounds with FACTUAL precision
        parts.append(self._format_recent_with_anchors(actions, obs))

        # === Current status ===
        parts.append(self._format_status(state, phase))

        result = "\n\n".join(parts)

        # Budget enforcement: truncate if over budget
        max_chars = alloc.get("recent_history", 2000) + alloc.get("focus_context", 2500) + alloc.get("relevant_files", 2000)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n\n...[context truncated for budget]..."

        return result

    # ==================================================================
    # Legacy single-string API (backward compat)
    # ==================================================================

    def build_context(self, task: str, state: "AgentLoopState",
                      tool_descriptions: str) -> str:
        """Legacy single-string context. Prefer build_messages() for cache benefits."""
        msgs = self.build_messages(task, state, tool_descriptions)
        parts = []
        for m in msgs:
            prefix = f"[{m['role']}]" if m['role'] == 'system' else ""
            parts.append(f"{prefix}\n{m['content']}")
        return "\n\n".join(parts)

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _last_read_file(state: "AgentLoopState") -> Optional[str]:
        for a in reversed(state.action_history):
            if a.get("tool") == "file_read":
                return a.get("params", {}).get("path")
        return None

    @staticmethod
    def _format_recent_with_anchors(actions: list[dict],
                                     observations: list[dict]) -> str:
        """Format recent history with exact grounding anchors."""
        lines = ["## Recent Actions"]
        for a, o in zip(actions, observations):
            iteration = a.get("iteration", "?")
            tool = a.get("tool", "unknown")
            params = a.get("params", {})
            success = "OK" if o.get("success", False) else "FAIL"
            output = o.get("output", "") or o.get("error", "")

            # Truncate long file content outputs to keep context compact
            if tool == "file_read" and len(output) > 1200:
                output = output[:1200] + "\n...[file truncated]..."
            elif tool in ("grep_text", "grep_ast", "find_symbol") and len(output) > 800:
                output = output[:800] + "\n...[results truncated]..."
            elif tool == "test_run" and len(output) > 1500:
                # Keep error section, truncate passing tests list
                err_idx = output.find("FAILURES")
                if err_idx > 0:
                    output = output[err_idx:err_idx + 1200]
                else:
                    output = output[:1000]

            lines.append(f"\n### Step {iteration}: {tool} [{success}]")
            # Show params compactly
            params_str = ", ".join(f"{k}={repr(v)[:60]}" for k, v in params.items())
            lines.append(f"Params: {params_str}")
            lines.append(f"```\n{output[:1500]}\n```")

        return "\n".join(lines)

    @staticmethod
    def _format_status(state: "AgentLoopState", phase: str) -> str:
        return (
            f"## Status\n"
            f"- Phase: {phase.upper()}\n"
            f"- Iteration: {state.current_iteration}/{state.max_iterations}\n"
            f"- Files modified: {', '.join(state.modified_files) if state.modified_files else 'none'}\n"
            f"- Tests: {state.test_results.get('passed', 0)} passed, "
            f"{state.test_results.get('failed', 0)} failed"
            if state.test_results else "- Tests: not yet run"
        )

    # ==================================================================
    # Invalidation — call after file modifications
    # ==================================================================

    def on_file_modified(self, rel_path: str):
        """Notify context manager that a file was modified externally."""
        self.cache.invalidate_file(rel_path)

    def on_file_created(self, rel_path: str):
        """Notify that a new file was created."""
        self.cache.invalidate_file(rel_path)
        self._repo_map_text = None

    def on_file_deleted(self, rel_path: str):
        """Notify that a file was deleted."""
        self.cache._file_mtimes.pop(rel_path, None)
        self.cache._file_signatures.pop(rel_path, None)
        self.cache._file_imports.pop(rel_path, None)
        self.cache._repo_map_text = None


# ============================================================================
# ContextualToolFilter — reduce tool scope to minimize hallucination risk
# ============================================================================

class ContextualToolFilter:
    """Limits available tools based on execution phase to reduce hallucination."""

    FULL_TOOL_SET = {
        "file_read", "file_edit", "file_write", "file_list",
        "grep_text", "grep_ast", "find_symbol",
        "shell_run", "test_run", "lint_check",
        "git_diff", "git_log", "git_blame",
        "web_search", "read_docs", "submit"
    }

    PHASE_TOOLS = {
        "exploration": {
            "grep_text", "grep_ast", "find_symbol", "file_list",
            "file_read", "web_search", "read_docs", "submit"
        },
        "editing": {
            "file_read", "file_edit", "file_write",
            "lint_check", "git_diff", "shell_run", "submit"
        },
        "verification": {
            "test_run", "lint_check", "git_diff", "git_log", "git_blame",
            "file_read", "file_edit", "submit"
        },
    }

    @classmethod
    def filter(cls, phase: str) -> set[str]:
        return cls.PHASE_TOOLS.get(phase, cls.FULL_TOOL_SET)

    @classmethod
    def should_offer(cls, tool_name: str, phase: str) -> bool:
        allowed = cls.filter(phase)
        return tool_name in allowed
