"""Sandboxed execution environment for generated code."""

import os
import subprocess
import sys
from typing import Optional


class SandboxRunner:
    """Runs generated code in a sandboxed subprocess."""

    def __init__(self, work_dir: Optional[str] = None, timeout: int = 120):
        self.work_dir = work_dir or os.path.join(os.getcwd(), "execution_sandbox")
        self.timeout = timeout
        os.makedirs(self.work_dir, exist_ok=True)

    def run_python(self, script_path: str, args: list[str] = None) -> dict:
        """Execute a Python script in the sandbox."""
        if not os.path.exists(script_path):
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Script not found: {script_path}",
                "returncode": -1
            }

        cmd = [sys.executable, script_path] + (args or [])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:3000],
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution timed out after {self.timeout}s",
                "returncode": -1
            }

    def run_python_module(self, module_name: str, module_args: list[str] = None) -> dict:
        """Run a Python module (e.g., 'python -m pytest')."""
        cmd = [sys.executable, "-m", module_name] + (module_args or [])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.work_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:3000],
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution timed out after {self.timeout}s",
                "returncode": -1
            }

    def check_import(self, module_path: str) -> dict:
        """Check if a Python module can be imported."""
        module_name = os.path.splitext(os.path.basename(module_path))[0]
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import {module_name}; print(f'{module_name} imported OK')"],
                cwd=os.path.dirname(module_path) or self.work_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:500],
                "stderr": result.stderr[:500]
            }
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e)}
