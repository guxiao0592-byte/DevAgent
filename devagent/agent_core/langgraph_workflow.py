"""LangGraph-based workflow orchestration for DevAgent.

This module provides an alternative workflow implementation using LangGraph,
a graph-based workflow orchestration framework. It enables stateful,
durable execution with conditional branching and retry loops.

Requires: pip install langgraph langchain-core

Reference: https://langchain-ai.github.io/langgraph/
"""

import json
import os
from typing import Optional, Any
from .state import AgentState
from .schemas import TaskSpec
from .llm_client import LLMClient
from .config_loader import load_config, get_llm_config, get_workflow_config
from ..agents.requirement_agent import RequirementAgent
from ..agents.design_agent import DesignAgent
from ..agents.code_agent import CodeAgent
from ..agents.test_agent import TestAgent
from ..agents.debug_agent import DebugAgent
from ..agents.repair_agent import RepairAgent
from ..agents.review_agent import ReviewAgent
from ..tools.file_tool import FileTool
from ..tools.test_runner import PytestRunner

try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


class LangGraphWorkflowController:
    """LangGraph-based workflow controller with graph-structured pipeline."""

    def __init__(self, config_path: Optional[str] = None):
        if not HAS_LANGGRAPH:
            raise ImportError(
                "LangGraph is required. Install: pip install langgraph langchain-core"
            )

        self.config = load_config(config_path)
        llm_config = get_llm_config(self.config)
        self.llm = LLMClient(llm_config)
        self.workflow_config = get_workflow_config(self.config)
        self.file_tool = FileTool()

        # Initialize agents
        self.requirement_agent = RequirementAgent(self.llm, self.config.get("tools", {}))
        self.design_agent = DesignAgent(self.llm, self.workflow_config)
        self.code_agent = CodeAgent(self.llm, self.workflow_config)
        self.test_agent = TestAgent(self.llm, self.config.get("tools", {}))
        self.debug_agent = DebugAgent(self.llm, self.workflow_config)
        self.repair_agent = RepairAgent(self.llm, self.config.get("tools", {}))
        self.review_agent = ReviewAgent(self.llm, self.workflow_config)
        self.test_runner = PytestRunner(timeout=(self.config.get("tools", {})).get("pytest_timeout", 60))

    def execute(self, spec: TaskSpec) -> AgentState:
        """Execute workflow using LangGraph state graph."""
        state = AgentState(
            task_type=spec.task_type,
            input_path=spec.input_path,
            output_root=spec.output_root,
            max_retry=spec.max_retry
        )
        os.makedirs(state.output_root, exist_ok=True)
        # Initialize central ArtifactRegistry for this workflow run
        try:
            from ..tools.artifact_registry import ArtifactRegistry
            state.artifact_registry = ArtifactRegistry(state.output_root or "outputs")
        except Exception:
            state.artifact_registry = None

        workflow = self._build_workflow(spec.task_type)
        app = workflow.compile()

        # Execute the graph
        try:
            final_state = app.invoke(state)
            if isinstance(final_state, dict):
                # Convert dict back to AgentState if needed
                state = AgentState(**{k: v for k, v in final_state.items() if hasattr(state, k)})
            else:
                state = final_state
        except Exception as e:
            state.add_error("langgraph", f"Graph execution failed: {str(e)}")
            state.status = "FAILED"

        # Run review
        try:
            state = self.review_agent.run(state)
        except Exception as e:
            state.add_error("review", f"Review failed: {str(e)}")

        self._save_execution_log(state)
        return state

    def _build_workflow(self, task_type: str) -> StateGraph:
        """Build the appropriate workflow graph based on task type."""
        builder = StateGraph(AgentState)

        # Add nodes
        builder.add_node("requirement", self.requirement_agent.run)
        builder.add_node("design", self.design_agent.run)
        builder.add_node("code", self.code_agent.run)
        builder.add_node("test", self.test_agent.run)
        builder.add_node("debug", self.debug_agent.run)
        builder.add_node("repair", self.repair_agent.run)
        builder.add_node("review", self.review_agent.run)

        if task_type == "design":
            builder.set_entry_point("requirement")
            builder.add_edge("requirement", "design")
            builder.add_edge("design", "review")
            builder.add_edge("review", END)

        elif task_type == "implement":
            builder.set_entry_point("code")
            builder.add_edge("code", "test")
            builder.add_conditional_edges(
                "test",
                self._should_repair,
                {"repair": "repair", "review": "review"}
            )
            builder.add_edge("repair", "review")
            builder.add_edge("review", END)

        elif task_type == "repair":
            builder.set_entry_point("debug")
            builder.add_edge("debug", "repair")
            builder.add_conditional_edges(
                "repair",
                self._should_retry_repair,
                {"repair": "repair", "review": "review"}
            )
            builder.add_edge("review", END)

        elif task_type == "full":
            builder.set_entry_point("requirement")
            builder.add_edge("requirement", "design")
            builder.add_edge("design", "code")
            builder.add_edge("code", "test")
            builder.add_conditional_edges(
                "test",
                self._should_repair,
                {"debug": "debug", "review": "review"}
            )
            builder.add_edge("debug", "repair")
            builder.add_conditional_edges(
                "repair",
                self._should_retry_repair,
                {"debug": "debug", "review": "review"}
            )
            builder.add_edge("review", END)

        return builder

    @staticmethod
    def _should_repair(state: AgentState) -> str:
        """Conditional edge: should we enter repair loop?"""
        if state.test_results and (
            state.test_results.get("failed", 0) > 0 or
            state.test_results.get("errors", 0) > 0
        ):
            return "debug" if state.task_type == "full" else "repair"
        return "review"

    @staticmethod
    def _should_retry_repair(state: AgentState) -> str:
        """Conditional edge: should we retry repair?"""
        if state.retry_count >= state.max_retry:
            return "review"

        if state.repair_patch:
            rg = state.repair_patch.get("regression_results", {})
            if rg.get("success", False) or rg.get("failed", 0) == 0:
                return "review"

        return "debug"

    def _save_execution_log(self, state: AgentState):
        """Save execution log."""
        log_path = os.path.join(state.output_root, "execution.log")
        lines = [f"DevAgent LangGraph Execution Log - {state.task_id}"]
        lines.append(f"Task Type: {state.task_type}")
        lines.append(f"Status: {state.status}\n")
        lines.append("Execution Trace:")
        for trace in state.execution_trace:
            lines.append(f"  [{trace.get('timestamp', '')}] {trace.get('node', '')}: {trace.get('status', '')}")
        if state.errors:
            lines.append("\nErrors:")
            for err in state.errors:
                lines.append(f"  [{err.get('phase', '')}] {err.get('message', '')}")
        self.file_tool.write_text(log_path, "\n".join(lines))
        # Register execution log to artifact registry if available
        try:
            reg = getattr(state, "artifact_registry", None)
            if reg is not None:
                from ..agent_core.schemas import Artifact as ArtifactModel
                art = ArtifactModel(id=f"langgraph_execution_log_{state.task_id}",
                                    type="workflow:execution_log",
                                    format="txt",
                                    content="\n".join(lines),
                                    metadata={"generated_by": "LangGraphWorkflowController", "filename": "execution.log"})
                reg.register_from_state(state, "workflow", art)
        except Exception:
            pass

    @staticmethod
    def is_available() -> bool:
        """Check if LangGraph is installed."""
        return HAS_LANGGRAPH
