"""Patch generation and application tool."""

import os
import subprocess
import difflib


class PatchTool:
    """Generates and applies patches for code repair."""

    @staticmethod
    def generate_diff(original_path: str, repaired_path: str) -> str:
        """Generate a unified diff between original and repaired file."""
        with open(original_path, 'r') as f:
            original_lines = f.readlines()
        with open(repaired_path, 'r') as f:
            repaired_lines = f.readlines()

        rel_path = os.path.basename(original_path)
        diff = difflib.unified_diff(
            original_lines, repaired_lines,
            fromfile=f'a/{rel_path}',
            tofile=f'b/{rel_path}',
            lineterm=''
        )
        return '\n'.join(diff)

    @staticmethod
    def generate_patch_from_text(original_text: str, repaired_text: str, file_path: str) -> str:
        """Generate a diff patch from text content."""
        original_lines = original_text.splitlines(keepends=True)
        repaired_lines = repaired_text.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines, repaired_lines,
            fromfile=f'a/{file_path}',
            tofile=f'b/{file_path}',
            lineterm=''
        )
        return '\n'.join(diff)

    @staticmethod
    def apply_patch(file_path: str, patch_content: str, work_dir: str = None) -> bool:
        """Apply a patch to a file using git apply."""
        cwd = work_dir or os.path.dirname(file_path)
        patch_file = os.path.join(cwd, "_temp_patch.diff")

        try:
            with open(patch_file, 'w') as f:
                f.write(patch_content)

            result = subprocess.run(
                ["git", "apply", patch_file],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0
        except Exception:
            return False
        finally:
            if os.path.exists(patch_file):
                os.remove(patch_file)

    @staticmethod
    def format_patch_report(patch_content: str, original_path: str,
                            repaired_files: list[str], regression_ok: bool) -> dict:
        """Format a structured repair report."""
        return {
            "patch_applied": bool(patch_content.strip()),
            "original_file": original_path,
            "modified_files": repaired_files,
            "patch_content": patch_content,
            "patch_size_lines": len(patch_content.split('\n')),
            "regression_pass": regression_ok
        }
