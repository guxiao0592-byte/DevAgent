"""Tests for LangGraph-compatible StateGraph implementation."""

import sys, os, asyncio, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devagent.agentic.state_graph import (
    StateGraph, GraphState, Command, CheckpointManager,
    SubgraphNode, build_devagent_graph,
)


class TestGraphState:
    def test_defaults(self):
        s = GraphState()
        assert s.iteration == 0
        assert s.messages == []

    def test_merge(self):
        s = GraphState()
        s2 = s.merge({"iteration": 5, "messages": [{"role": "user", "content": "hi"}]})
        assert s2.iteration == 5
        assert len(s2.messages) == 1
        assert s.iteration == 0  # Original unchanged

    def test_merge_preserves_existing(self):
        s = GraphState(files_modified=["a.py"])
        s2 = s.merge({"files_modified": ["b.py"], "metadata": {"key": "val"}})
        assert "a.py" in s2.files_modified
        assert "b.py" in s2.files_modified
        assert s2.metadata["key"] == "val"

    def test_to_dict_roundtrip(self):
        s = GraphState(iteration=3, files_modified=["x.py", "y.py"],
                       metadata={"phase": "editing"})
        d = s.to_dict()
        s2 = GraphState.from_dict(d)
        assert s2.iteration == 3
        assert s2.files_modified == ["x.py", "y.py"]


class TestCommand:
    def test_goto(self):
        cmd = Command(update={"iteration": 5}, goto="think")
        assert cmd.has_goto()
        assert cmd.goto == "think"

    def test_no_goto(self):
        cmd = Command(update={"iteration": 5})
        assert not cmd.has_goto()


class TestCheckpointManager:
    def test_save_and_load(self):
        tmp = tempfile.mkdtemp()
        ckpt = CheckpointManager(os.path.join(tmp, "checkpoints"))

        s = GraphState(iteration=3, files_modified=["a.py"])
        ckpt.save("thread_1", s, "act", 3)

        loaded = ckpt.load_latest("thread_1")
        assert loaded is not None
        assert loaded.iteration == 3
        assert loaded.files_modified == ["a.py"]

    def test_list_snapshots(self):
        tmp = tempfile.mkdtemp()
        ckpt = CheckpointManager(os.path.join(tmp, "checkpoints"))

        for i in range(5):
            s = GraphState(iteration=i)
            ckpt.save("thread_1", s, f"node_{i}", i)

        snapshots = ckpt.list_snapshots("thread_1")
        assert len(snapshots) == 5

    def test_fork(self):
        tmp = tempfile.mkdtemp()
        ckpt = CheckpointManager(os.path.join(tmp, "checkpoints"))

        s = GraphState(iteration=5, files_modified=["a.py"])
        ckpt.save("thread_1", s, "act", 5)

        ok = ckpt.fork("thread_1", 5, "thread_2")
        assert ok

        forked = ckpt.load_latest("thread_2")
        assert forked is not None
        assert forked.iteration == 5

    def test_load_nonexistent(self):
        ckpt = CheckpointManager()
        state = ckpt.load_latest("nonexistent")
        assert state is None


class TestStateGraph:
    async def _run_simple_graph(self):
        """Build and run a minimal graph with conditional branching."""
        graph = StateGraph()

        async def start_node(state):
            return {"iteration": 1, "messages": [{"role": "system", "content": "started"}]}

        async def middle_node(state):
            return {"iteration": state.iteration + 1}

        async def end_node(state):
            return {"messages": [{"role": "system", "content": "done"}]}

        def router(state):
            return "go_end" if state.iteration >= 2 else "go_middle"

        graph.add_node("start", start_node)
        graph.add_node("middle", middle_node)
        graph.add_node("end", end_node)
        graph.set_entry_point("start")
        graph.add_edge("start", "middle")
        graph.add_conditional_edges("middle", router, {
            "go_middle": "middle",
            "go_end": "end",
        })
        graph.add_edge("end", StateGraph.END)

        result = await graph.ainvoke()
        return result

    def test_simple_graph(self):
        result = asyncio.run(self._run_simple_graph())
        assert result.messages[-1]["content"] == "done"
        assert result.iteration >= 2

    async def _run_graph_with_checkpoint(self):
        tmp = tempfile.mkdtemp()
        graph = StateGraph()
        graph.thread_id = "test_thread"
        graph.checkpointer = CheckpointManager(os.path.join(tmp, "ckpt"))

        async def step(state):
            return {"iteration": state.iteration + 1}

        def router(state):
            return "done" if state.iteration >= 3 else "loop"

        graph.add_node("step", step)
        graph.set_entry_point("step")
        graph.add_conditional_edges("step", router, {
            "loop": "step", "done": StateGraph.END
        })

        result = await graph.ainvoke()
        return result, graph

    def test_graph_with_checkpoint(self):
        result, graph = asyncio.run(self._run_graph_with_checkpoint())
        assert result.iteration == 3

        # Verify checkpoint was saved
        loaded = graph.checkpointer.load_latest("test_thread")
        assert loaded is not None


class TestSubgraphNode:
    async def _run_subgraph(self):
        child = StateGraph()

        async def child_step(state):
            return {"files_modified": ["child_file.py"],
                    "metadata": {"child_done": True}}

        child.add_node("work", child_step)
        child.set_entry_point("work")
        child.add_edge("work", StateGraph.END)

        parent = StateGraph()
        sub = SubgraphNode(child, state_filter=["files_modified"])
        parent.add_node("delegate", sub.execute)
        parent.set_entry_point("delegate")
        parent.add_edge("delegate", StateGraph.END)

        return await parent.ainvoke()

    def test_subgraph(self):
        result = asyncio.run(self._run_subgraph())
        assert "child_file.py" in result.files_modified
        assert result.metadata.get("subgraph_completed") is True


class TestBuildDevAgentGraph:
    def test_build_and_mermaid(self):
        graph = build_devagent_graph()
        mmd = graph.mermaid()
        assert "stateDiagram" in mmd
        assert "think" in mmd
        assert "fault_loc" in mmd
        assert "submit" in mmd


# ============================================================================
# P1 Tests: Messages, ToolNode, StateMigrator, GraphAgentCore
# ============================================================================

from devagent.agentic.state_graph import (
    ToolMessage, AIMessage, HumanMessage, BaseMessage,
    ToolNode, StateMigrator, GraphAgentCore,
)


class TestMessages:
    def test_tool_message(self):
        tm = ToolMessage(content="Result: 42", tool_name="shell_run",
                        tool_call_id="call_1",
                        metadata={"success": True})
        d = tm.to_dict()
        assert d["role"] == "tool"
        assert d["content"] == "Result: 42"
        assert d["name"] == "shell_run"

    def test_ai_message(self):
        am = AIMessage(content="I will fix this",
                       tool_calls=[{"name": "file_edit", "arguments": {}}])
        d = am.to_dict()
        assert d["role"] == "assistant"

    def test_human_message(self):
        hm = HumanMessage(content="Fix the login bug")
        d = hm.to_dict()
        assert d["role"] == "user"

    def test_base_message(self):
        bm = BaseMessage(content="System prompt", role="system", name="planner")
        d = bm.to_dict()
        assert d["role"] == "system"


class TestToolNode:
    def test_tool_node_creation(self):
        """ToolNode should be creatable with tools parameter."""
        node = ToolNode(tools=None, parallel=True)
        assert node.parallel is True
        assert node.tools is None

    async def _test_execute_no_tool_calls(self):
        node = ToolNode(tools=None)
        state = GraphState(messages=[
            {"role": "assistant", "content": "no tool calls here"}
        ])
        result = await node.execute(state)
        assert "errors" in result or "No pending tool calls" in str(result)
        return result

    def test_execute_no_pending(self):
        result = asyncio.run(self._test_execute_no_tool_calls())
        assert isinstance(result, dict)


class TestStateMigrator:
    def test_add_and_migrate(self):
        migrator = StateMigrator()

        # Migration: v1 → v2 adds new field
        def v1_to_v2(state: dict) -> dict:
            s = dict(state)
            s["metadata"] = dict(s.get("metadata", {}))
            s["metadata"]["new_field"] = "migrated"
            s["metadata"]["schema_version"] = 2
            return s

        migrator.add_migration(1, 2, v1_to_v2)

        state = GraphState(metadata={"schema_version": 1, "key": "val"})
        migrated = migrator.migrate(state, target_version=2)

        assert migrated.metadata.get("schema_version") == 2
        assert migrated.metadata.get("new_field") == "migrated"
        assert migrated.metadata.get("migrated_from") == 1

    def test_no_migration_needed(self):
        migrator = StateMigrator()
        state = GraphState(metadata={"schema_version": 3})
        result = migrator.migrate(state, target_version=3)
        assert result.metadata["schema_version"] == 3

    def test_no_path_raises(self):
        migrator = StateMigrator()
        state = GraphState(metadata={"schema_version": 1})
        try:
            migrator.migrate(state, target_version=99)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestGraphAgentCore:
    def test_build_graph_structure(self):
        """GraphAgentCore should build a valid StateGraph."""
        agent = GraphAgentCore(workspace="/tmp")
        graph = agent.build_graph("Test task", max_iterations=10)

        mmd = graph.mermaid()
        assert "think" in mmd
        assert "act" in mmd
        assert graph.checkpointer is not None

    def test_build_graph_has_entry(self):
        agent = GraphAgentCore(workspace="/tmp")
        graph = agent.build_graph("test")
        assert graph._entry_point == "think"
        assert "think" in graph._nodes
        assert "act" in graph._nodes

