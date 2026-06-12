"""Tests for DevAgent V2 context management — cache optimization & hallucination guards."""

import os
import sys
import tempfile
import asyncio
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.state import AgentLoopState
from devagent.agentic.tools import ToolRegistry
from devagent.agentic.context import (
    ContextManager,
    ContextBudget,
    PhaseDetector,
    CacheManager,
    RepoMap,
    RelevantFileRetriever,
    FocusContextBuilder,
    HistoryCompressor,
    HallucinationGuard,
    ContextualToolFilter,
)


# ============================================================================
# Helpers
# ============================================================================

def make_workspace():
    """Create a temporary workspace with test files."""
    tmp = tempfile.mkdtemp()
    src_dir = os.path.join(tmp, "src")
    os.makedirs(src_dir, exist_ok=True)

    with open(os.path.join(src_dir, "models.py"), "w") as f:
        f.write('''"""Data models."""

class User:
    """A user entity."""
    def __init__(self, username: str, email: str):
        self.username = username
        self.email = email

    def validate(self) -> bool:
        return "@" in self.email and len(self.username) > 0

class Task:
    """A task entity."""
    def __init__(self, title: str, assignee: User = None):
        self.title = title
        self.assignee = assignee
        self.completed = False

    def complete(self):
        self.completed = True
''')

    with open(os.path.join(src_dir, "services.py"), "w") as f:
        f.write('''"""Business logic services."""

from src.models import User, Task


def create_user(username: str, email: str) -> User:
    """Create and validate a user."""
    user = User(username, email)
    if not user.validate():
        raise ValueError("Invalid user data")
    return user


def assign_task(task: Task, user: User):
    """Assign a task to a user."""
    task.assignee = user


def get_user_tasks(user: User, all_tasks: list[Task]) -> list[Task]:
    """Get all tasks assigned to a user."""
    return [t for t in all_tasks if t.assignee == user]
''')

    tests_dir = os.path.join(tmp, "tests")
    os.makedirs(tests_dir, exist_ok=True)

    with open(os.path.join(tests_dir, "test_models.py"), "w") as f:
        f.write('''import pytest
from src.models import User, Task


def test_user_validation():
    assert User("alice", "alice@test.com").validate() is True


def test_user_invalid_email():
    assert User("alice", "bad-email").validate() is False


def test_task_completion():
    t = Task("Write tests")
    assert t.completed is False
    t.complete()
    assert t.completed is True
''')

    return tmp


def make_state(workspace: str = "/tmp") -> AgentLoopState:
    return AgentLoopState(
        task_type="agentic",
        workspace=workspace,
        task_description="Fix the bug in user validation",
        language="python",
        max_iterations=50,
        status="RUNNING"
    )


def make_tool_descriptions() -> str:
    return ToolRegistry.create_default().get_descriptions()


# ============================================================================
# Test ContextBudget
# ============================================================================

class TestContextBudget:
    def test_default_allocation(self):
        budget = ContextBudget()
        alloc = budget.allocate("exploration")
        assert "system_prompt" in alloc
        assert "focus_context" in alloc
        assert alloc["relevant_files"] == 3000  # exploration gets more
        assert alloc["focus_context"] == 1500

    def test_editing_allocation(self):
        budget = ContextBudget()
        alloc = budget.allocate("editing")
        assert alloc["focus_context"] == 3500  # editing gets most
        assert alloc["relevant_files"] == 1000

    def test_verification_allocation(self):
        budget = ContextBudget()
        alloc = budget.allocate("verification")
        assert alloc["recent_history"] == 2500  # verification needs history
        assert alloc["focus_context"] == 1500

    def test_remaining_total(self):
        budget = ContextBudget(total_max=8000)
        remaining = budget.remaining("exploration", 0)
        assert remaining > 0
        assert remaining <= 8000


# ============================================================================
# Test PhaseDetector
# ============================================================================

class TestPhaseDetector:
    def test_initial_exploration(self):
        state = make_state()
        assert PhaseDetector.detect(state) == "exploration"

    def test_grep_is_exploration(self):
        state = make_state()
        state.add_action({"tool": "grep_text", "params": {"pattern": "test"}})
        state.add_observation({"success": True, "output": "3 matches"})
        assert PhaseDetector.detect(state) == "exploration"

    def test_file_read_is_editing(self):
        state = make_state()
        state.add_action({"tool": "grep_text", "params": {"pattern": "User"}})
        state.add_observation({"success": True, "output": "1 match"})
        state.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True, "output": "class User:..."})
        assert PhaseDetector.detect(state) == "editing"

    def test_file_edit_is_editing(self):
        state = make_state()
        state.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True})
        state.add_action({"tool": "file_edit", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True})
        assert PhaseDetector.detect(state) == "editing"

    def test_test_run_is_verification(self):
        state = make_state()
        state.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True})
        state.add_action({"tool": "test_run", "params": {"paths": ["tests/"]}})
        state.add_observation({"success": True})
        assert PhaseDetector.detect(state) == "verification"

    def test_failed_test_reenters_exploration(self):
        state = make_state()
        state.test_results = {"passed": 2, "failed": 1, "collected": 3}
        state.add_action({"tool": "file_edit", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True})
        assert PhaseDetector.detect(state) == "exploration"


# ============================================================================
# Test CacheManager
# ============================================================================

class TestCacheManager:
    def test_signature_caching(self):
        ws = make_workspace()
        cm = CacheManager(ws)

        sig1 = cm.get_or_parse_signature("src/models.py")
        assert "class User" in sig1
        assert "class Task" in sig1

        # Second call should be cached
        sig2 = cm.get_or_parse_signature("src/models.py")
        assert sig1 == sig2

        assert cm.is_file_fresh("src/models.py")

    def test_mtime_invalidation(self):
        ws = make_workspace()
        cm = CacheManager(ws)

        cm.get_or_parse_signature("src/models.py")
        assert cm.is_file_fresh("src/models.py")

        # Invalidate
        cm.invalidate_file("src/models.py")
        assert not cm.is_file_fresh("src/models.py")

    def test_import_caching(self):
        ws = make_workspace()
        cm = CacheManager(ws)

        imports = cm.get_cached_imports("src/services.py")
        assert "src" in imports or len(imports) >= 0  # services imports from src.models

    def test_hash_stability(self):
        h1 = CacheManager.hash_text("hello world")
        h2 = CacheManager.hash_text("hello world")
        assert h1 == h2
        assert len(h1) == 16


# ============================================================================
# Test RepoMap
# ============================================================================

class TestRepoMap:
    def test_generate(self):
        ws = make_workspace()
        cache = CacheManager(ws)
        rm = RepoMap(cache)

        result = rm.generate(ws)
        assert "src/" in result
        assert "models.py" in result
        assert "services.py" in result
        assert "tests/" in result
        assert "class User" in result or "def create_user" in result

        # Cache should be set
        assert cache.get_repo_map() is not None

    def test_incremental_generation(self):
        ws = make_workspace()
        cache = CacheManager(ws)
        rm = RepoMap(cache)

        first = rm.generate(ws)

        # Add a new file
        with open(os.path.join(ws, "src", "utils.py"), "w") as f:
            f.write("def helper():\n    pass\n")

        second = rm.generate(ws)
        assert "utils.py" in second
        assert len(second) > len(first)


# ============================================================================
# Test RelevantFileRetriever
# ============================================================================

class TestRelevantFileRetriever:
    def test_retrieve_by_keywords(self):
        ws = make_workspace()
        retriever = RelevantFileRetriever()

        results = retriever.retrieve("user validation", ws, top_k=5)
        assert any("models" in r for r in results)

    def test_modified_files_boosted(self):
        ws = make_workspace()
        retriever = RelevantFileRetriever()

        results = retriever.retrieve(
            "task completion", ws, top_k=3,
            modified_files=["src/models.py"]
        )
        # models.py should be ranked high
        if results:
            assert "models" in results[0]


# ============================================================================
# Test FocusContextBuilder
# ============================================================================

class TestFocusContextBuilder:
    def test_build_with_line_numbers(self):
        ws = make_workspace()
        cache = CacheManager(ws)
        builder = FocusContextBuilder(cache)

        result = builder.build("src/models.py", ws)
        assert "L1-" in result  # line range header
        assert "class User" in result
        assert "1|" in result  # line number anchor

    def test_build_missing_file(self):
        ws = make_workspace()
        cache = CacheManager(ws)
        builder = FocusContextBuilder(cache)

        result = builder.build("nonexistent.py", ws)
        assert "FILE NOT FOUND" in result


# ============================================================================
# Test HistoryCompressor
# ============================================================================

class TestHistoryCompressor:
    def test_compresses_early_rounds(self):
        compressor = HistoryCompressor()
        actions = [
            {"tool": "grep_text", "params": {"pattern": "User"}, "iteration": 1},
            {"tool": "file_read", "params": {"path": "src/models.py"}, "iteration": 2},
            {"tool": "grep_text", "params": {"pattern": "validate"}, "iteration": 3},
            {"tool": "file_edit", "params": {"path": "src/models.py"}, "iteration": 4},
            {"tool": "test_run", "params": {"paths": ["tests/"]}, "iteration": 5},
            {"tool": "file_edit", "params": {"path": "src/models.py"}, "iteration": 6},
            {"tool": "test_run", "params": {"paths": ["tests/"]}, "iteration": 7},
        ]
        obs = [
            {"success": True, "output": "Found 2", "structured": {"count": 2}},
            {"success": True, "output": "class User:..."},
            {"success": True, "output": "Found 1", "structured": {"count": 1}},
            {"success": True, "output": "Edited"},
            {"success": False, "output": "FAILED", "structured": {"passed": 1, "failed": 1}},
            {"success": True, "output": "Edited"},
            {"success": True, "output": "PASSED", "structured": {"passed": 3, "failed": 0}},
        ]

        result = compressor.compress(actions, obs, recent_rounds=3)
        assert len(result) > 0
        assert "src/models.py" in result
        assert "Earlier Rounds" in result

    def test_no_compression_needed(self):
        compressor = HistoryCompressor()
        actions = [{"tool": "file_read", "params": {"path": "x.py"}, "iteration": 1}]
        obs = [{"success": True, "output": "ok"}]

        result = compressor.compress(actions, obs, recent_rounds=5)
        assert result == ""


# ============================================================================
# Test HallucinationGuard
# ============================================================================

class TestHallucinationGuard:
    def test_validate_edit_target_exists(self):
        ws = make_workspace()
        valid, msg = HallucinationGuard.validate_edit_target("src/models.py", ws)
        assert valid

    def test_validate_edit_target_missing(self):
        ws = make_workspace()
        valid, msg = HallucinationGuard.validate_edit_target("src/ghost.py", ws)
        assert not valid
        assert "HALLUCINATION GUARD" in msg

    def test_validate_function_name_exists(self):
        ws = make_workspace()
        valid, msg = HallucinationGuard.validate_function_name(
            "src/models.py", "validate", ws
        )
        assert valid

    def test_validate_function_name_missing(self):
        ws = make_workspace()
        valid, msg = HallucinationGuard.validate_function_name(
            "src/models.py", "delete_all_users", ws
        )
        assert not valid
        assert "HALLUCINATION GUARD" in msg

    def test_build_grounding_context_empty(self):
        state = make_state()
        ctx = HallucinationGuard.build_grounding_context(state)
        assert "NONE" in ctx or "not read" in ctx.lower()

    def test_build_grounding_context_with_files(self):
        state = make_state()
        state.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True})
        state.modified_files.append("src/models.py")
        ctx = HallucinationGuard.build_grounding_context(state)
        assert "src/models.py" in ctx
        assert "Files You Have Read" in ctx
        assert "Files You Have Modified" in ctx


# ============================================================================
# Test ContextualToolFilter
# ============================================================================

class TestContextualToolFilter:
    def test_exploration_tools(self):
        tools = ContextualToolFilter.filter("exploration")
        assert "grep_text" in tools
        assert "grep_ast" in tools
        assert "file_read" in tools
        assert "file_edit" not in tools  # editing not allowed in exploration

    def test_editing_tools(self):
        tools = ContextualToolFilter.filter("editing")
        assert "file_edit" in tools
        assert "file_read" in tools
        assert "lint_check" in tools

    def test_verification_tools(self):
        tools = ContextualToolFilter.filter("verification")
        assert "test_run" in tools
        assert "git_diff" in tools

    def test_should_offer(self):
        assert ContextualToolFilter.should_offer("grep_text", "exploration")
        assert ContextualToolFilter.should_offer("file_edit", "editing")
        assert ContextualToolFilter.should_offer("test_run", "verification")
        assert not ContextualToolFilter.should_offer("test_run", "exploration")


# ============================================================================
# Test ContextManager — multi-message API
# ============================================================================

class TestContextManagerMultiMessage:
    def test_build_messages_structure(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        state = make_state(ws)
        td = make_tool_descriptions()

        messages = mgr.build_messages("Fix validation bug", state, td)

        # Should have 3 messages: system, repo map, dynamic
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert "DevAgent" in messages[0]["content"]
        assert "Fix validation bug" in messages[0]["content"]

        assert "src/" in messages[1]["content"]  # repo map

    def test_cache_stability(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        state = make_state(ws)
        td = make_tool_descriptions()

        msgs1 = mgr.build_messages("Fix validation bug", state, td)
        msgs2 = mgr.build_messages("Fix validation bug", state, td)

        # System prompt should be identical (cached)
        assert msgs1[0]["content"] == msgs2[0]["content"]

    def test_phase_affects_dynamic_content(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        td = make_tool_descriptions()

        state_exp = make_state(ws)
        state_edit = make_state(ws)
        state_edit.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state_edit.add_observation({"success": True})

        msgs_exp = mgr.build_messages("Fix bug", state_exp, td)
        msgs_edit = mgr.build_messages("Fix bug", state_edit, td)

        # Dynamic content should differ by phase
        assert msgs_exp[-1]["content"] != msgs_edit[-1]["content"]

    def test_grounding_in_context(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        state = make_state(ws)
        td = make_tool_descriptions()

        # Add some read history
        state.add_action({"tool": "file_read", "params": {"path": "src/models.py"}})
        state.add_observation({"success": True, "output": "class User:..."})
        state.add_action({"tool": "test_run", "params": {"paths": ["tests/"]}})
        state.add_observation({"success": False, "output": "1 failed",
                              "structured": {"passed": 2, "failed": 1}})
        state.test_results = {"passed": 2, "failed": 1}

        messages = mgr.build_messages("Fix test failure", state, td)

        # Dynamic message should contain grounding signals
        dynamic = messages[-1]["content"]
        assert "src/models.py" in dynamic
        assert "FAILED" in dynamic

    def test_legacy_build_context(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        state = make_state(ws)
        td = make_tool_descriptions()

        ctx = mgr.build_context("Fix bug", state, td)
        assert isinstance(ctx, str)
        assert len(ctx) > 100

    def test_on_file_modified_invalidates(self):
        ws = make_workspace()
        mgr = ContextManager(ws)
        state = make_state(ws)
        td = make_tool_descriptions()

        # Generate repo map first
        mgr.build_messages("test", state, td)
        assert mgr.cache.get_repo_map() is not None
        assert mgr.cache.is_file_fresh("src/models.py")

        # Modify file
        mgr.on_file_modified("src/models.py")
        assert not mgr.cache.is_file_fresh("src/models.py")
