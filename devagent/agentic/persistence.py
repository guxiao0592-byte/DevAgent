"""Persistence Layer — SQLite-backed Task, Event, Review, Artifact stores.

Design:
  - Zero-dependency (sqlite3 in stdlib)
  - Repository pattern with clean interfaces
  - JSONB emulated via TEXT columns
  - Auto-creates tables on first use
  - Thread-safe via WAL mode

Tables:
  tasks       — task metadata and status
  runs        — execution runs within a task
  events      — agent action/observation events (append-only)
  reviews     — phase review requests and decisions
  artifacts   — produced artifact records
  checkpoints — agent loop state checkpoints
"""

from __future__ import annotations
import sqlite3
import json
import os
import time
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class TaskRecord:
    """A task stored in the database."""
    id: str = ""
    title: str = ""
    mode: str = "agentic"
    status: str = "RUNNING"
    current_phase: str = ""
    workspace: str = "."
    output_dir: str = ""
    phase_index: int = 0
    total_phases: int = 6
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_row(cls, row: tuple) -> "TaskRecord":
        cols = ["id", "title", "mode", "status", "current_phase", "workspace",
                "output_dir", "phase_index", "total_phases", "created_at", "updated_at", "completed_at"]
        d = dict(zip(cols, row))
        return cls(**d)


@dataclass
class EventRecord:
    """An agent execution event."""
    id: str = ""
    task_id: str = ""
    run_id: str = ""
    event_type: str = ""
    payload: dict = field(default_factory=dict)
    sequence_no: int = 0
    created_at: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["payload"] = json.dumps(d["payload"]) if isinstance(d["payload"], dict) else d["payload"]
        return d


@dataclass
class ReviewRecord:
    """A phase review record."""
    id: str = ""
    task_id: str = ""
    phase: str = ""
    title: str = ""
    summary: str = ""
    quality_score: dict = field(default_factory=dict)
    status: str = "pending"  # pending, approved, revised, rejected
    decision: str = ""
    feedback: str = ""
    created_at: str = ""
    decided_at: str = ""


@dataclass
class ArtifactRecord:
    """An artifact produced by an agent."""
    id: str = ""
    task_id: str = ""
    phase: str = ""
    type: str = ""
    path: str = ""
    hash_sha256: str = ""
    size_bytes: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


# ============================================================================
# Database Schema
# ============================================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    mode TEXT DEFAULT 'agentic',
    status TEXT DEFAULT 'RUNNING',
    current_phase TEXT DEFAULT '',
    workspace TEXT DEFAULT '.',
    output_dir TEXT DEFAULT '',
    phase_index INTEGER DEFAULT 0,
    total_phases INTEGER DEFAULT 6,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT DEFAULT 'RUNNING',
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    sequence_no INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, sequence_no);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    phase TEXT DEFAULT '',
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    quality_score TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    decision TEXT DEFAULT '',
    feedback TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    decided_at TEXT DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    phase TEXT DEFAULT '',
    type TEXT DEFAULT '',
    path TEXT DEFAULT '',
    hash_sha256 TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    iteration INTEGER DEFAULT 0,
    state_data TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Non-critical but convenient
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON reviews(task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
"""


# ============================================================================
# Persistent Store — Singleton per process
# ============================================================================

class PersistentStore:
    """Central persistence manager using SQLite."""

    _instance: Optional["PersistentStore"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = "./outputs/devagent.db"):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str = None) -> "PersistentStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path or "./outputs/devagent.db")
        return cls._instance

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._get_conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    # ======== Tasks ========

    def save_task(self, task: TaskRecord) -> str:
        """Insert or update a task."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            existing = conn.execute("SELECT id FROM tasks WHERE id=?", (task.id,)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE tasks SET status=?, current_phase=?, phase_index=?,
                       updated_at=? WHERE id=?""",
                    (task.status, task.current_phase, task.phase_index, now, task.id))
            else:
                task.created_at = now
                task.updated_at = now
                conn.execute(
                    """INSERT INTO tasks (id,title,mode,status,current_phase,workspace,
                       output_dir,phase_index,total_phases,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (task.id, task.title, task.mode, task.status, task.current_phase,
                     task.workspace, task.output_dir, task.phase_index, task.total_phases,
                     task.created_at, task.updated_at))
            conn.commit()
        return task.id

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get a task by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            return dict(row)
        return None

    def list_tasks(self, limit: int = 50, status: str = None) -> list[dict]:
        """List tasks, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update_task_status(self, task_id: str, status: str,
                           current_phase: str = "", phase_index: int = None):
        """Quick status update."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            if phase_index is not None:
                conn.execute(
                    "UPDATE tasks SET status=?, current_phase=?, phase_index=?, updated_at=? WHERE id=?",
                    (status, current_phase, phase_index, now, task_id))
            else:
                conn.execute(
                    "UPDATE tasks SET status=?, current_phase=?, updated_at=? WHERE id=?",
                    (status, current_phase, now, task_id))
            if status in ("COMPLETED", "FINISHED", "FAILED", "ABORTED"):
                conn.execute("UPDATE tasks SET completed_at=? WHERE id=?", (now, task_id))
            conn.commit()

    # ======== Events ========

    def append_event(self, task_id: str, event_type: str, payload: dict = None,
                     run_id: str = "", sequence_no: int = 0) -> str:
        """Append an event to the log. Returns event ID."""
        import uuid
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO events (id,task_id,run_id,event_type,payload,sequence_no,created_at) VALUES (?,?,?,?,?,?,?)",
                (eid, task_id, run_id, event_type,
                 json.dumps(payload or {}, ensure_ascii=False),
                 sequence_no, now))
            conn.commit()
        return eid

    def get_events(self, task_id: str, after_sequence: int = 0,
                   limit: int = 500) -> list[dict]:
        """Get events for a task, optionally after a sequence number (for replay)."""
        with self._get_conn() as conn:
            if after_sequence > 0:
                rows = conn.execute(
                    "SELECT * FROM events WHERE task_id=? AND sequence_no > ? ORDER BY sequence_no ASC LIMIT ?",
                    (task_id, after_sequence, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY sequence_no ASC LIMIT ?",
                    (task_id, limit)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
            results.append(d)
        return results

    def get_last_sequence(self, task_id: str) -> int:
        """Get the highest sequence number for a task."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(sequence_no) FROM events WHERE task_id=?", (task_id,)).fetchone()
        return row[0] or 0

    # ======== Reviews ========

    def save_review(self, review: ReviewRecord):
        """Insert or update a review."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            existing = conn.execute("SELECT id FROM reviews WHERE id=?", (review.id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE reviews SET status=?, decision=?, feedback=?, decided_at=? WHERE id=?",
                    (review.status, review.decision, review.feedback, now, review.id))
            else:
                review.created_at = now
                conn.execute(
                    """INSERT INTO reviews (id,task_id,phase,title,summary,quality_score,status,decision,feedback,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (review.id, review.task_id, review.phase, review.title,
                     review.summary, json.dumps(review.quality_score or {}),
                     review.status, review.decision, review.feedback, review.created_at))
            conn.commit()

    def get_pending_reviews(self, task_id: str) -> list[dict]:
        """Get pending reviews for a task."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE task_id=? AND status='pending' ORDER BY created_at",
                (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_review_history(self, task_id: str) -> list[dict]:
        """Get all reviews for a task."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE task_id=? ORDER BY created_at DESC",
                (task_id,)).fetchall()
        return [dict(r) for r in rows]

    # ======== Artifacts ========

    def save_artifact(self, artifact: ArtifactRecord):
        """Save an artifact record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO artifacts (id,task_id,phase,type,path,hash_sha256,size_bytes,metadata,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (artifact.id, artifact.task_id, artifact.phase, artifact.type,
                 artifact.path, artifact.hash_sha256, artifact.size_bytes,
                 json.dumps(artifact.metadata or {}), now))
            conn.commit()

    def list_artifacts(self, task_id: str, phase: str = None) -> list[dict]:
        """List artifacts for a task, optionally filtered by phase."""
        with self._get_conn() as conn:
            if phase:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE task_id=? AND phase=? ORDER BY created_at",
                    (task_id, phase)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at",
                    (task_id,)).fetchall()
        return [dict(r) for r in rows]

    # ======== Checkpoints ========

    def save_checkpoint(self, task_id: str, iteration: int, state_data: dict):
        """Save an agent loop state checkpoint."""
        import uuid
        cid = f"ckpt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO checkpoints (id,task_id,iteration,state_data,created_at) VALUES (?,?,?,?,?)",
                (cid, task_id, iteration, json.dumps(state_data, ensure_ascii=False), now))
            # Keep only last 50 checkpoints per task
            conn.execute(
                "DELETE FROM checkpoints WHERE task_id=? AND id NOT IN (SELECT id FROM checkpoints WHERE task_id=? ORDER BY iteration DESC LIMIT 50)",
                (task_id, task_id))
            conn.commit()
        return cid

    def get_latest_checkpoint(self, task_id: str) -> Optional[dict]:
        """Get the most recent checkpoint for a task."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE task_id=? ORDER BY iteration DESC LIMIT 1",
                (task_id,)).fetchone()
        if row:
            d = dict(row)
            try:
                d["state_data"] = json.loads(d["state_data"])
            except json.JSONDecodeError:
                pass
            return d
        return None

    # ======== Utility ========

    def get_dashboard_metrics(self) -> dict:
        """Compute dashboard metrics from stored data."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('COMPLETED','FINISHED')").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='FAILED'").fetchone()[0]
            running = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='RUNNING'").fetchone()[0]
            avg_iter = conn.execute("SELECT AVG(phase_index) FROM tasks WHERE status IN ('COMPLETED','FINISHED')").fetchone()[0]
            total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "active_tasks": running,
            "success_rate": (completed / total) if total > 0 else 0.0,
            "avg_iterations": round(avg_iter, 1) if avg_iter else 0,
            "total_events": total_events,
        }

    def close(self):
        """Close all connections."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
