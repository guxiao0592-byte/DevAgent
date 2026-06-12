"""Artifact registry helpers: register artifacts and maintain index files under outputs/."""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
import hashlib
import mimetypes

from ..agent_core.schemas import Artifact
from .file_tool import FileTool


class ArtifactRegistry:
    """Simple on-disk registry for artifacts produced by agents.

    It stores artifact files using the existing AgentState.get_output_subdir structure
    and writes an index.json per phase directory listing artifacts and metadata.
    """

    def __init__(self, base_output: str):
        self.base_output = base_output
        FileTool.ensure_dir(base_output)

    def register(self, phase: str, artifact: Artifact) -> Dict[str, Any]:
        """Write artifact content to disk (if applicable) and update index.json.

        Returns the persisted metadata dict including path and timestamp.
        """
        subdir = os.path.join(self.base_output, phase)
        FileTool.ensure_dir(subdir)

        # Ensure artifact id
        if not artifact.id:
            artifact.id = f"a_{uuid.uuid4().hex[:8]}"

        filename = artifact.metadata.get("filename") or f"{artifact.id}.{self._ext_for_format(artifact.format)}"
        path = os.path.join(subdir, filename)
        # Prepare content bytes and metadata
        content_bytes = artifact.content.encode('utf-8') if isinstance(artifact.content, str) else artifact.content
        size = len(content_bytes)
        checksum = hashlib.sha256(content_bytes).hexdigest()
        mime_type = mimetypes.types_map.get('.' + self._ext_for_format(artifact.format), 'application/octet-stream')

        # Write content
        FileTool.write_bytes(path, content_bytes)

        # Build metadata entry
        entry = {
            "id": artifact.id,
            "type": artifact.type,
            "format": artifact.format,
            "filename": filename,
            "path": path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": artifact.metadata,
            "size": size,
            "checksum": checksum,
            "mime_type": mime_type,
        }

        # Update index
        index_path = os.path.join(subdir, "index.json")
        index = {}
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                index = {}

        index.setdefault("artifacts", []).append(entry)
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        return entry

    def register_from_state(self, state: object, phase_key: str, artifact: Artifact) -> Dict[str, Any]:
        """Register an artifact using AgentState's output subdir mapping.

        This avoids agents needing to hardcode phase directory names and
        centralizes index structure.
        """
        try:
            out_dir = state.get_output_subdir(phase_key)
        except Exception:
            # Fallback to base_output/phase_key if state not compatible
            out_dir = os.path.join(self.base_output, phase_key)

        FileTool.ensure_dir(out_dir)

        # Ensure artifact id
        if not artifact.id:
            artifact.id = f"a_{uuid.uuid4().hex[:8]}"

        filename = artifact.metadata.get("filename") or f"{artifact.id}.{self._ext_for_format(artifact.format)}"
        path = os.path.join(out_dir, filename)
        # Prepare content bytes and metadata
        content_bytes = artifact.content.encode('utf-8') if isinstance(artifact.content, str) else artifact.content
        size = len(content_bytes)
        checksum = hashlib.sha256(content_bytes).hexdigest()
        mime_type = mimetypes.types_map.get('.' + self._ext_for_format(artifact.format), 'application/octet-stream')

        # Write content
        FileTool.write_bytes(path, content_bytes)

        # Build metadata entry
        entry = {
            "id": artifact.id,
            "type": artifact.type,
            "format": artifact.format,
            "filename": filename,
            "path": path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": artifact.metadata,
            "size": size,
            "checksum": checksum,
            "mime_type": mime_type,
            "workflow_id": getattr(state, "task_id", None),
        }

        # Update index
        index_path = os.path.join(out_dir, "index.json")
        index = {}
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                index = {}

        index.setdefault("artifacts", []).append(entry)
        # Write atomically: write to temp then rename
        tmp_index = index_path + ".tmp"
        with open(tmp_index, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        os.replace(tmp_index, index_path)

        # Record registry event to state trace (if available)
        try:
            if hasattr(state, "add_trace") and callable(state.add_trace):
                state.add_trace("ArtifactRegistry", "registered", {
                    "phase": phase_key,
                    "artifact_id": entry.get("id"),
                    "filename": entry.get("filename"),
                    "path": entry.get("path"),
                })
        except Exception:
            # Non-fatal: tracing should not break registry
            pass

        return entry

    @staticmethod
    def _ext_for_format(fmt: str) -> str:
        mapping = {
            "mermaid": "mmd",
            "plantuml": "puml",
            "py": "py",
            "diff": "diff",
            "text": "txt",
            "json": "json",
            "md": "md",
        }
        return mapping.get(fmt, "txt")
