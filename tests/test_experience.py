"""Tests for Experience Library — cross-task learning."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.experience import (
    Experience, ExperienceStore, ExperienceInjector, ExperienceRecorder,
)


def make_store():
    tmp = tempfile.mkdtemp()
    return ExperienceStore(os.path.join(tmp, "experience"))


class TestExperience:
    def test_to_dict(self):
        exp = Experience(
            id="exp_001", type="bug_fix",
            bug_signature="ZeroDivisionError in divide()",
            error_type="ZeroDivisionError",
            fix_description="Added zero check before division",
            fix_patch="-    return a / b\n+    if b == 0:\n+        raise ValueError\n+    return a / b",
            iterations_needed=5,
            task_id="task_001",
        )
        d = exp.to_dict()
        assert d["id"] == "exp_001"
        assert d["error_type"] == "ZeroDivisionError"
        assert d["success"] is True

    def test_signature_hash(self):
        e1 = Experience(id="e1", error_type="ZeroDivisionError",
                       bug_signature="division by zero in calculate")
        e2 = Experience(id="e2", error_type="ZeroDivisionError",
                       bug_signature="division by zero in calculate")
        assert e1.signature_hash() == e2.signature_hash()

    def test_from_dict(self):
        data = {
            "id": "exp_x", "type": "bug_fix",
            "bug_signature": "test bug",
            "error_type": "ValueError",
            "success": False, "iterations_needed": 10
        }
        exp = Experience.from_dict(data)
        assert exp.id == "exp_x"
        assert exp.error_type == "ValueError"
        assert not exp.success


class TestExperienceStore:
    def test_add_and_retrieve(self):
        store = make_store()
        exp = Experience(
            id="exp_001", type="bug_fix",
            bug_signature="ZeroDivisionError in divide()",
            error_type="ZeroDivisionError",
            fix_description="Added zero check",
        )
        store.add(exp)

        results = store.retrieve({
            "error_type": "ZeroDivisionError",
            "error_message": "division by zero",
            "task": "fix divide by zero bug",
        })
        assert len(results) >= 1
        assert results[0].id == "exp_001"

    def test_retrieve_no_match(self):
        store = make_store()
        results = store.retrieve({"error_type": "UnknownError"})
        assert len(results) == 0  # Below relevance threshold

    def test_retrieve_by_keyword(self):
        store = make_store()
        store.add(Experience(
            id="e1", type="bug_fix",
            bug_signature="AttributeError: user is None in get_display_name",
            error_type="AttributeError",
            fix_description="Added null check before accessing user.name",
        ))
        results = store.retrieve({
            "error_type": "AttributeError",
            "error_message": "NoneType has no attribute",
            "task": "fix null pointer in user display",
        })
        assert len(results) >= 1

    def test_deduplication(self):
        store = make_store()
        e1 = Experience(id="e1", error_type="ZeroDivisionError",
                       bug_signature="divide by zero")
        e2 = Experience(id="e2", error_type="ZeroDivisionError",
                       bug_signature="divide by zero")  # Same signature
        store.add(e1)
        store.add(e2)
        assert store.size() == 1  # Deduplicated

    def test_project_match_boosts_score(self):
        store = make_store()
        store.add(Experience(id="e1", error_type="TypeError",
                            bug_signature="type mismatch", project="myapp"))
        store.add(Experience(id="e2", error_type="TypeError",
                            bug_signature="type mismatch", project="other"))

        results = store.retrieve({
            "error_type": "TypeError",
            "project": "myapp",
        })
        # e1 (project=myapp) should rank higher / be present
        assert any(r.id == "e1" for r in results)


class TestExperienceInjector:
    def test_inject_adds_message(self):
        injector = ExperienceInjector()
        exp = Experience(
            id="e1", error_type="ZeroDivisionError",
            bug_signature="divide by zero",
            fix_description="Added zero check",
            fix_patch="- return a / b\n+ if b == 0: return None\n+ return a / b",
        )
        messages = [{"role": "system", "content": "System prompt"}]
        new_messages = injector.inject([exp], messages)
        assert len(new_messages) > len(messages)
        # Should contain the experience info
        assert "ZeroDivisionError" in new_messages[-1]["content"]

    def test_inject_empty(self):
        injector = ExperienceInjector()
        messages = [{"role": "system", "content": "test"}]
        new_messages = injector.inject([], messages)
        assert len(new_messages) == len(messages)


class TestExperienceRecorder:
    def test_extract_error_type(self):
        # Verify the method is callable
        assert callable(ExperienceRecorder._extract_error_type)

    def test_extract_tags(self):
        from devagent.agentic.state import AgentLoopState
        state = AgentLoopState(
            workspace="/tmp/test", task_description="fix bug",
            language="python"
        )
        state.add_action({"tool": "grep_text", "params": {"pattern": "divide"}})
        state.add_observation({"success": True, "output": "found"})
        state.add_action({"tool": "file_edit", "params": {"path": "src/math_ops.py"}})
        state.add_observation({"success": True, "output": "edited"})

        tags = ExperienceRecorder._extract_tags(state)
        assert "math_ops" in tags
