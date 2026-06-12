"""File operations tool for reading/writing artifacts."""

import os
import shutil


class FileTool:
    """Handles file and directory operations for the DevAgent workflow."""

    @staticmethod
    def read_text(file_path: str) -> str:
        """Read text content from a file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def write_text(file_path: str, content: str) -> str:
        """Write text content to a file, creating directories as needed."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return file_path

    @staticmethod
    def write_json(file_path: str, data: dict) -> str:
        """Write JSON data to a file."""
        import json
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return file_path

    @staticmethod
    def ensure_dir(dir_path: str) -> str:
        """Ensure a directory exists, create it if necessary."""
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

    @staticmethod
    def write_bytes(file_path: str, content: bytes) -> str:
        """Write binary content to a file, creating directories as needed."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(content)
        return file_path

    @staticmethod
    def list_files(dir_path: str, pattern: str = None) -> list[str]:
        """List files in a directory, optionally filtered by suffix."""
        if not os.path.exists(dir_path):
            return []
        files = []
        for root, _, filenames in os.walk(dir_path):
            for f in filenames:
                if pattern is None or f.endswith(pattern):
                    files.append(os.path.join(root, f))
        return sorted(files)

    @staticmethod
    def copy_dir(src: str, dst: str):
        """Copy a directory recursively."""
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    @staticmethod
    def safe_path(base: str, *parts: str) -> str:
        """Safely join path parts, preventing directory traversal."""
        full = os.path.normpath(os.path.join(base, *parts))
        base_norm = os.path.normpath(base)
        if not full.startswith(base_norm):
            raise PermissionError(f"Path traversal detected: {full}")
        return full
