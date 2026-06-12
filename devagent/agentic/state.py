"""Enhanced agent state with event sourcing for DevAgent V2.

AgentLoopState tracks the ReAct loop execution with full history,
supports checkpoint save/restore, and integrates with the event bus.
"""

import os
import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from ..agent_core.state import AgentState


@dataclass
class AgentLoopState:
    """State for the ReAct agentic loop execution."""

    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    task_type: str = ""
    workspace: str = ""
    task_description: str = ""
    language: str = "python"

    # Loop control
    current_iteration: int = 0
    max_iterations: int = 50
    status: str = "INIT"  # INIT → RUNNING → COMPLETED / FAILED / STUCK

    # History
    action_history: list[dict] = field(default_factory=list)
    observation_history: list[dict] = field(default_factory=list)

    # File tracking
    modified_files: list[str] = field(default_factory=list)
    test_results: dict = field(default_factory=dict)

    # Sub-tasks (from planner)
    sub_tasks: list[dict] = field(default_factory=list)
    current_sub_task: str = ""

    # Context budget
    recent_history_rounds: int = 5

    # Legacy state for compatibility
    legacy_state: Optional[AgentState] = None

    # Checkpoint
    checkpoint_dir: str = ""

    def add_action(self, action: dict):
        action["iteration"] = self.current_iteration
        action["timestamp"] = datetime.now().isoformat()
        self.action_history.append(action)

    def add_observation(self, observation: dict):
        observation["iteration"] = self.current_iteration
        observation["timestamp"] = datetime.now().isoformat()
        self.observation_history.append(observation)
        if observation.get("modified_files"):
            self.modified_files.extend(observation["modified_files"])
            self.modified_files = list(dict.fromkeys(self.modified_files))

    def get_recent_history(self, rounds: int = None) -> tuple[list, list]:
        """Get recent N rounds of action-observation pairs."""
        n = rounds or self.recent_history_rounds
        return self.action_history[-n:], self.observation_history[-n:]

    def is_terminal(self) -> bool:
        return self.status in ("COMPLETED", "FAILED", "STUCK")

    def progress_ratio(self) -> float:
        return min(1.0, self.current_iteration / max(self.max_iterations, 1))

    # ==================================================================
    # Checkpoint save / restore
    # ==================================================================

    def to_checkpoint(self) -> dict:
        return {
            "version": 2,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "workspace": self.workspace,
            "task_description": self.task_description,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "status": self.status,
            "action_history": self.action_history,
            "observation_history": self.observation_history,
            "modified_files": self.modified_files,
            "test_results": self.test_results,
            "sub_tasks": self.sub_tasks,
            "current_sub_task": self.current_sub_task,
        }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "AgentLoopState":
        return cls(
            task_id=data["task_id"],
            task_type=data.get("task_type", ""),
            workspace=data.get("workspace", ""),
            task_description=data.get("task_description", ""),
            current_iteration=data.get("current_iteration", 0),
            max_iterations=data.get("max_iterations", 50),
            status=data.get("status", "INIT"),
            action_history=data.get("action_history", []),
            observation_history=data.get("observation_history", []),
            modified_files=data.get("modified_files", []),
            test_results=data.get("test_results", {}),
            sub_tasks=data.get("sub_tasks", []),
            current_sub_task=data.get("current_sub_task", ""),
        )

    def save(self, directory: str = ".devagent/checkpoints"):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.task_id}.json")
        with open(path, "w") as f:
            json.dump(self.to_checkpoint(), f, indent=2, ensure_ascii=False)
        self.checkpoint_dir = directory
        return path

    def restore(self, task_id: str, directory: str = ".devagent/checkpoints") -> bool:
        path = os.path.join(directory, f"{task_id}.json")
        if not os.path.exists(path):
            return False
        with open(path) as f:
            data = json.load(f)
        restored = self.from_checkpoint(data)
        for k, v in restored.__dict__.items():
            setattr(self, k, v)
        return True
