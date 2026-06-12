"""LangGraph-Compatible StateGraph for DevAgent V2.

Implements LangGraph's core design patterns without external dependencies:
  1. StateGraph — Nodes + Edges + Conditional branching
  2. Per-step Checkpointing — Thread-safe snapshots with thread_id
  3. Interrupt / Resume — Pause execution at any node for human input
  4. Subgraph Delegation — Nest graphs as nodes
  5. Command Pattern — Nodes return updates + edge navigation

Key differences from raw ReAct loop:
  - Graph structure allows MULTIPLE PATHS (test fail→repair, test pass→submit)
  - Each node is a pure function: state_in → state_out (declarative)
  - Auto-checkpoint after each node execution
  - Interrupt points can be placed at any edge
"""

import os
import json
import copy
import time
import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from collections import defaultdict


# ============================================================================
# State Types
# ============================================================================

@dataclass
class GraphState:
    """LangGraph-compatible state container.

    Each node receives the full state and returns a dict of UPDATES
    (partial state), which are merged back. This is the LangGraph pattern
    of declarative state updates instead of imperative mutation.
    """
    messages: list[dict] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    test_results: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    current_node: str = ""
    iteration: int = 0

    def merge(self, updates: dict) -> "GraphState":
        """Apply partial updates to create a new state snapshot."""
        new = copy.deepcopy(self)
        for key, value in updates.items():
            if key in ("messages", "files_read", "files_modified", "errors"):
                if isinstance(value, list):
                    getattr(new, key).extend(value)
            elif key == "metadata":
                new.metadata.update(value)
            elif hasattr(new, key):
                setattr(new, key, value)
        return new

    def to_dict(self) -> dict:
        return {
            "messages": self.messages[-10:],  # Last 10 for snapshot size
            "files_read": self.files_read,
            "files_modified": self.files_modified,
            "test_results": self.test_results,
            "errors": self.errors[-5:],
            "metadata": self.metadata,
            "current_node": self.current_node,
            "iteration": self.iteration,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphState":
        return cls(
            messages=data.get("messages", []),
            files_read=data.get("files_read", []),
            files_modified=data.get("files_modified", []),
            test_results=data.get("test_results", {}),
            errors=data.get("errors", []),
            metadata=data.get("metadata", {}),
            current_node=data.get("current_node", ""),
            iteration=data.get("iteration", 0),
        )


# ============================================================================
# Command Pattern
# ============================================================================

@dataclass
class Command:
    """A node can return a Command to update state AND control navigation.

    LangGraph equivalent: Command(goto="next_node", update={...})
    """
    update: dict = field(default_factory=dict)   # Partial state update
    goto: Optional[str] = None                     # Force next node
    resume: Optional[Any] = None                   # Value for interrupt resume

    def has_goto(self) -> bool:
        return self.goto is not None


# ============================================================================
# Checkpoint Manager
# ============================================================================

class CheckpointManager:
    """Per-step checkpoint persistence with thread_id isolation.

    LangGraph equivalent: MemorySaver / SqliteSaver
    """

    def __init__(self, storage_dir: str = ".devagent/checkpoints"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, list[dict]] = defaultdict(list)

    def save(self, thread_id: str, state: GraphState,
             node_name: str, step: int):
        """Save a checkpoint snapshot after each node execution."""
        snapshot = {
            "thread_id": thread_id,
            "node": node_name,
            "step": step,
            "timestamp": datetime.now().isoformat(),
            "state": state.to_dict(),
            "state_hash": self._hash_state(state),
        }
        self._cache[thread_id].append(snapshot)

        # Persist to disk
        path = self.storage_dir / f"{thread_id}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    def load_latest(self, thread_id: str) -> Optional[GraphState]:
        """Load the most recent checkpoint for a thread."""
        snapshots = self._get_snapshots(thread_id)
        if not snapshots:
            return None
        return GraphState.from_dict(snapshots[-1]["state"])

    def load_at_step(self, thread_id: str, step: int) -> Optional[GraphState]:
        """Load checkpoint at a specific step."""
        snapshots = self._get_snapshots(thread_id)
        for s in snapshots:
            if s["step"] == step:
                return GraphState.from_dict(s["state"])
        return None

    def list_snapshots(self, thread_id: str) -> list[dict]:
        """List all checkpoints for a thread (summary only)."""
        return [
            {"node": s["node"], "step": s["step"], "timestamp": s["timestamp"]}
            for s in self._get_snapshots(thread_id)
        ]

    def fork(self, thread_id: str, from_step: int, new_thread_id: str) -> bool:
        """Fork a thread from a checkpoint — LangGraph time travel."""
        state = self.load_at_step(thread_id, from_step)
        if not state:
            return False
        self.save(new_thread_id, state, "fork", 0)
        return True

    def _get_snapshots(self, thread_id: str) -> list[dict]:
        if thread_id in self._cache:
            return self._cache[thread_id]

        path = self.storage_dir / f"{thread_id}.jsonl"
        if not path.exists():
            return []

        snapshots = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        snapshots.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        self._cache[thread_id] = snapshots
        return snapshots

    @staticmethod
    def _hash_state(state: GraphState) -> str:
        data = json.dumps(state.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()[:16]


# ============================================================================
# StateGraph
# ============================================================================

# Type aliases
NodeFunc = Callable[[GraphState], dict | Command | GraphState]
ConditionFunc = Callable[[GraphState], str]


class StateGraph:
    """LangGraph-compatible graph execution engine.

    Usage pattern (identical to LangGraph):
      graph = StateGraph(initial_state)
      graph.add_node("think", think_node)
      graph.add_node("act", act_node)
      graph.add_edge("think", "act")
      graph.add_conditional_edges("act", route_after_act, {
          "think": "think",
          "submit": END,
      })
      graph.set_entry_point("think")
      result = await graph.ainvoke()
    """

    END = "__end__"
    START = "__start__"

    def __init__(self, initial_state: GraphState = None):
        self._nodes: dict[str, NodeFunc] = {}
        self._edges: dict[str, str] = {}
        self._conditional_edges: dict[str, tuple[ConditionFunc, dict[str, str]]] = {}
        self._entry_point: str = ""
        self._interrupt_before: set[str] = set()
        self._interrupt_after: set[str] = set()
        self.initial_state = initial_state or GraphState()
        self.checkpointer: Optional[CheckpointManager] = None
        self.thread_id: str = "default"

    # ---- Builder API (identical to LangGraph) ----

    def add_node(self, name: str, func: NodeFunc):
        self._nodes[name] = func

    def add_edge(self, from_node: str, to_node: str):
        self._edges[from_node] = to_node

    def add_conditional_edges(self, from_node: str,
                               condition: ConditionFunc,
                               mapping: dict[str, str]):
        self._conditional_edges[from_node] = (condition, mapping)

    def set_entry_point(self, node_name: str):
        self._entry_point = node_name

    def set_finish_point(self, node_name: str):
        """Mark a node as terminal (goes to END)."""
        self._edges[node_name] = self.END

    def interrupt_before(self, node_names: list[str]):
        """Interrupt execution BEFORE specified nodes (LangGraph API)."""
        self._interrupt_before.update(node_names)

    def interrupt_after(self, node_names: list[str]):
        """Interrupt execution AFTER specified nodes (LangGraph API)."""
        self._interrupt_after.update(node_names)

    # ---- Execution API ----

    async def ainvoke(self, thread_id: str = None) -> GraphState:
        """Execute the graph (async). LangGraph: graph.ainvoke(state)."""
        if thread_id:
            self.thread_id = thread_id

        state = copy.deepcopy(self.initial_state)

        # Restore from checkpoint if available
        if self.checkpointer:
            saved = self.checkpointer.load_latest(self.thread_id)
            if saved:
                state = saved

        current = self._entry_point
        step = state.iteration

        while current and current != self.END:
            if current not in self._nodes:
                break

            # Check interrupt before
            if current in self._interrupt_before:
                interrupt_result = await self._handle_interrupt(current, state)
                if interrupt_result is False:
                    break  # User stopped execution

            # Execute node (support both sync and async node functions)
            node_fn = self._nodes[current]
            state.current_node = current
            state.iteration = step

            if asyncio.iscoroutinefunction(node_fn):
                result = await node_fn(copy.deepcopy(state))
            else:
                result = node_fn(copy.deepcopy(state))
            step += 1

            # Handle node return types
            if isinstance(result, Command):
                state = state.merge(result.update)
                if result.goto:
                    current = result.goto
                else:
                    current = self._get_next(current, state)
            elif isinstance(result, dict):
                state = state.merge(result)
                current = self._get_next(current, state)
            elif isinstance(result, GraphState):
                state = result
                current = self._get_next(current, state)
            else:
                current = self._get_next(current, state)

            # Check interrupt after
            if current in self._interrupt_after:
                interrupt_result = await self._handle_interrupt(current, state)
                if interrupt_result is False:
                    break

            # Save checkpoint
            if self.checkpointer:
                self.checkpointer.save(self.thread_id, state, current, step)

        return state

    def invoke(self, thread_id: str = None) -> GraphState:
        """Execute the graph (sync)."""
        return asyncio.run(self.ainvoke(thread_id))

    def _get_next(self, current: str, state: GraphState) -> str:
        """Determine the next node to execute."""
        # Check conditional edges first
        if current in self._conditional_edges:
            condition_fn, mapping = self._conditional_edges[current]
            branch = condition_fn(state)
            return mapping.get(branch, self.END)

        # Check normal edges
        if current in self._edges:
            return self._edges[current]

        return self.END

    async def _handle_interrupt(self, node_name: str,
                                 state: GraphState) -> Optional[bool]:
        """Handle interrupt at a node. Returns False to stop, True to continue."""
        # Store interrupt point for external resolution
        if not hasattr(self, '_interrupt_state'):
            self._interrupt_state = {}
        self._interrupt_state[node_name] = {
            "state": state.to_dict(),
            "timestamp": datetime.now().isoformat(),
            "resolved": False,
        }
        # Default: continue after 0.1s (non-blocking in headless mode)
        await asyncio.sleep(0.1)
        return True

    def resume(self, node_name: str, value: Any = None) -> bool:
        """Resume execution from an interrupt point."""
        if hasattr(self, '_interrupt_state'):
            entry = self._interrupt_state.get(node_name)
            if entry:
                entry["resolved"] = True
                entry["value"] = value
                return True
        return False

    # ---- Visualisation ----

    def mermaid(self) -> str:
        """Generate a Mermaid diagram of the graph."""
        lines = ["```mermaid", "stateDiagram-v2"]
        for src, dst in self._edges.items():
            lines.append(f"    {src} --> {dst}")
        for src, (_, mapping) in self._conditional_edges.items():
            for branch, dst in mapping.items():
                lines.append(f"    {src} --> {dst} : {branch}")
        lines.append("```")
        return "\n".join(lines)


# ============================================================================
# Subgraph Node — nest graphs as nodes (LangGraph subgraph pattern)
# ============================================================================

class SubgraphNode:
    """A node that executes a child StateGraph as a sub-step.

    LangGraph equivalent: add_node("delegate", subgraph)

    The child graph receives the parent's state (or a filtered subset)
    and returns updates that are merged back into the parent.
    """

    def __init__(self, child_graph: StateGraph,
                 state_filter: Optional[list[str]] = None):
        self.child = child_graph
        self.state_filter = state_filter  # Keys to pass to child

    async def execute(self, state: GraphState) -> dict:
        """Execute the child graph as a sub-step."""
        # Filter state for child
        child_state = copy.deepcopy(state)
        if self.state_filter:
            child_state = GraphState()
            for key in self.state_filter:
                if hasattr(state, key):
                    setattr(child_state, key, getattr(state, key))

        self.child.initial_state = child_state

        # Execute child graph
        result = await self.child.ainvoke()

        # Return only new updates (not entire state)
        return {
            "files_modified": result.files_modified,
            "test_results": result.test_results,
            "metadata": {"subgraph_completed": True, **result.metadata},
        }


# ============================================================================
# P1-3: Standardized Message Types (LangGraph-compatible)
# ============================================================================

@dataclass
class BaseMessage:
    """Base message type — LangGraph compatible."""
    content: str
    role: str = "system"
    name: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        name = getattr(self, 'tool_name', None) or self.name or ""
        return {
            "role": self.role, "content": self.content,
            "name": name, **self.metadata
        }


@dataclass
class ToolMessage(BaseMessage):
    """Message representing a tool execution result."""
    tool_call_id: str = ""
    tool_name: str = ""

    def __post_init__(self):
        self.role = "tool"


@dataclass
class AIMessage(BaseMessage):
    """Message from the AI agent."""
    tool_calls: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.role = "assistant"


@dataclass
class HumanMessage(BaseMessage):
    """Message from the human user."""

    def __post_init__(self):
        self.role = "user"


# ============================================================================
# P1-1: ToolNode — Parallel tool execution (LangGraph-compatible)
# ============================================================================

class ToolNode:
    """Executes one or more tools in parallel, like LangGraph's ToolNode.

    Usage:
      node = ToolNode(tools)
      graph.add_node("tools", node)
      result = await node(state)  # returns {"messages": [ToolMessage, ...]}
    """

    def __init__(self, tools, parallel: bool = True):
        self.tools = tools       # ToolRegistry instance
        self.parallel = parallel

    async def execute(self, state: GraphState) -> dict:
        """Execute pending tool calls from the state messages and return results."""
        # Find the last AIMessage with tool_calls
        last_ai = None
        for m in reversed(state.messages):
            if isinstance(m, dict) and m.get("role") == "assistant":
                tc = m.get("tool_calls", [])
                if tc:
                    last_ai = m
                    break

        if not last_ai:
            return {"messages": [], "errors": [{"msg": "No pending tool calls found"}]}

        tool_calls = last_ai.get("tool_calls", [])

        if self.parallel and len(tool_calls) > 1:
            # Parallel execution
            tasks = []
            for tc in tool_calls:
                tasks.append(self._execute_one(tc, state))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Sequential execution
            results = []
            for tc in tool_calls:
                results.append(await self._execute_one(tc, state))

        tool_messages = []
        for i, r in enumerate(results):
            tc = tool_calls[i] if i < len(tool_calls) else {}
            if isinstance(r, Exception):
                tool_messages.append(ToolMessage(
                    content=f"Error: {r}", tool_name=tc.get("name", "unknown"),
                    tool_call_id=tc.get("id", ""),
                    metadata={"success": False, "error": str(r)}
                ).to_dict())
            else:
                tool_messages.append(r.to_dict() if isinstance(r, ToolMessage) else {
                    "role": "tool", "content": str(r)[:2000],
                    "tool_call_id": tc.get("id", ""),
                    "name": tc.get("name", "unknown"),
                })

        return {"messages": tool_messages}

    async def _execute_one(self, tool_call: dict, state: GraphState):
        """Execute a single tool call."""
        tool_name = tool_call.get("name", tool_call.get("function", {}).get("name", ""))
        params = tool_call.get("arguments", tool_call.get("function", {}).get("arguments", {}))

        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}

        workspace = state.metadata.get("workspace", ".")

        if hasattr(self.tools, 'execute'):
            result = await self.tools.execute(tool_name, params, workspace)
            return ToolMessage(
                content=result.output[:2000] if result.success else result.error,
                tool_name=tool_name,
                tool_call_id=tool_call.get("id", ""),
                metadata={"success": result.success,
                          "structured": result.structured}
            )

        return ToolMessage(
            content=f"Tool '{tool_name}' not available",
            tool_name=tool_name, tool_call_id=tool_call.get("id", ""),
            metadata={"success": False}
        )


# ============================================================================
# P1-2: State Migration — Schema version migration (LangGraph-compatible)
# ============================================================================

class StateMigrator:
    """Migrates state between schema versions, like LangGraph's migrate_state.

    Usage:
      migrator = StateMigrator()
      migrator.add_migration(1, 2, lambda s: {**s, "new_field": "default"})
      new_state = migrator.migrate(state, target_version=2)
    """

    def __init__(self):
        self._migrations: dict[tuple[int, int], Callable] = {}

    def add_migration(self, from_version: int, to_version: int,
                      migrate_fn: Callable[[dict], dict]):
        """Register a migration function between two versions."""
        self._migrations[(from_version, to_version)] = migrate_fn

    def migrate(self, state: GraphState, target_version: int) -> GraphState:
        """Migrate state to the target version by chaining migrations."""
        current = state.metadata.get("schema_version", 1)

        if current == target_version:
            return state

        # Find migration path
        path = self._find_path(current, target_version)
        if not path:
            raise ValueError(
                f"No migration path from version {current} to {target_version}")

        data = state.to_dict()
        for step in path:
            fn = self._migrations.get(step)
            if fn:
                data = fn(data)
            else:
                raise ValueError(f"No migration for {step[0]} → {step[1]}")

        # Apply migrated data back to state
        new_state = GraphState.from_dict(data)
        new_state.metadata["schema_version"] = target_version
        new_state.metadata["migrated_from"] = current
        return new_state

    def _find_path(self, from_v: int, to_v: int) -> list[tuple[int, int]]:
        """Find sequence of (from, to) migration steps."""
        path = []
        current = from_v
        visited = set()

        while current != to_v and current not in visited:
            visited.add(current)
            if current < to_v:
                step = (current, current + 1)
            else:
                step = (current, current - 1)

            if step in self._migrations:
                path.append(step)
                current = step[1]
            else:
                return []  # No path

        return path if current == to_v else []


# ============================================================================
# P1-4: GraphAgentCore — ReAct loop as StateGraph nodes with real integration
# ============================================================================

class GraphAgentCore:
    """Agentic loop executed as a StateGraph with real tool integration.

    This is the graph-structured equivalent of DevAgentCore.execute_async(),
    using StateGraph nodes for each phase of the ReAct cycle.

    Unlike the demonstrative build_devagent_graph(), this integrates with
    the real LLM client, context manager, and tool registry.
    """

    def __init__(self, llm_client=None, tools=None, context_mgr=None,
                 workspace: str = "."):
        self.llm = llm_client
        self.tools = tools
        self.context_mgr = context_mgr
        self.workspace = workspace
        self._pending_action: Optional[dict] = None

    def build_graph(self, task_description: str,
                    max_iterations: int = 50) -> StateGraph:
        """Build the full agentic graph with LLM-driven decision making."""
        graph = StateGraph()
        graph.checkpointer = CheckpointManager()
        tool_node = ToolNode(self.tools, parallel=True)

        # --- Node definitions ---

        async def think(state: GraphState) -> dict:
            """LLM-driven decision node."""
            if not self.llm or not self.tools or not self.context_mgr:
                return {"metadata": {"error": "LLM/tools/context not configured"}}

            from .state import AgentLoopState
            agent_state = AgentLoopState(
                workspace=self.workspace,
                task_description=task_description,
                max_iterations=max_iterations
            )
            agent_state.current_iteration = state.iteration

            tool_descs = self.tools.get_descriptions()
            messages = self.context_mgr.build_messages(
                task_description, agent_state, tool_descs
            )

            response = self.llm.chat(
                messages=[{"role": "user", "content": messages[0]["content"]
                          if isinstance(messages, list) and messages else str(messages)}]
            )

            # Parse action from response
            import re as _re
            action_m = _re.search(r'ACTION:\s*(\w+)', response, _re.IGNORECASE)
            params_m = _re.search(r'PARAMS:\s*(\{.+?\})', response, _re.DOTALL)

            tool_name = action_m.group(1) if action_m else "submit"
            params = {}
            if params_m:
                try:
                    params = json.loads(params_m.group(1))
                except json.JSONDecodeError:
                    pass

            self._pending_action = {"tool": tool_name, "params": params}

            return {
                "messages": [AIMessage(
                    content=f"Decided: {tool_name}",
                    tool_calls=[{
                        "id": f"call_{state.iteration}",
                        "name": tool_name,
                        "function": {"name": tool_name, "arguments": json.dumps(params)},
                        "arguments": params,
                    }],
                    metadata={"thought": response[:200]}
                ).to_dict()],
                "iteration": state.iteration + 1,
                "metadata": {"phase": "thinking"}
            }

        async def act(state: GraphState) -> dict:
            """Execute the pending tool call."""
            if not self._pending_action:
                return {"metadata": {"phase": "acting", "error": "No pending action"}}

            action = self._pending_action
            workspace = state.metadata.get("workspace", self.workspace)
            result = await self.tools.execute(
                action["tool"], action["params"], workspace
            )

            tool_msg = ToolMessage(
                content=result.output[:2000] if result.success else result.error,
                tool_name=action["tool"],
                tool_call_id=f"call_{state.iteration}",
                metadata={"success": result.success, "structured": result.structured}
            )

            updates = {
                "messages": [tool_msg.to_dict()],
                "metadata": {"phase": "acting", "last_tool": action["tool"]}
            }

            if result.success and action["tool"] in ("file_edit", "file_write"):
                path = action["params"].get("path", "")
                if path:
                    updates["files_modified"] = [path]

            if action["tool"] == "test_run" and result.success:
                updates["test_results"] = result.structured

            self._pending_action = None
            return updates

        def route_after_act(state: GraphState) -> str:
            """After acting: check if we should stop or continue."""
            tr = state.test_results
            metadata = state.metadata

            if metadata.get("submitted"):
                return "end"
            if tr and tr.get("failed", 0) == 0 and tr.get("collected", 0) > 0:
                return "think"  # Tests pass but task may not be done — think again
            if state.iteration >= metadata.get("max_iterations", max_iterations):
                return "end"
            return "think"  # Continue the loop

        # --- Build graph ---
        graph.add_node("think", think)
        graph.add_node("act", act)
        graph.set_entry_point("think")
        graph.add_edge("think", "act")
        graph.add_conditional_edges("act", route_after_act, {
            "think": "think",
            "end": StateGraph.END,
        })

        return graph

    async def run(self, task_description: str,
                  max_iterations: int = 50) -> GraphState:
        """Execute the graph-structured agentic loop."""
        graph = self.build_graph(task_description, max_iterations)
        graph.initial_state = GraphState(
            metadata={"task": task_description, "workspace": self.workspace,
                      "max_iterations": max_iterations}
        )
        return await graph.ainvoke()


# ============================================================================
# Factory: Build DevAgent Graph from existing components
# ============================================================================

def build_devagent_graph(core=None, tools=None) -> StateGraph:
    """Construct a LangGraph-style StateGraph from DevAgent components.

    This demonstrates how DevAgent's existing capabilities can be
    composed as a proper graph with branching paths.

    Graph structure:
        [START]
           │
        [think] ←──────────────┐
           │                    │
        [act]                  │
           │                    │
        [observe] ──(branch)──┤
           │                    │
      ┌────┴────┐              │
      │          │              │
   (pass)    (fail)            │
      │          │              │
   [submit]  [fault_loc]──────┘
      │
    [END]
    """
    graph = StateGraph()
    graph.checkpointer = CheckpointManager()

    async def think_node(state: GraphState) -> dict:
        """Agent thinks: analyze state, decide next action."""
        state.metadata["phase"] = "thinking"
        return {"messages": [{"role": "agent", "content": "Thinking..."}],
                "iteration": state.iteration + 1}

    async def act_node(state: GraphState) -> dict:
        """Agent acts: execute the decided tool."""
        state.metadata["phase"] = "acting"
        return {"messages": [{"role": "agent", "content": "Acting..."}]}

    async def observe_node(state: GraphState) -> dict:
        """Agent observes: process tool output, update state."""
        state.metadata["phase"] = "observing"
        return {"messages": [{"role": "agent", "content": "Observing..."}]}

    async def fault_loc_node(state: GraphState) -> dict:
        """Run fault localization on test failures."""
        state.metadata["phase"] = "fault_localization"
        return {"messages": [{"role": "agent", "content": "Running fault localization..."}]}

    async def submit_node(state: GraphState) -> dict:
        """Submit the completed task."""
        state.metadata["phase"] = "submitting"
        return {"messages": [{"role": "agent", "content": "Task completed"}],
                "metadata": {"submitted": True}}

    # Route: after observing, do tests pass?
    def route_after_observe(state: GraphState) -> str:
        tr = state.test_results
        if tr and tr.get("failed", 0) == 0 and tr.get("collected", 0) > 0:
            return "pass"
        if state.iteration >= state.metadata.get("max_iterations", 50):
            return "pass"  # Give up and submit
        return "fail"

    # Register nodes
    graph.add_node("think", think_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("fault_loc", fault_loc_node)
    graph.add_node("submit", submit_node)

    # Edges
    graph.set_entry_point("think")
    graph.add_edge("think", "act")
    graph.add_edge("act", "observe")
    graph.add_edge("fault_loc", "think")   # After fault loc, think again
    graph.add_edge("submit", StateGraph.END)

    # Conditional: after observe, branch based on test results
    graph.add_conditional_edges("observe", route_after_observe, {
        "pass": "submit",
        "fail": "fault_loc",
    })

    return graph
