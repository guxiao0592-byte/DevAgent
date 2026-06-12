"""Session Manager — manages client connections, roles, and message routing.

Features:
  - Multiple concurrent clients per task (controller + observers)
  - Role-based access (CONTROLLER / OBSERVER)
  - Automatic reconnection with event replay
  - Message delivery via per-client async queues
  - Stale session cleanup
"""

import asyncio
import enum
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from collections import defaultdict


# ============================================================================
# Enums
# ============================================================================

class ClientRole(str, enum.Enum):
    """Role of a connected client."""
    CONTROLLER = "controller"   # Can send commands, approve/deny actions
    OBSERVER = "observer"       # Read-only, sees events only


class ClientTransport(str, enum.Enum):
    """Transport type for a client connection."""
    WEBSOCKET = "websocket"
    IPC = "ipc"
    TUI = "tui"
    REST = "rest"


# ============================================================================
# Data Types
# ============================================================================

@dataclass
class ClientSession:
    """A single connected client session."""
    client_id: str
    task_id: str
    role: ClientRole = ClientRole.OBSERVER
    transport: str = "websocket"
    connected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_heartbeat: str = field(default_factory=lambda: datetime.now().isoformat())
    message_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))

    # Reconnection support
    session_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    last_event_seq: int = 0          # Last event sequence number delivered
    is_connected: bool = True

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "task_id": self.task_id,
            "role": self.role.value,
            "transport": self.transport,
            "connected_at": self.connected_at,
            "is_connected": self.is_connected,
        }


# ============================================================================
# Session Manager
# ============================================================================

class SessionManager:
    """Manages all client sessions across tasks.

    Responsibilities:
      - Client registration / deregistration
      - Event broadcasting to all clients of a task
      - Event logging for reconnection replay
      - Command routing to task's InteractionController
      - Controller binding for InteractionController lookup
      - Stale session cleanup
    """

    def __init__(self, max_event_log: int = 500,
                 session_timeout_seconds: int = 300):
        # task_id -> {client_id -> ClientSession}
        self._sessions: dict[str, dict[str, ClientSession]] = defaultdict(dict)

        # task_id -> ordered event list (for replay on reconnect)
        self._event_log: dict[str, list[dict]] = defaultdict(list)
        self._max_event_log = max_event_log

        # task_id -> InteractionController
        self._task_controllers: dict[str, object] = {}

        # Cleanup
        self._session_timeout = session_timeout_seconds

    # ==================================================================
    # Client lifecycle
    # ==================================================================

    async def register_client(self, task_id: str,
                              role: ClientRole = ClientRole.OBSERVER,
                              transport: str = "websocket",
                              session_token: str = None) -> ClientSession:
        """Register a new client or reconnect an existing one.

        If session_token is provided and matches an existing session,
        the client reconnects and receives missed events.

        Args:
            task_id: The task to observe/interact with
            role: CONTROLLER or OBSERVER
            transport: Connection transport type
            session_token: Optional token for reconnection

        Returns:
            The ClientSession (new or reconnected)
        """
        # Check for reconnection
        if session_token:
            for cid, session in self._sessions.get(task_id, {}).items():
                if session.session_token == session_token:
                    session.is_connected = True
                    session.last_heartbeat = datetime.now().isoformat()
                    session.transport = transport

                    # Replay missed events
                    missed = self._event_log[task_id][session.last_event_seq:]
                    for event in missed:
                        try:
                            session.message_queue.put_nowait(event)
                        except asyncio.QueueFull:
                            break
                    session.last_event_seq = len(self._event_log[task_id])
                    return session

        # New client — enforce single controller
        if role == ClientRole.CONTROLLER:
            for existing in self._sessions.get(task_id, {}).values():
                if existing.role == ClientRole.CONTROLLER and existing.is_connected:
                    # Downgrade existing controller to observer
                    existing.role = ClientRole.OBSERVER

        # Create new session
        client_id = f"client_{uuid.uuid4().hex[:8]}"
        session = ClientSession(
            client_id=client_id,
            task_id=task_id,
            role=role,
            transport=transport,
        )
        self._sessions[task_id][client_id] = session
        return session

    async def unregister_client(self, task_id: str, client_id: str):
        """Mark client as disconnected (keep session for reconnection)."""
        if task_id in self._sessions and client_id in self._sessions[task_id]:
            self._sessions[task_id][client_id].is_connected = False
            self._sessions[task_id][client_id].last_heartbeat = datetime.now().isoformat()

    async def remove_client(self, task_id: str, client_id: str):
        """Permanently remove a client session."""
        if task_id in self._sessions:
            self._sessions[task_id].pop(client_id, None)
            if not self._sessions[task_id]:
                del self._sessions[task_id]

    # ==================================================================
    # Message routing
    # ==================================================================

    async def broadcast(self, task_id: str, event: dict):
        """Push event to all connected clients watching a task.

        Events are stored in the log for reconnection replay.
        Old events are trimmed when the log exceeds max_event_log.

        Args:
            task_id: Target task
            event: Event dict with at minimum a "type" key
        """
        # Store for replay
        seq = len(self._event_log[task_id])
        event["_seq"] = seq
        self._event_log[task_id].append(event)

        # Trim old events
        if len(self._event_log[task_id]) > self._max_event_log:
            self._event_log[task_id] = self._event_log[task_id][-self._max_event_log:]

        # Push to all connected clients
        for session in self._sessions.get(task_id, {}).values():
            if session.is_connected:
                try:
                    session.message_queue.put_nowait(event)
                    session.last_event_seq = seq + 1
                except asyncio.QueueFull:
                    pass  # Drop for slow clients

    async def send_to_client(self, task_id: str, client_id: str,
                              event: dict) -> bool:
        """Send an event to a specific client. Returns True if delivered."""
        session = self._sessions.get(task_id, {}).get(client_id)
        if not session or not session.is_connected:
            return False
        try:
            session.message_queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False

    async def send_command(self, task_id: str, client_id: str,
                            command) -> bool:
        """Route a user command to the task's InteractionController.

        Args:
            task_id: Target task
            client_id: Sending client
            command: UserCommand instance

        Returns:
            True if command was enqueued, False otherwise
        """
        controller = self._task_controllers.get(task_id)
        if not controller:
            return False

        try:
            # Set client_id on command
            command.client_id = client_id
            await controller.enqueue_command(command)
            return True
        except Exception:
            return False

    # ==================================================================
    # Controller binding
    # ==================================================================

    def bind_controller(self, task_id: str, controller):
        """Bind an InteractionController to a task.

        The controller is used to route commands and resolve approvals.
        """
        self._task_controllers[task_id] = controller

    def unbind_controller(self, task_id: str):
        """Remove controller binding after task completion."""
        self._task_controllers.pop(task_id, None)

    def get_controller(self, task_id: str):
        """Get the InteractionController for a task, if any."""
        return self._task_controllers.get(task_id)

    # ==================================================================
    # Queries
    # ==================================================================

    def get_client(self, task_id: str, client_id: str) -> Optional[ClientSession]:
        """Get a specific client session."""
        return self._sessions.get(task_id, {}).get(client_id)

    def get_connected_clients(self, task_id: str) -> list[ClientSession]:
        """List all currently connected clients for a task."""
        return [s for s in self._sessions.get(task_id, {}).values()
                if s.is_connected]

    def get_connected_count(self, task_id: str) -> int:
        """Count connected clients for a task."""
        return len(self.get_connected_clients(task_id))

    def get_active_tasks(self) -> list[str]:
        """List task IDs that have connected clients or controllers."""
        return list(self._sessions.keys())

    def get_event_log(self, task_id: str, from_seq: int = 0) -> list[dict]:
        """Get events from the log starting at a sequence number."""
        log = self._event_log.get(task_id, [])
        return log[from_seq:]

    # ==================================================================
    # Maintenance
    # ==================================================================

    async def heartbeat(self, task_id: str, client_id: str):
        """Update a client's heartbeat timestamp."""
        session = self._sessions.get(task_id, {}).get(client_id)
        if session:
            session.last_heartbeat = datetime.now().isoformat()

    async def cleanup_stale(self, max_idle_seconds: int = None):
        """Remove sessions that have been disconnected for too long."""
        timeout = max_idle_seconds or self._session_timeout
        now = datetime.now()

        for task_id in list(self._sessions.keys()):
            for cid in list(self._sessions[task_id].keys()):
                s = self._sessions[task_id][cid]
                if not s.is_connected:
                    try:
                        last = datetime.fromisoformat(s.last_heartbeat)
                        if (now - last).total_seconds() > timeout:
                            del self._sessions[task_id][cid]
                    except (ValueError, TypeError):
                        del self._sessions[task_id][cid]

            # Clean up empty task entries (if no controller bound)
            if not self._sessions[task_id] and task_id not in self._task_controllers:
                del self._sessions[task_id]
