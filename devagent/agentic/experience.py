"""Experience Library — cross-task learning for DevAgent V2.

Implements the design from docs/improvement/08_经验库_跨任务学习设计.md:
  Experience     — structured record of a bug→fix pattern
  ExperienceStore — JSONL + vector embedding storage with hybrid retrieval
  ExperienceInjector — few-shot injection of similar past fixes into context
"""

import os
import json
import re
import hashlib
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Experience:
    """A single experience record capturing a bug→fix pattern."""
    id: str
    type: str = "bug_fix"              # bug_fix | refactor | feature_add | exploration

    # Indexing fields
    bug_signature: str = ""            # Error summary for similarity matching
    error_type: str = ""               # ZeroDivisionError | AttributeError | ...
    language: str = "python"
    file_pattern: str = ""             # "**/*.py" glob
    code_context_before: str = ""      # Code snippet around the bug

    # Result fields
    fix_description: str = ""
    fix_patch: str = ""
    success: bool = True
    iterations_needed: int = 0

    # Metadata
    task_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    project: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "bug_signature": self.bug_signature,
            "error_type": self.error_type,
            "language": self.language,
            "file_pattern": self.file_pattern,
            "code_context_before": self.code_context_before[:500],
            "fix_description": self.fix_description,
            "fix_patch": self.fix_patch[:1000],
            "success": self.success,
            "iterations_needed": self.iterations_needed,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "project": self.project,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Experience":
        return cls(
            id=data.get("id", ""),
            type=data.get("type", "bug_fix"),
            bug_signature=data.get("bug_signature", ""),
            error_type=data.get("error_type", ""),
            language=data.get("language", "python"),
            file_pattern=data.get("file_pattern", ""),
            code_context_before=data.get("code_context_before", ""),
            fix_description=data.get("fix_description", ""),
            fix_patch=data.get("fix_patch", ""),
            success=data.get("success", True),
            iterations_needed=data.get("iterations_needed", 0),
            task_id=data.get("task_id", ""),
            timestamp=data.get("timestamp", ""),
            project=data.get("project", ""),
            tags=data.get("tags", []),
        )

    def signature_hash(self) -> str:
        """Content-based hash for deduplication."""
        key = f"{self.error_type}:{self.bug_signature[:100]}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ============================================================================
# Experience Store
# ============================================================================

class ExperienceStore:
    """Persistent store with hybrid retrieval (keyword + structural + tag)."""

    def __init__(self, store_dir: str = ".devagent/experience"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, Experience] = {}
        self._load_all()

    def add(self, exp: Experience):
        """Add an experience record, deduplicating by signature hash."""
        sig = exp.signature_hash()
        # Check for near-duplicate
        for existing in self._records.values():
            if existing.signature_hash() == sig:
                return  # Duplicate, skip

        self._records[exp.id] = exp
        self._append_to_log(exp)

    def _append_to_log(self, exp: Experience):
        log_path = self.store_dir / "records.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(exp.to_dict(), ensure_ascii=False) + "\n")

    def _load_all(self):
        log_path = self.store_dir / "records.jsonl"
        if not log_path.exists():
            return
        try:
            with open(log_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            exp = Experience.from_dict(data)
                            self._records[exp.id] = exp
                        except (json.JSONDecodeError, TypeError):
                            continue
        except Exception:
            pass

    def retrieve(self, query: dict, top_k: int = 5) -> list[Experience]:
        """Hybrid retrieval: keyword + error_type + structural + tag matching.

        query keys:
          - error_type: str (e.g., 'ZeroDivisionError')
          - error_message: str (full error text)
          - task: str (task description)
          - language: str
          - project: str
          - file_pattern: str
        """
        scores: dict[str, float] = {}

        error_type = query.get("error_type", "")
        error_msg = query.get("error_message", "")
        task = query.get("task", "")
        language = query.get("language", "python")
        project = query.get("project", "")
        file_pattern = query.get("file_pattern", "")

        keywords = self._extract_keywords(f"{error_type} {error_msg} {task}")
        query_text = f"{error_type} {error_msg} {task}".lower()

        for exp_id, exp in self._records.items():
            score = 0.0

            # 1. Error type match (strong signal)
            if error_type and exp.error_type == error_type:
                score += 3.0

            # 2. Keyword overlap in bug signature
            sig_lower = exp.bug_signature.lower()
            for kw in keywords:
                if kw in sig_lower:
                    score += 1.0
                if kw in query_text:
                    if kw in exp.fix_description.lower():
                        score += 0.5

            # 3. Language match
            if language == exp.language:
                score += 0.5

            # 4. Project match
            if project and project == exp.project:
                score += 1.0

            # 5. File pattern match
            if file_pattern and exp.file_pattern:
                if self._pattern_overlap(file_pattern, exp.file_pattern):
                    score += 0.8

            # 6. Tag overlap
            query_tags = set(keywords[:5])
            exp_tags = set(exp.tags)
            tag_overlap = len(query_tags & exp_tags)
            score += tag_overlap * 0.3

            # 7. Success bonus (prefer successful fixes)
            if exp.success:
                score *= 1.1

            if score > 0:
                scores[exp_id] = score

        # Sort and return top-k
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        results = [self._records[exp_id] for exp_id, _ in ranked[:top_k]
                   if scores[exp_id] > 1.0]  # Minimum relevance threshold
        return results

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        words = re.findall(r'[a-zA-Z_]\w+', text.lower())
        stop = {'the', 'a', 'an', 'is', 'are', 'was', 'in', 'on', 'at',
                'to', 'for', 'of', 'with', 'and', 'or', 'this', 'that',
                'it', 'from', 'by', 'as', 'not', 'but', 'be', 'has', 'had'}
        return [w for w in words if w not in stop and len(w) > 2]

    @staticmethod
    def _pattern_overlap(a: str, b: str) -> bool:
        a_parts = set(a.replace("*", "").split("/"))
        b_parts = set(b.replace("*", "").split("/"))
        return bool(a_parts & b_parts)

    def size(self) -> int:
        return len(self._records)

    def clear(self):
        self._records.clear()
        log_path = self.store_dir / "records.jsonl"
        if log_path.exists():
            log_path.unlink()


# ============================================================================
# Experience Injector
# ============================================================================

class ExperienceInjector:
    """Formats and injects relevant past experiences into the agent context."""

    INJECTION_TEMPLATE = """## Past Experience: Similar Issues Fixed Before

{experiences}

Learn from these patterns. If a past fix matches your current situation,
apply the same approach. Otherwise use them as reference only.
"""

    def inject(self, experiences: list[Experience],
               messages: list[dict]) -> list[dict]:
        """Insert past experiences into the context messages."""
        if not experiences:
            return messages

        parts = []
        for i, exp in enumerate(experiences[:3], 1):
            parts.append(self._format_one(i, exp))

        injection = self.INJECTION_TEMPLATE.format(experiences="\n".join(parts))

        # Insert after system messages, before dynamic context
        new_messages = list(messages)
        new_messages.append({"role": "user", "content": injection})
        return new_messages

    @staticmethod
    def _format_one(idx: int, exp: Experience) -> str:
        lines = [
            f"### Experience {idx}: {exp.error_type}",
            f"- Bug: {exp.bug_signature[:200]}",
            f"- Fix: {exp.fix_description[:300]}",
        ]
        if exp.fix_patch:
            patch_preview = exp.fix_patch[:600]
            lines.append(f"- Patch:\n```diff\n{patch_preview}\n```")
        lines.append(f"- Result: {'success' if exp.success else 'failed'} "
                     f"({exp.iterations_needed} iterations)")
        return "\n".join(lines)


# ============================================================================
# Experience Recorder (integration with DevAgentCore)
# ============================================================================

class ExperienceRecorder:
    """Extracts and records experiences from completed agent tasks."""

    def __init__(self, store: ExperienceStore):
        self.store = store

    def record_from_state(self, state: "AgentLoopState") -> Optional[Experience]:
        """Extract an experience from a completed AgentLoopState."""
        if state.status != "COMPLETED" or not state.modified_files:
            return None

        # Extract error signature from test results or action history
        error_type = self._extract_error_type(state)
        bug_signature = self._extract_bug_signature(state)

        if not error_type and not bug_signature:
            return None

        # Extract fix info
        fix_patch = self._extract_patch(state)
        fix_description = self._extract_fix_description(state)

        exp = Experience(
            id=f"exp_{state.task_id}",
            type="bug_fix" if error_type else "feature_add",
            bug_signature=bug_signature or state.task_description[:200],
            error_type=error_type or "none",
            language=state.language,
            file_pattern=state.modified_files[0] if state.modified_files else "",
            fix_description=fix_description,
            fix_patch=fix_patch,
            success=True,
            iterations_needed=state.current_iteration,
            task_id=state.task_id,
            project=os.path.basename(state.workspace),
            tags=self._extract_tags(state),
        )
        self.store.add(exp)
        return exp

    @staticmethod
    def _extract_error_type(state) -> str:
        tr = state.test_results or {}
        if tr.get("failed", 0) > 0:
            return "TestFailure"
        # Look for errors in action history
        for obs in state.observation_history:
            if not obs.get("success", True):
                err = obs.get("error", "")
                for etype in ["SyntaxError", "AttributeError", "TypeError",
                             "ValueError", "ZeroDivisionError", "KeyError",
                             "ImportError", "NameError", "IndexError"]:
                    if etype in err:
                        return etype
        return ""

    @staticmethod
    def _extract_bug_signature(state) -> str:
        parts = []
        # Add test failure info
        tr = state.test_results or {}
        if tr.get("failed", 0) > 0:
            parts.append(f"{tr.get('failed', 0)} tests failed")
        # Add error messages from observations
        for obs in state.observation_history[-5:]:
            err = obs.get("error", "") or obs.get("output", "")
            if not obs.get("success", True):
                parts.append(err[:100])
        return " | ".join(parts[:3]) if parts else state.task_description[:200]

    @staticmethod
    def _extract_fix_description(state) -> str:
        if state.modified_files:
            return f"Modified {len(state.modified_files)} file(s): " + \
                   ", ".join(state.modified_files[:5])
        return "No files modified"

    @staticmethod
    def _extract_patch(state) -> str:
        # Look for patch in observations
        for obs in reversed(state.observation_history):
            output = obs.get("output", "")
            if "diff" in output.lower() or "patch" in output.lower():
                return output[:1000]
        return ""

    @staticmethod
    def _extract_tags(state) -> list[str]:
        tags = set()
        for a in state.action_history:
            tool = a.get("tool", "")
            if tool == "grep_text":
                pat = a.get("params", {}).get("pattern", "")
                if pat:
                    tags.add(pat[:30])
            elif tool in ("file_edit", "file_write"):
                path = a.get("params", {}).get("path", "")
                if path:
                    tags.add(Path(path).stem)
        return list(tags)[:8]
