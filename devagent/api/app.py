"""FastAPI server for DevAgent HTTP API with bidirectional WebSocket support.

Features:
  - REST endpoints for task creation, status, history
  - Bidirectional WebSocket for real-time interaction (V2)
  - Approval/command endpoints for REST fallback
  - Dashboard with live metrics
"""

import os
import json
import uuid
import re
import html as html_mod
import asyncio
import datetime
import zlib
import base64 as b64
import urllib.request as urllib_req
import urllib.error as urllib_err
import shutil
import zipfile
import io
from pathlib import Path as PathLib
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, BackgroundTasks, UploadFile, File
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# In-memory task history (limited to 100 entries)
TASK_HISTORY: list[dict] = []

async def _route_command(task_id: str, client_id: str, cmd_type,
                          session_mgr, msg: dict):
    """Route a command from WebSocket to the task's InteractionController.

    Args:
        task_id: Target task ID
        client_id: Sending client ID
        cmd_type: CommandType enum value
        session_mgr: SessionManager instance (may be None)
        msg: Original message dict with data payload
    """
    if not session_mgr:
        return
    from ..agentic.interaction import UserCommand
    cmd = UserCommand(
        type=cmd_type,
        data=msg.get("data", {}),
        client_id=client_id,
    )
    await session_mgr.send_command(task_id, client_id, cmd)


def _is_pipeline_task(task_desc: str) -> bool:
    """Detect if task should use PipelineRunner (code-driven flow).

    Pipeline tasks are: full pipeline, design, implement, repair, test.
    Agentic/exploratory tasks use the free-form ReAct loop.
    """
    pipeline_markers = [
        "FULL PIPELINE", "full pipeline",
        "Build the project", "build the project",
        "Analyze the requirements", "analyze the requirements",
        "debug_issue", "repair_code",
    ]
    return any(marker in task_desc for marker in pipeline_markers)


def _collect_pipeline_files(pstate) -> list[str]:
    """Collect all generated files from pipeline state."""
    files = []
    for r in getattr(pstate, 'results', []):
        for f in getattr(r, 'files_generated', []):
            if f not in files:
                files.append(f)
    return files


def _collect_pipeline_errors(pstate) -> list[dict]:
    """Collect errors from pipeline state."""
    errors = []
    for r in getattr(pstate, 'results', []):
        for e in getattr(r, 'errors', []):
            errors.append({"phase": getattr(r, 'phase', ''), "message": str(e)})
    if not getattr(pstate, 'results', []) and pstate.status == "FAILED":
        errors.append({"phase": "pipeline", "message": "Pipeline execution failed"})
    return errors


if HAS_FASTAPI:
    app = FastAPI(
        title="DevAgent API",
        description="DevAgent: LLM-based Software Engineering Agent API - IDE Integration",
        version="1.0.0"
    )

    # Read interactive mode from env var on module import
    import os as _os
    _DEFAULT_INTERACTIVE = _os.environ.get("DEVAGENT_INTERACTIVE", "off")

    @app.on_event("startup")
    async def _startup_set_mode():
        # Priority: env var > CLI args > default
        mode = _os.environ.get("DEVAGENT_INTERACTIVE", "")
        if not mode:
            import sys as _sys
            for i, a in enumerate(_sys.argv):
                if a in ("--interactive", "-I") and i + 1 < len(_sys.argv):
                    mode = _sys.argv[i + 1]; break
                if a.startswith("--interactive="):
                    mode = a.split("=", 1)[1]; break
        if not mode:
            mode = "off"
        app.state.interactive_mode = mode
        print(f"[DevAgent] Interactive mode set to: {mode}", flush=True)

        # Restore TASK_HISTORY from PersistenceStore (survives restarts)
        try:
            from ..agentic.persistence import PersistentStore
            persist = PersistentStore("./outputs/devagent.db")
            app.state._persist = persist
            tasks = persist.list_tasks(limit=50)
            for t in tasks:
                TASK_HISTORY.append({
                    "task_id": t["id"], "status": t["status"], "task_type": t["mode"],
                    "output_dir": t.get("output_dir", ""), "report_path": "",
                    "errors": [], "warnings": [], "metrics": {},
                    "iterations": t.get("phase_index", 0),
                    "duration_sec": 0.0, "created_at": t.get("created_at", ""),
                })
            if tasks:
                print(f"[DevAgent] Restored {len(tasks)} tasks from persistence DB", flush=True)
        except Exception as e:
            print(f"[DevAgent] Persistence init skipped: {e}", flush=True)

    # CORS for IDE embedded webviews
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve frontend static files
    _frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    if os.path.isdir(_frontend_dir):
        try:
            from fastapi.staticfiles import StaticFiles
            app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
            print(f"[DevAgent] Frontend served from: {_frontend_dir}", flush=True)
        except ImportError:
            print("[DevAgent] fastapi.staticfiles not available, frontend not mounted", flush=True)

    class TaskRequest(BaseModel):
        task: str  # design / implement / repair / full
        input: Optional[str] = ""
        code: Optional[str] = ""
        tests: Optional[str] = ""
        output: Optional[str] = "./outputs"
        max_retry: Optional[int] = 2
        language: Optional[str] = "python"

    class TaskResponse(BaseModel):
        task_id: str
        status: str
        output_dir: str
        report_path: str
        errors: list
        warnings: list
        metrics: dict

    class FileInfo(BaseModel):
        path: str
        language: str
        content: Optional[str] = None

    @app.get("/")
    def root():
        return {
            "service": "DevAgent API",
            "version": "1.0.0",
            "frontend": "/app/",
            "dashboard": "/dashboard",
            "api_docs": "/docs",
            "endpoints": {
                "health": "GET /health",
                "execute_task": "POST /api/v1/tasks",
                "task_design": "POST /api/v1/tasks/design",
                "task_implement": "POST /api/v1/tasks/implement",
                "task_repair": "POST /api/v1/tasks/repair",
                "task_full": "POST /api/v1/tasks/full",
                "task_status": "GET /api/v1/tasks/{task_id}",
                "task_stream": "WS /api/v1/tasks/{task_id}/stream",
                "task_history": "GET /api/v1/tasks/history",
                "analyze_file": "POST /api/v1/analyze/file",
                "project_structure": "GET /api/v1/project/structure",
            }
        }

    # Initialize dashboard
    from ..agentic.observability import DashboardAPI, StreamingServer, TaskHistoryManager
    _dashboard = DashboardAPI(TaskHistoryManager(), StreamingServer())

    @app.get("/health")
    def health():
        return {"status": "ok", "timestamp": datetime.datetime.now().isoformat()}

    @app.get("/dashboard")
    def dashboard_page():
        from fastapi.responses import HTMLResponse
        from ..agentic.observability import DASHBOARD_HTML
        return HTMLResponse(content=DASHBOARD_HTML)

    @app.get("/api/v1/dashboard/metrics")
    def dashboard_metrics():
        return _dashboard.history.get_metrics()

    @app.get("/api/v1/dashboard/trend")
    def dashboard_trend(metric: str = "success_rate"):
        return {"trend": _dashboard.history.get_trend(metric)}

    @app.get("/api/v1/dashboard/status")
    def dashboard_status():
        return _dashboard.history.get_metrics()

    @app.post("/api/v1/tasks", response_model=TaskResponse)
    def create_task(request: TaskRequest):
        """Execute a DevAgent task — V1 sync mode, per-task output dir."""
        from ..agent_core.schemas import TaskSpec
        from ..agent_core.workflow import WorkflowController

        input_val = request.input or ""
        code_val = request.code or ""
        tests_val = request.tests or ""

        # Give EVERY task its own output subdirectory (fix: prevent cross-task data leak)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        output_root = os.path.abspath(os.path.join(request.output or "./outputs", f"run_{task_id}"))
        os.makedirs(output_root, exist_ok=True)

        # If input is not a file path but looks like inline text, save to temp file
        if input_val and not os.path.isfile(input_val) and len(input_val) > 2:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.md', prefix='requirements_',
                dir=output_root, delete=False, encoding='utf-8'
            )
            tmp.write(input_val)
            tmp.close()
            input_val = tmp.name

        spec = TaskSpec(
            task_type=request.task,
            input_path=input_val,
            code_path=code_val,
            tests_path=tests_val,
            output_path=output_root,
            max_retry=request.max_retry or 2,
            language=request.language or "python"
        )

        valid, error_msg = spec.validate()
        if not valid:
            raise HTTPException(status_code=400, detail=error_msg)

        spec.output_root = output_root

        controller = WorkflowController()
        state = controller.execute(spec)

        # Fix: use the spec's task_id (which may differ from our generated one)
        real_task_id = state.task_id or task_id

        metrics = {}
        if state.test_results:
            metrics["test_collected"] = state.test_results.get("collected", 0)
            metrics["test_passed"] = state.test_results.get("passed", 0)
            metrics["test_failed"] = state.test_results.get("failed", 0)

        if state.repair_patch:
            metrics["files_modified"] = len(state.repair_patch.get("modified_files", []))
            rg = state.repair_patch.get("regression_results", {})
            if rg:
                metrics["regression_passed"] = rg.get("passed", 0)

        response = TaskResponse(
            task_id=real_task_id,
            status=state.status,
            output_dir=output_root,
            report_path=state.final_report or "",
            errors=state.errors,
            warnings=state.warnings,
            metrics=metrics
        )

        # Record history
        TASK_HISTORY.insert(0, response.dict())
        if len(TASK_HISTORY) > 100:
            TASK_HISTORY.pop()

        # Track output dir for download
        app.state._task_outputs[real_task_id] = output_root

        return response

    @app.post("/api/v1/tasks/design")
    def task_design(input: str = "", output: str = "./outputs"):
        """Execute design mode task."""
        return create_task(TaskRequest(task="design", input=input, output=output))

    @app.post("/api/v1/tasks/implement")
    def task_implement(input: str = "", output: str = "./outputs"):
        """Execute implement mode task."""
        return create_task(TaskRequest(task="implement", input=input, output=output))

    @app.post("/api/v1/tasks/repair")
    def task_repair(code: str = "", tests: str = "", output: str = "./outputs"):
        """Execute repair mode task."""
        return create_task(TaskRequest(task="repair", code=code, tests=tests, output=output))

    @app.post("/api/v1/tasks/full")
    def task_full(input: str = "", output: str = "./outputs", max_retry: int = 2):
        """Execute full mode task."""
        return create_task(TaskRequest(task="full", input=input, output=output, max_retry=max_retry))

    @app.post("/api/v1/tasks/agentic")
    def task_agentic(description: str = "", workspace: str = ".", output: str = "./outputs"):
        """Execute agentic mode task (V2)."""
        return create_task(TaskRequest(task="agentic", input=description, output=output, code=workspace))

    @app.get("/api/v1/tasks/history")
    def get_task_history():
        """Get recent task execution history."""
        return {"tasks": TASK_HISTORY, "total": len(TASK_HISTORY)}

    @app.get("/api/v1/tasks/{task_id}")
    def get_task_status(task_id: str):
        """Get status of a specific task by ID, including phase progress."""
        # Check TASK_HISTORY first
        for task in TASK_HISTORY:
            if task.get("task_id") == task_id:
                # If task is RUNNING, check background progress
                if task.get("status") == "RUNNING":
                    bg = getattr(app.state, '_bg_tasks', {}).get(task_id, {})
                    if bg:
                        task = dict(task)
                        task["status"] = bg.get("status", "RUNNING")
                        tr = bg.get("test_results") or {}
                        task["metrics"] = task.get("metrics", {}) or {}
                        task["metrics"]["test_passed"] = tr.get("passed", 0)
                        task["metrics"]["test_failed"] = tr.get("failed", 0)
                        task["metrics"]["test_collected"] = tr.get("collected", 0)
                        task["metrics"]["files_modified"] = len(bg.get("modified_files", []))
                        task["metrics"]["iterations"] = bg.get("iterations", 0)
                        task["current_phase"] = bg.get("phase", "")
                        if bg.get("errors"):
                            task["errors"] = bg["errors"]

                # Enrich with phase progress from THIS TASK'S filesystem ONLY
                output_dir = _resolve_output_dir(task_id)
                # NEVER fall back to task.get("output_dir") — that's the shared root
                if output_dir and os.path.isdir(output_dir):
                    phase_order = ['requirements', 'design', 'implementation', 'tests', 'repair', 'reports']
                    phases_done = []
                    for pdir, pname in [('01_requirements','requirements'), ('02_design','design'),
                        ('03_implementation','implementation'), ('04_tests','tests'),
                        ('05_repair','repair'), ('06_reports','reports')]:
                        pp = os.path.join(output_dir, pdir)
                        if os.path.isdir(pp) and os.listdir(pp):
                            phases_done.append(pname)
                    task["phases_completed"] = phases_done
                    task["current_phase"] = task.get("current_phase") or (phase_order[len(phases_done)] if len(phases_done) < len(phase_order) else '')
                    task["phase_index"] = len(phases_done)
                    task["total_phases"] = len(phase_order)

                return task
        # Check _bg_tasks as fallback
        bg_tasks = getattr(app.state, '_bg_tasks', {})
        if task_id in bg_tasks:
            bg = bg_tasks[task_id]
            output_dir = _resolve_output_dir(task_id)
            phases_done = []
            if output_dir and os.path.isdir(output_dir):
                for pdir, pname in [('01_requirements','requirements'), ('02_design','design'),
                    ('03_implementation','implementation'), ('04_tests','tests'),
                    ('05_repair','repair'), ('06_reports','reports')]:
                    pp = os.path.join(output_dir, pdir)
                    if os.path.isdir(pp) and os.listdir(pp):
                        phases_done.append(pname)
            return {
                "task_id": task_id,
                "status": bg.get("status", "UNKNOWN"),
                "output_dir": output_dir,
                "report_path": "",
                "errors": bg.get("errors", []),
                "warnings": [],
                "phases_completed": phases_done,
                "current_phase": bg.get("phase", ""),
                "phase_index": len(phases_done),
                "total_phases": 6,
                "metrics": {
                    "iterations": bg.get("iterations", 0),
                    "files_modified": len(bg.get("modified_files", [])),
                },
            }
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    @app.post("/api/v1/analyze/file")
    def analyze_file(file: FileInfo):
        """Analyze a single file for issues using DevAgent LLM analysis."""
        try:
            from ..agent_core.llm_client import LLMClient
            from ..agent_core.config_loader import load_config, get_llm_config
            config = load_config()
            llm_config = get_llm_config(config)
            llm = LLMClient(llm_config)

            from ..lsp.server import CODE_ANALYSIS_PROMPT
            content = file.content or ""
            max_chars = 8000
            code = content[:max_chars]
            if len(content) > max_chars:
                code += "\n\n# ... [file truncated]"

            result = llm.chat_structured(
                messages=[{"role": "user", "content": f"Analyze this code for bugs and issues:\n\n```\n{code}\n```"}],
                system_prompt=CODE_ANALYSIS_PROMPT
            )
            issues = result.get("issues", [])
        except Exception as e:
            # Fallback to static analyzer if LLM fails
            from ..tools.static_analyzer import StaticAnalyzer
            analyzer = StaticAnalyzer()
            issues = analyzer.analyze_code(file.content or "", file.language, file.path)

        return {"file": file.path, "language": file.language, "issues": issues, "count": len(issues)}

    @app.get("/api/v1/project/structure")
    def get_project_structure(path: str = "."):
        """Get project directory structure for IDE display."""
        import fnmatch
        ignored_patterns = [
            ".git", "__pycache__", "*.pyc", ".pytest_cache",
            "venv", ".venv", "node_modules", ".egg-info",
            ".DS_Store", "*.docx", "outputs"
        ]
        def should_ignore(name):
            return any(fnmatch.fnmatch(name, p) for p in ignored_patterns)

        structure = []
        base = os.path.abspath(path)
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not should_ignore(d)]
            rel = os.path.relpath(root, base)
            if rel == ".":
                for f in sorted(files):
                    if not should_ignore(f):
                        structure.append(os.path.join(root, f))
            else:
                level = rel.replace(os.sep, "/")
                for f in sorted(files):
                    if not should_ignore(f):
                        structure.append(f"{level}/{f}")
        return {"root": base, "files": sorted(structure), "count": len(structure)}

    @app.get("/api/v1/file/read")
    def read_file_content(path: str = "", max_lines: int = 2000):
        """Read a file's content. Blocks paths outside workspace for safety."""
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        # Safety: only allow files within ./outputs or current workspace
        abs_path = os.path.abspath(path)
        allowed_prefixes = [
            os.path.abspath("./outputs"),
            os.path.abspath("."),
            "/tmp", os.path.sep + "tmp",
        ]
        if not any(abs_path.startswith(p) for p in allowed_prefixes if os.path.isdir(p)):
            raise HTTPException(status_code=403, detail="Access denied: file outside workspace")
        try:
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            total = len(lines)
            content = ''.join(lines[:max_lines])
            return {"path": abs_path, "total_lines": total, "content": content, "truncated": total > max_lines}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ==================================================================
    # File Upload — upload requirements / input files
    # ==================================================================

    # Track output directories per task for download
    if not hasattr(app.state, '_task_outputs'):
        app.state._task_outputs = {}

    def _zip_dir(dir_path: str) -> io.BytesIO:
        """Create an in-memory ZIP archive of a directory."""
        buf = io.BytesIO()
        base = os.path.abspath(dir_path)
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(base):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    if f.startswith('.'):
                        continue
                    fp = os.path.join(root, f)
                    arcname = os.path.relpath(fp, base)
                    zf.write(fp, arcname)
        buf.seek(0)
        return buf

    @app.post("/api/v1/upload")
    async def upload_file(file: UploadFile = File(...)):
        """Upload a requirements file. Returns the saved file path.

        Supports: .md, .txt, .py, .json, .yaml, .yml
        """
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        # Validate extension
        ext = os.path.splitext(file.filename)[1].lower()
        allowed = {'.md', '.txt', '.py', '.json', '.yaml', '.yml', '.rst'}
        if ext not in allowed:
            raise HTTPException(status_code=400,
                detail=f"Unsupported file type: {ext}. Allowed: {', '.join(allowed)}")

        # Save to outputs/uploads/
        upload_dir = os.path.abspath("./outputs/uploads")
        os.makedirs(upload_dir, exist_ok=True)

        # Sanitize filename and avoid collisions
        safe_name = file.filename.replace(' ', '_').replace('/', '_')
        file_id = uuid.uuid4().hex[:8]
        saved_name = f"{file_id}_{safe_name}"
        saved_path = os.path.join(upload_dir, saved_name)

        content = await file.read()
        with open(saved_path, 'wb') as f:
            f.write(content)

        # Try to decode as text for preview
        try:
            text_preview = content.decode('utf-8')[:500]
        except UnicodeDecodeError:
            text_preview = "[二进制文件]"

        return {
            "status": "ok",
            "filename": file.filename,
            "saved_path": saved_path,
            "saved_name": saved_name,
            "size_bytes": len(content),
            "preview": text_preview,
        }

    # Shared output resolver (plain function, not a route)
    def _resolve_output_dir(task_id: str) -> str:
        """Resolve output directory for a task. NEVER falls back to shared dir."""
        task_outputs = getattr(app.state, '_task_outputs', {})
        out = task_outputs.get(task_id, "")
        candidates = [out, os.path.abspath(f"./outputs/run_{task_id}")]

        # Also scan for any directory containing this task_id
        outputs_root = os.path.abspath("./outputs")
        if os.path.isdir(outputs_root):
            for entry in os.listdir(outputs_root):
                full = os.path.join(outputs_root, entry)
                if os.path.isdir(full) and task_id in entry and entry.startswith('run_'):
                    candidates.append(full)

        for d in candidates:
            if d and os.path.isdir(d):
                # CRITICAL: Only accept if phase dirs actually exist inside
                if os.path.isdir(os.path.join(d, '01_requirements')) or \
                   os.path.isdir(os.path.join(d, '02_design')):
                    return d
        return ""

    _phase_dir_map = {
        'requirements': '01_requirements', 'design': '02_design',
        'implementation': '03_implementation', 'tests': '04_tests',
        'repair': '05_repair', 'reports': '06_reports',
    }

    @app.get("/api/v1/tasks/{task_id}/download")
    def download_full_project(task_id: str):
        """Download the entire project output as a ZIP file."""
        output_dir = _resolve_output_dir(task_id)
        if not output_dir:
            raise HTTPException(status_code=404,
                detail=f"No output directory found for task {task_id}")
        buf = _zip_dir(output_dir)
        zip_name = f"DevAgent_{task_id}.zip"
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})

    @app.get("/api/v1/tasks/{task_id}/download/{phase}")
    def download_phase(task_id: str, phase: str):
        """Download a specific pipeline phase output as ZIP."""
        if phase not in _phase_dir_map:
            raise HTTPException(status_code=400,
                detail=f"Invalid phase: {phase}. Valid: {', '.join(_phase_dir_map.keys())}")
        output_dir = _resolve_output_dir(task_id)
        if not output_dir:
            raise HTTPException(status_code=404,
                detail=f"No output directory found for task {task_id}")
        phase_dir = os.path.join(output_dir, _phase_dir_map[phase])
        if not os.path.isdir(phase_dir):
            raise HTTPException(status_code=404,
                detail=f"Phase '{phase}' output not found for task {task_id}")
        buf = _zip_dir(phase_dir)
        zip_name = f"DevAgent_{task_id}_{phase}.zip"
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})

    @app.get("/api/v1/tasks/{task_id}/phases")
    def list_phases(task_id: str):
        """List available phases and their file counts for a task."""
        output_dir = _resolve_output_dir(task_id)
        rev_map = {v: k for k, v in _phase_dir_map.items()}
        phases = []
        if output_dir and os.path.isdir(output_dir):
            for entry in sorted(os.listdir(output_dir)):
                ep = os.path.join(output_dir, entry)
                if os.path.isdir(ep) and not entry.startswith('.'):
                    pname = rev_map.get(entry, entry)
                    files = [f for f in os.listdir(ep) if not f.startswith('.')]
                    phases.append({
                        "name": pname, "directory": entry, "file_count": len(files),
                        "files": files[:20],
                        "size_kb": round(sum(
                            os.path.getsize(os.path.join(ep, f))
                            for f in files if os.path.isfile(os.path.join(ep, f))) / 1024, 1),
                    })
        return {"task_id": task_id, "output_dir": output_dir, "phases": phases}

    # ==================================================================
    # Document Serving — serve generated docs, diagrams, and structured data
    # ==================================================================

    @app.get("/api/v1/tasks/{task_id}/document/{phase}")
    def get_phase_document(task_id: str, phase: str):
        """Get the generated document (markdown) for a specific phase.

        Phases: requirements, design, implementation, tests, repair, reports
        Returns: { content: "...", format: "markdown", filename: "..." }
        """
        output_dir = _resolve_output_dir(task_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail=f"No output for task {task_id}")

        phase_dirs = {
            'requirements': '01_requirements',
            'design': '02_design',
            'implementation': '03_implementation',
            'tests': '04_tests',
            'repair': '05_repair',
            'reports': '06_reports',
        }
        if phase not in phase_dirs:
            raise HTTPException(status_code=400,
                detail=f"Invalid phase: {phase}. Valid: {', '.join(phase_dirs.keys())}")

        phase_dir = os.path.join(output_dir, phase_dirs[phase])
        if not os.path.isdir(phase_dir):
            raise HTTPException(status_code=404, detail=f"Phase '{phase}' not found")

        # Find the main document: prefer .md files, then .json index
        files = sorted(os.listdir(phase_dir))
        doc_content = None
        doc_filename = None

        # Priority: *_spec.md > *_analysis.md > *_design.md > *.md > index.json
        priority_patterns = [
            r'.*_spec\.md$', r'.*specification\.md$', r'.*_analysis\.md$',
            r'.*_design\.md$', r'.*_report\.md$', r'.*executive_report\.md$',
            r'.*\.md$',
        ]
        for pat in priority_patterns:
            for f in files:
                if re.match(pat, f, re.IGNORECASE) and not f.startswith('.'):
                    fp = os.path.join(phase_dir, f)
                    try:
                        with open(fp, 'r', encoding='utf-8') as fh:
                            doc_content = fh.read()
                        doc_filename = f
                        break
                    except Exception:
                        continue
            if doc_content:
                break

        if not doc_content:
            raise HTTPException(status_code=404,
                detail=f"No document found for phase '{phase}'")

        return {
            "task_id": task_id,
            "phase": phase,
            "filename": doc_filename,
            "format": "markdown",
            "content": doc_content,
        }

    @app.get("/api/v1/tasks/{task_id}/diagrams/{phase}")
    def get_phase_diagrams(task_id: str, phase: str):
        """Get all Mermaid/PUML diagram code for a phase.

        Returns: { diagrams: [{ name, type, code, format }] }
        """
        output_dir = _resolve_output_dir(task_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail=f"No output for task {task_id}")

        phase_dirs = {
            'requirements': '01_requirements',
            'design': '02_design',
            'implementation': '03_implementation',
            'tests': '04_tests',
            'repair': '05_repair',
            'reports': '06_reports',
        }
        if phase not in phase_dirs:
            raise HTTPException(status_code=400,
                detail=f"Invalid phase: {phase}. Valid: {', '.join(phase_dirs.keys())}")

        phase_dir = os.path.join(output_dir, phase_dirs[phase])
        if not os.path.isdir(phase_dir):
            raise HTTPException(status_code=404, detail=f"Phase '{phase}' not found")

        diagrams = []
        for f in sorted(os.listdir(phase_dir)):
            if f.startswith('.'):
                continue
            fp = os.path.join(phase_dir, f)
            ext = os.path.splitext(f)[1].lower()
            if ext not in ('.mmd', '.puml', '.md'):
                continue
            try:
                with open(fp, 'r', encoding='utf-8') as fh:
                    code = fh.read()
            except Exception:
                continue

            # Determine diagram type from filename and content
            name = os.path.splitext(f)[0]
            fmt = 'mermaid' if ext == '.mmd' or ('```mermaid' in code) else 'plantuml' if ext == '.puml' else 'mermaid'
            dtype = 'unknown'
            for kw in ['class', 'er', 'sequence', 'state', 'flowchart', 'usecase', 'activity', 'dfd', 'component', 'deployment']:
                if kw in name.lower() or kw in code[:200].lower():
                    dtype = kw
                    break

            # Extract Mermaid code from markdown blocks
            if '```mermaid' in code:
                m = re.search(r'```mermaid\s*\n(.*?)```', code, re.DOTALL)
                if m:
                    code = m.group(1).strip()
                    fmt = 'mermaid'
            elif code.strip().startswith('@startuml'):
                fmt = 'plantuml'

            diagrams.append({
                "name": name,
                "type": dtype,
                "filename": f,
                "format": fmt,
                "code": code,
            })

        # Also extract diagrams embedded in markdown documents
        for f in sorted(os.listdir(phase_dir)):
            if not f.endswith('.md') or f.startswith('.'):
                continue
            fp = os.path.join(phase_dir, f)
            try:
                with open(fp, 'r', encoding='utf-8') as fh:
                    md_content = fh.read()
            except Exception:
                continue

            # Find all mermaid code blocks in the markdown
            for idx, m in enumerate(re.finditer(r'```(?:mermaid|mmd)\s*\n(.*?)```', md_content, re.DOTALL)):
                code = m.group(1).strip()
                if len(code) < 20:
                    continue
                # Try to guess diagram type from surrounding text or code
                dtype = 'unknown'
                for kw in ['classDiagram', 'erDiagram', 'sequenceDiagram', 'stateDiagram', 'flowchart']:
                    if kw in code[:50]:
                        dtype = kw.replace('Diagram', '').lower()
                        break
                diagrams.append({
                    "name": f"{os.path.splitext(f)[0]}_embedded_{idx+1}",
                    "type": dtype,
                    "filename": f,
                    "format": "mermaid",
                    "code": code,
                })

        return {"task_id": task_id, "phase": phase, "diagrams": diagrams}

    # ==================================================================
    # Diagram Rendering — server-side via Kroki with syntax fixing
    # ==================================================================

    def _fix_mermaid(code: str) -> str:
        """Auto-fix common LLM-generated Mermaid syntax errors.
        Applied before rendering to improve success rate.
        """
        lines = code.split('\n')
        fixed = []

        for line in lines:
            # Fix 1: Unquoted labels with special chars in node definitions
            # e.g.  Node[Some Label with spaces]  →  Node["Some Label with spaces"]
            # But don't break already-quoted labels
            m = re.match(r'^(\s*)(\w+)(\[(?!["\']).*\])', line)
            if m:
                indent, nid, label = m.group(1), m.group(2), m.group(3)
                inner = label[1:-1]  # strip [ and ]
                # Only quote if contains special chars
                if any(c in inner for c in ' ()，。、；：！？｛｝【】《》（）'):
                    line = f'{indent}{nid}["{inner}"]'

            # Fix 2: Single-quote issues — convert to double quotes in labels
            # Fix 3: Chinese quotes in labels
            line = line.replace('"', "'").replace('"', "'")

            # Fix 4: classDiagram class names with spaces
            if line.strip().startswith('class ') and 'classDiagram' not in line:
                cls_match = re.match(r'(\s*class\s+)([^{]+)(.*)', line)
                if cls_match:
                    prefix, name, rest = cls_match.group(1), cls_match.group(2).strip(), cls_match.group(3)
                    if ' ' in name and not name.startswith('`'):
                        line = f'{prefix}`{name}`{rest}'

            # Fix 5: stateDiagram — state names with spaces need quotes
            if 'stateDiagram' not in line and ' --> ' in line:
                parts = line.split(' --> ')
                if len(parts) == 2:
                    src = parts[0].strip()
                    dst = parts[1].strip()
                    # Only fix if it's a state machine transition with unquoted spaces
                    # Leave sequence diagram participant names alone

            # Fix 6: flowchart edge labels with unescaped pipe chars
            # e.g.  A -->|label with | inside| B  — rare, skip for now

            # Fix 7: Empty lines in the middle of a class definition
            # (already handled by the diff; keep as-is)

            # Fix 8: node IDs with . / inside them (from file paths)
            if 'flowchart' in code[:50].lower() or 'graph ' in code[:50].lower():
                m = re.match(r'^(\s*)([a-zA-Z0-9_./-]+)(\[.*\]|\{.*\}|\(\(.*\)\)|\(.*\))', line)
                if m:
                    nid = m.group(2)
                    if '.' in nid or '/' in nid:
                        safe_id = nid.replace('.', '_').replace('/', '_')
                        line = line.replace(nid, safe_id, 1)

            fixed.append(line)

        return '\n'.join(fixed)

    class RenderRequest(BaseModel):
        code: str = ""
        format: str = "mermaid"

    @app.post("/api/v1/diagrams/render")
    async def render_diagram(request: RenderRequest):
        """Render a Mermaid/PUML diagram to SVG via Kroki.io.
        Auto-fixes common LLM syntax errors. Falls back gracefully.

        Accepts JSON: { code: "...", format: "mermaid" | "plantuml" }
        Returns: { format: "svg", svg: "<svg>...", fixed: bool }
        """
        code = (request.code or "").strip()
        fmt = (request.format or "mermaid").strip()

        if not code:
            raise HTTPException(status_code=400, detail="No diagram code provided")

        # Strip markdown fences
        fm = re.search(r'```(?:mermaid|mmd|plantuml)?\s*\n(.*?)```', code, re.DOTALL)
        if fm:
            code = fm.group(1).strip()

        if not code:
            raise HTTPException(status_code=400, detail="Empty diagram after stripping fences")

        # Auto-fix for Mermaid
        was_fixed = False
        if fmt == 'mermaid':
            fixed_code = _fix_mermaid(code)
            if fixed_code != code:
                was_fixed = True
                code = fixed_code

        # Render via Kroki
        try:
            encoded = b64.urlsafe_b64encode(
                zlib.compress(code.encode('utf-8'), 9)
            ).decode('ascii')
            url = f"https://kroki.io/{fmt}/svg/{encoded}"
            req = urllib_req.Request(url, headers={'User-Agent': 'DevAgent/3.4'})
            with urllib_req.urlopen(req, timeout=20) as resp:
                svg_data = resp.read().decode('utf-8')

            # Validate it's real SVG
            if '<svg' not in svg_data[:200]:
                raise HTTPException(status_code=500,
                    detail=f"Kroki returned non-SVG: {svg_data[:200]}")

            return {"format": "svg", "svg": svg_data, "fixed": was_fixed}
        except HTTPException:
            raise
        except urllib_err.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')[:300]
            raise HTTPException(status_code=422,
                detail=f"Kroki render error: {err_body}")
        except Exception as e:
            raise HTTPException(status_code=500,
                detail=f"Rendering failed: {str(e)}")

    @app.post("/api/v1/diagrams/batch-render")
    async def batch_render_diagrams(request: dict = None):
        """Render multiple diagrams at once. Body: { diagrams: [{ code, format }] }
        Returns: { results: [{ svg, error?, fixed }] }
        """
        from fastapi import Request

        body = request or {}
        diagrams = body.get('diagrams', []) if isinstance(body, dict) else []

        if not diagrams:
            raise HTTPException(status_code=400, detail="No diagrams provided")

        results = []
        for i, d in enumerate(diagrams):
            code = (d.get('code', '') or '').strip()
            fmt = (d.get('format', '') or 'mermaid').strip()

            fm = re.search(r'```(?:mermaid|mmd)?\s*\n(.*?)```', code, re.DOTALL)
            if fm:
                code = fm.group(1).strip()

            if not code:
                results.append({"index": i, "error": "Empty code", "svg": ""})
                continue

            was_fixed = False
            if fmt == 'mermaid':
                fixed_code = _fix_mermaid(code)
                if fixed_code != code:
                    was_fixed = True
                    code = fixed_code

            try:
                encoded = b64.urlsafe_b64encode(
                    zlib.compress(code.encode('utf-8'), 9)
                ).decode('ascii')
                url = f"https://kroki.io/{fmt}/svg/{encoded}"
                req = urllib_req.Request(url, headers={'User-Agent': 'DevAgent/3.4'})
                with urllib_req.urlopen(req, timeout=15) as resp:
                    svg_data = resp.read().decode('utf-8')
                results.append({
                    "index": i, "svg": svg_data, "fixed": was_fixed, "format": "svg",
                })
            except Exception as e:
                results.append({
                    "index": i, "error": str(e)[:200], "svg": "", "fixed": was_fixed,
                })

        return {"results": results, "total": len(diagrams), "success": sum(1 for r in results if r.get('svg'))}

    @app.get("/api/v1/tasks/{task_id}/structured/{phase}")
    def get_phase_structured_data(task_id: str, phase: str):
        """Get the structured JSON data for a phase (from index.json or similar).

        Returns parsed JSON content or { data: {}, filename: "..." }
        """
        output_dir = _resolve_output_dir(task_id)
        if not output_dir:
            raise HTTPException(status_code=404, detail=f"No output for task {task_id}")

        phase_dirs = {
            'requirements': '01_requirements',
            'design': '02_design',
            'implementation': '03_implementation',
            'tests': '04_tests',
            'repair': '05_repair',
            'reports': '06_reports',
        }
        if phase not in phase_dirs:
            raise HTTPException(status_code=400,
                detail=f"Invalid phase: {phase}. Valid: {', '.join(phase_dirs.keys())}")

        phase_dir = os.path.join(output_dir, phase_dirs[phase])
        if not os.path.isdir(phase_dir):
            raise HTTPException(status_code=404, detail=f"Phase '{phase}' not found")

        # Look for: index.json, structured_*.json, *.json (prefer ones with more data)
        files = sorted(os.listdir(phase_dir))
        best_file = None
        best_content = {}
        best_size = 0

        for f in files:
            if not f.endswith('.json') or f.startswith('.'):
                continue
            fp = os.path.join(phase_dir, f)
            try:
                with open(fp, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                sz = len(json.dumps(data))
                if sz > best_size:
                    best_size = sz
                    best_content = data
                    best_file = f
            except Exception:
                continue

        if not best_file:
            raise HTTPException(status_code=404,
                detail=f"No structured data found for phase '{phase}'")

        return {
            "task_id": task_id, "phase": phase,
            "filename": best_file, "data": best_content,
        }

    # ==================================================================
    # Event Replay — for Run Replay page
    # ==================================================================

    @app.get("/api/v2/tasks/{task_id}/events")
    def get_task_events(task_id: str, after: int = 0, limit: int = 500):
        """Get execution events for a task (for Run Replay page).
        Query params: after=0 (sequence number to start after), limit=500
        """
        persist = getattr(app.state, '_persist', None)
        if not persist:
            events = []
            # Fallback: build from _bg_tasks and phases
            bg = getattr(app.state, '_bg_tasks', {}).get(task_id, {})
            if bg:
                import datetime as _dt
                now = _dt.datetime.now().isoformat()
                seq = 0
                if bg.get('task_type'):
                    events.append({"id":"evt_0","task_id":task_id,"event_type":"TASK_CREATED","payload":{"task_id":task_id},"sequence_no":0,"created_at":now})
                if bg.get('errors'):
                    for e in bg['errors']:
                        seq += 1
                        events.append({"id":f"evt_{seq}","task_id":task_id,"event_type":"ERROR","payload":{"error":str(e)[:200]},"sequence_no":seq,"created_at":now})
                seq += 1
                events.append({"id":f"evt_{seq}","task_id":task_id,"event_type":"TASK_FINISHED","payload":{"status":bg.get("status","")},"sequence_no":seq,"created_at":now})
            return {"task_id": task_id, "events": events, "total": len(events)}

        events = persist.get_events(task_id, after_sequence=after, limit=limit)
        return {"task_id": task_id, "events": events, "total": len(events)}

    # ==================================================================
    # V2: Bidirectional WebSocket — real interactive session
    # ==================================================================

    @app.websocket("/api/v2/tasks/{task_id}/interactive")
    async def interactive_session(websocket: WebSocket, task_id: str,
                                   mode: str = "observe"):
        """Bidirectional WebSocket for real-time task interaction.

        Client receives: progress snapshots, approval requests, agent questions, tool events
        Client sends: commands (pause/resume/abort/redirect/inject),
                      approval responses, question answers

        Query params:
            mode: "controller" (can send commands) or "observe" (read-only, default)
            session_token: Optional token for reconnection
        """
        await websocket.accept()

        from ..agentic.session import SessionManager, ClientRole, ClientTransport
        from ..agentic.interaction import UserCommand, CommandType

        # Get session manager from app state (or create a simple in-process one)
        session_mgr = getattr(app.state, 'session_manager', None)
        use_real_session = session_mgr is not None

        if use_real_session:
            role = ClientRole.CONTROLLER if mode == "controller" else ClientRole.OBSERVER
            client = await session_mgr.register_client(
                task_id, role=role, transport="websocket"
            )
        else:
            client_id = f"ws_{uuid.uuid4().hex[:8]}"
            client = None  # Fallback: no session manager

        try:
            # Send connection acknowledgment
            await websocket.send_json({
                "type": "session.created",
                "task_id": task_id,
                "data": {
                    "client_id": client.client_id if client else client_id,
                    "mode": mode,
                    "message": "Connected to interactive session",
                },
                "timestamp": datetime.datetime.now().isoformat(),
            })

            if use_real_session and client:
                # Background task: forward events to this WebSocket client
                async def event_forwarder():
                    while client.is_connected:
                        try:
                            event = await asyncio.wait_for(
                                client.message_queue.get(), timeout=1.0
                            )
                            # Skip internal _seq field
                            clean_event = {k: v for k, v in event.items()
                                          if not k.startswith("_")}
                            await websocket.send_json(clean_event)
                        except asyncio.TimeoutError:
                            continue
                        except WebSocketDisconnect:
                            break
                        except Exception:
                            break

                forwarder_task = asyncio.create_task(event_forwarder())
            else:
                forwarder_task = None

            # Main loop: receive commands/messages from client
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                except asyncio.TimeoutError:
                    # Send heartbeat
                    try:
                        await websocket.send_json({
                            "type": "heartbeat",
                            "task_id": task_id,
                            "timestamp": datetime.datetime.now().isoformat(),
                        })
                    except Exception:
                        break
                    continue
                except WebSocketDisconnect:
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": "Invalid JSON"},
                        "timestamp": datetime.datetime.now().isoformat(),
                    })
                    continue

                msg_type = msg.get("type", "")

                # Route commands to InteractionController via SessionManager
                if msg_type == "command.pause":
                    await _route_command(task_id, client_id, CommandType.PAUSE,
                                        session_mgr, msg)
                elif msg_type == "command.resume":
                    await _route_command(task_id, client_id, CommandType.RESUME,
                                        session_mgr, msg)
                elif msg_type == "command.abort":
                    await _route_command(task_id, client_id, CommandType.ABORT,
                                        session_mgr, msg)
                elif msg_type == "command.retry":
                    await _route_command(task_id, client_id, CommandType.RETRY,
                                        session_mgr, msg)
                elif msg_type == "command.redirect":
                    await _route_command(task_id, client_id, CommandType.REDIRECT,
                                        session_mgr, msg)
                elif msg_type == "command.inject":
                    await _route_command(task_id, client_id,
                                        CommandType.INJECT_CONTEXT, session_mgr, msg)
                elif msg_type == "approval.response":
                    approval_id = msg.get("data", {}).get("approval_id", "")
                    resolution = msg.get("data", {}).get("resolution", "deny")
                    note = msg.get("data", {}).get("note", "")
                    # Try ThreadChannel first (for background agents)
                    tc = getattr(app.state, '_thread_channels', {}).get(task_id)
                    if tc and tc.resolve(approval_id, resolution, note):
                        pass  # resolved in background thread
                    else:
                        controller = session_mgr.get_controller(task_id) if session_mgr else None
                        if controller:
                            await controller.resolve_approval(approval_id, resolution, note)
                elif msg_type == "question.response":
                    question_id = msg.get("data", {}).get("question_id", "")
                    answer = msg.get("data", {}).get("answer", "")
                    tc = getattr(app.state, '_thread_channels', {}).get(task_id)
                    if tc and tc.resolve(question_id, "answered", answer):
                        pass
                    else:
                        controller = session_mgr.get_controller(task_id) if session_mgr else None
                        if controller:
                            await controller.resolve_approval(question_id, answer)
                elif msg_type == "review.response":
                    review_id = msg.get("data", {}).get("review_id", "")
                    decision = msg.get("data", {}).get("decision", "approve")
                    feedback = msg.get("data", {}).get("feedback", "")
                    suggestions = msg.get("data", {}).get("suggestions", [])
                    # Try ThreadChannel first
                    tc = getattr(app.state, '_thread_channels', {}).get(task_id)
                    if tc and tc.resolve(review_id, decision, feedback, suggestions):
                        pass  # resolved in bg thread
                    else:
                        controller = session_mgr.get_controller(task_id) if session_mgr else None
                        if controller and controller.review_gate:
                            controller.review_gate.resolve_review(
                                review_id, decision, feedback, suggestions
                            )
                elif msg_type == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.datetime.now().isoformat(),
                    })
                else:
                    # Unknown message type — echo for debugging
                    await websocket.send_json({
                        "type": "echo",
                        "data": {"received": msg_type},
                        "timestamp": datetime.datetime.now().isoformat(),
                    })

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if use_real_session and client:
                client.is_connected = False
                await session_mgr.unregister_client(task_id, client.client_id)
            if forwarder_task:
                forwarder_task.cancel()
            try:
                await websocket.close()
            except Exception:
                pass

    # ==================================================================
    # V1 Compat: Legacy streaming (now forwards real events)
    # ==================================================================

    @app.websocket("/api/v1/tasks/{task_id}/stream")
    async def task_stream(websocket: WebSocket, task_id: str):
        """Legacy WebSocket — now delegates to real interactive stream."""
        await interactive_session(websocket, task_id, mode="observe")

    # ==================================================================
    # V2 REST: Approval and command endpoints (fallback for non-WS clients)
    # ==================================================================

    @app.get("/api/v2/tasks/{task_id}/pending-approval")
    def get_pending_approval(task_id: str):
        """Get current pending approval for a task (REST fallback)."""
        session_mgr = getattr(app.state, 'session_manager', None)
        if session_mgr:
            controller = session_mgr.get_controller(task_id)
            if controller and hasattr(controller, 'get_pending_approval'):
                pending = controller.get_pending_approval()
                if pending:
                    return {"has_pending": True, "approval": pending}
        return {"has_pending": False}

    @app.post("/api/v2/tasks/{task_id}/approve")
    def approve_action(task_id: str,
                       approval_id: str = Query(""),
                       resolution: str = Query("approve"),
                       note: str = Query("")):
        """Approve or deny a pending action via REST (for non-WS clients)."""
        session_mgr = getattr(app.state, 'session_manager', None)
        if not session_mgr and not getattr(app.state, "interactive_mode", "off") != "off":
            raise HTTPException(status_code=503, detail="Interactive mode not enabled")

        controller = session_mgr.get_controller(task_id)
        if not controller:
            raise HTTPException(status_code=404, detail="Task not found")

        if resolution not in ("approve", "deny", "approve_all"):
            raise HTTPException(status_code=400,
                               detail="Resolution must be: approve, deny, approve_all")

        asyncio.ensure_future(
            controller.resolve_approval(approval_id, resolution, note)
        )
        return {"status": "ok", "task_id": task_id,
                "approval_id": approval_id, "resolution": resolution}

    @app.post("/api/v2/tasks/{task_id}/command")
    def send_command(task_id: str,
                     command: str = Query("", description="pause/resume/abort/retry/redirect/inject"),
                     focus: str = Query("", description="New focus for redirect command"),
                     context: str = Query("", description="Context to inject"),
                     hint: str = Query("", description="Hint for retry command")):
        """Send a control command via REST API (for non-WS clients)."""
        valid_cmds = {"pause", "resume", "abort", "retry", "redirect", "inject"}
        if command not in valid_cmds:
            raise HTTPException(status_code=400,
                               detail=f"Invalid command: {command}. Valid: {', '.join(valid_cmds)}")

        session_mgr = getattr(app.state, 'session_manager', None)
        if not session_mgr and not getattr(app.state, "interactive_mode", "off") != "off":
            raise HTTPException(status_code=503, detail="Interactive mode not enabled")

        from ..agentic.interaction import UserCommand, CommandType
        cmd_type = CommandType(command)
        data = {}
        if command == "redirect":
            data["focus"] = focus
        elif command == "inject":
            data["context"] = context
        elif command == "retry":
            data["hint"] = hint

        cmd = UserCommand(type=cmd_type, data=data)
        # Non-blocking: fire and forget
        ok = asyncio.get_event_loop().run_until_complete(
            session_mgr.send_command(task_id, "rest_api", cmd)
        ) if session_mgr else False

        return {"status": "ok" if ok else "queued", "command": command}

    # ==================================================================
    # V2 REST: Task interaction status
    # ==================================================================

    # ==================================================================
    # V2 Review: Phase review endpoints
    # ==================================================================

    @app.get("/api/v2/tasks/{task_id}/review/pending")
    def get_pending_review(task_id: str):
        """Get the current pending phase review, if any."""
        # Check ThreadChannel first (used by background agent threads)
        tc = getattr(app.state, '_thread_channels', {}).get(task_id)
        if tc:
            pending = tc.get_pending()
            if pending:
                p = pending[0]
                return {
                    "has_pending": True,
                    "review_id": p["id"],
                    "phase": p.get("type", "review"),
                    "title": p.get("description", "")[:200],
                    "summary": p.get("description", "")[:200],
                    "quality_score": "unknown",
                    "status": "pending",
                    "markdown": "",
                    "created_at": p.get("created_at", ""),
                    "timeout_seconds": 600,
                }

        # Fallback: PhaseReviewGate (foreground/interactive mode)
        session_mgr = getattr(app.state, 'session_manager', None)
        if session_mgr:
            controller = session_mgr.get_controller(task_id)
            if controller and controller.review_gate:
                active = controller.review_gate.get_active_session()
                if active:
                    formatted = active.get_formatted()
                    return {
                        "has_pending": True,
                        "review_id": active.request.id,
                        "phase": active.request.phase,
                        "title": active.request.title,
                        "summary": active.request.summary,
                        "quality_score": active.request.quality_score.value
                                       if active.request.quality_score else "unknown",
                        "status": active.request.status.value,
                        "markdown": formatted.markdown if formatted else "",
                        "created_at": active.request.created_at,
                        "timeout_seconds": active.timeout_seconds,
                    }
        return {"has_pending": False}

    @app.get("/api/v2/tasks/{task_id}/review/history")
    def get_review_history(task_id: str):
        """Get review history for the task."""
        session_mgr = getattr(app.state, 'session_manager', None)
        if session_mgr:
            controller = session_mgr.get_controller(task_id)
            if controller and controller.review_gate:
                return {
                    "task_id": task_id,
                    "reviews": controller.review_gate.get_review_history(),
                }
        return {"task_id": task_id, "reviews": []}

    @app.post("/api/v2/tasks/{task_id}/review/respond")
    def respond_to_review(task_id: str,
                          review_id: str = Query(""),
                          decision: str = Query("revise",
                              description="approve / revise / reject"),
                          feedback: str = Query("",
                              description="Detailed feedback for the agent"),
                          suggestions: str = Query("",
                              description="Comma-separated list of specific suggestions")):
        """Respond to a pending phase review via REST."""
        if decision not in ("approve", "revise", "reject"):
            raise HTTPException(status_code=400,
                               detail="Decision must be: approve, revise, reject")

        suggestion_list = [s.strip() for s in suggestions.split(",") if s.strip()] if suggestions else []

        # PRIMARY: ThreadChannel.resolve — unblocks agent background thread
        tc = getattr(app.state, '_thread_channels', {}).get(task_id)
        if tc and tc.resolve(review_id, decision, feedback, suggestion_list):
            return {"status": "ok", "task_id": task_id, "review_id": review_id, "decision": decision}

        # FALLBACK: PhaseReviewGate (for foreground/interactive mode)
        session_mgr = getattr(app.state, 'session_manager', None)
        if session_mgr:
            controller = session_mgr.get_controller(task_id)
            if controller and controller.review_gate:
                ok = controller.review_gate.resolve_review(review_id, decision, feedback, suggestion_list)
                if ok:
                    return {"status": "ok", "task_id": task_id, "review_id": review_id, "decision": decision}

        raise HTTPException(status_code=404, detail="Review not found or already resolved")

        return {
            "status": "ok",
            "task_id": task_id,
            "review_id": review_id,
            "decision": decision,
        }

    # ==================================================================
    # V2: Task interaction status
    # ==================================================================

    @app.get("/api/v2/tasks/{task_id}/status")
    def get_interaction_status(task_id: str):
        """Get detailed interaction status including controller state."""
        imode = getattr(app.state, 'interactive_mode', 'off')
        session_mgr = getattr(app.state, 'session_manager', None)
        if not session_mgr:
            return {"interactive_mode": imode != "off", "task_id": task_id, "server_mode": imode}

        controller = session_mgr.get_controller(task_id)
        clients = session_mgr.get_connected_clients(task_id) if session_mgr else []

        return {
            "task_id": task_id,
            "interactive_mode": True,
            "controller_state": {
                "is_paused": controller.is_paused if controller else False,
                "is_aborted": controller.is_aborted if controller else False,
                "step_counter": controller._step_counter if controller else 0,
                "has_pending_approval": controller.has_pending_approval if controller else False,
                "pending_approval": controller.get_pending_approval() if controller else None,
            } if controller else None,
            "connected_clients": [
                {"client_id": c.client_id, "role": c.role.value if hasattr(c.role, 'value') else str(c.role),
                 "transport": c.transport, "is_connected": c.is_connected}
                for c in clients
            ],
            "active_tasks": session_mgr.get_active_tasks() if session_mgr else [],
        }

    # ==================================================================
    # V2: Agentic endpoint with interactive mode support
    # ==================================================================

    @app.post("/api/v2/tasks/agentic")
    async def create_agentic_task(request: TaskRequest):
        """Start an agentic task with real-time interaction support.

        Returns immediately with task_id. Agent runs in background thread.
        Connect WebSocket for interaction:
          ws://127.0.0.1:8911/api/v2/tasks/{task_id}/interactive?mode=controller

        Poll GET /api/v1/tasks/{task_id} for status.
        """
        output_root = os.path.abspath(request.output or "./outputs")
        os.makedirs(output_root, exist_ok=True)
        task_desc = request.input or ""
        workspace = request.code or "."
        task_id = f"task_{uuid.uuid4().hex[:8]}"

        if not hasattr(app.state, '_bg_tasks'):
            app.state._bg_tasks = {}
        if not hasattr(app.state, '_thread_channels'):
            app.state._thread_channels = {}
        # Ensure a shared SessionManager exists for WebSocket ↔ Agent bridge
        if not hasattr(app.state, 'session_manager') or app.state.session_manager is None:
            from ..agentic.session import SessionManager
            app.state.session_manager = SessionManager()

        app.state._bg_tasks[task_id] = {
            "status": "RUNNING", "test_results": None,
            "modified_files": [], "errors": [], "iterations": 0,
            "task_type": "agentic", "output_dir": output_root,
        }

        # === ADD TO TASK_HISTORY (fix: V2 tasks were never tracked) ===
        now = datetime.datetime.now().isoformat()
        per_task_dir = output_root  # For V2, this is the shared root initially
        TASK_HISTORY.insert(0, {
            "task_id": task_id, "status": "RUNNING", "task_type": "agentic",
            "output_dir": per_task_dir, "report_path": "",
            "errors": [], "warnings": [], "metrics": {},
            "iterations": 0, "duration_sec": 0.0, "created_at": now,
        })
        if len(TASK_HISTORY) > 100:
            TASK_HISTORY.pop()

        # === PersistenceStore ===
        if not hasattr(app.state, '_persist'):
            try:
                from ..agentic.persistence import PersistentStore
                app.state._persist = PersistentStore("./outputs/devagent.db")
            except ImportError:
                app.state._persist = None
        persist = getattr(app.state, '_persist', None)
        if persist:
            try:
                from ..agentic.persistence import TaskRecord as PTRecord
                persist.save_task(PTRecord(
                    id=task_id, title=task_desc[:200], mode="agentic",
                    status="RUNNING", workspace=workspace, output_dir=output_root))
            except Exception:
                pass

        # Shared SessionManager
        sm = app.state.session_manager

        # ThreadChannel
        from ..agentic.thread_channel import ThreadChannel

        # ThreadChannel with the shared session manager
        from ..agentic.thread_channel import ThreadChannel
        channel = ThreadChannel(session_manager=sm)
        app.state._thread_channels[task_id] = channel

        def _sync_history(task_id, status, errors, files, iterations):
            """Sync a task's completion status to TASK_HISTORY."""
            for i, t in enumerate(TASK_HISTORY):
                if t.get("task_id") == task_id:
                    TASK_HISTORY[i]["status"] = status
                    TASK_HISTORY[i]["errors"] = errors
                    TASK_HISTORY[i]["files_modified"] = files
                    TASK_HISTORY[i]["iterations"] = iterations
                    return

        def _run_agent():
            try:
                from ..agentic.core import DevAgentCore
                from ..agentic.interaction import InteractionController, _set_active_controller
                from ..agentic.review_gate import PhaseReviewGate
                from ..agentic.tools import RequestReviewTool
                RequestReviewTool._global_review_count = 0
                RequestReviewTool._approved_phases = set()
                RequestReviewTool._revise_counts = {}
                max_iter = 60
                core = DevAgentCore()

                ic = InteractionController(
                    session_manager=sm,
                    enable_approval=False, enable_dialogue=False,
                    enable_streaming=False, enable_review_gate=False,
                )
                ic._thread_channel = channel
                ic._task_id = task_id  # for ThreadChannel client detection
                core.interaction = ic

                rg = PhaseReviewGate(llm_client=core.llm, session_manager=sm)
                rg._thread_channel = channel
                ic.review_gate = rg

                _set_active_controller(ic)
                sm.bind_controller(task_id, ic)

                # Detect mode: use PipelineRunner for full/design/implement/repair flows
                use_pipeline = _is_pipeline_task(task_desc)
                if use_pipeline:
                    out_dir = os.path.join(output_root, f"run_{task_id}")
                    pstate = core.run_pipeline(task_desc, workspace, output_root=out_dir)
                    final_status = pstate.status
                    final_errors = _collect_pipeline_errors(pstate)
                    final_files = _collect_pipeline_files(pstate)
                    final_iterations = len(pstate.results)
                    app.state._bg_tasks[task_id] = {
                        "status": final_status, "test_results": {},
                        "modified_files": final_files,
                        "errors": final_errors,
                        "iterations": final_iterations, "phase": pstate.current_phase,
                        "task_type": "full", "output_dir": out_dir,
                    }
                    app.state._task_outputs[task_id] = out_dir
                else:
                    state = core.execute(task_desc, workspace, "python", max_iter)
                    final_status = state.status
                    final_errors = getattr(state, 'errors', [])
                    final_files = state.modified_files
                    final_iterations = state.current_iteration
                    app.state._bg_tasks[task_id] = {
                        "status": final_status, "test_results": state.test_results,
                        "modified_files": final_files,
                        "errors": final_errors,
                        "iterations": final_iterations,
                        "task_type": "agentic", "output_dir": output_root,
                    }
                sm.unbind_controller(task_id)

                # === SYNC TASK_HISTORY ===
                _sync_history(task_id, final_status, final_errors, final_files, final_iterations)
                if persist:
                    try: persist.update_task_status(task_id, final_status)
                    except Exception: pass

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                app.state._bg_tasks[task_id] = {
                    "status": "FAILED",
                    "errors": [f"{e}\n{tb}"],
                    "test_results": None, "modified_files": [], "iterations": 0,
                    "task_type": "agentic", "output_dir": output_root,
                }
                _sync_history(task_id, "FAILED", [f"{e}\n{tb}"], [], 0)
                if persist:
                    try: persist.update_task_status(task_id, "FAILED")
                    except Exception: pass

        import threading
        t = threading.Thread(target=_run_agent, daemon=True)
        t.start()

        return TaskResponse(
            task_id=task_id, status="RUNNING", output_dir=output_root,
            report_path="", errors=[], warnings=[], metrics={},
        )

    # ==================================================================
    # Server startup
    # ==================================================================

    def run_server(host: str = "127.0.0.1", port: int = 8911, interactive: str = None):
        """Run the FastAPI server."""
        import uvicorn
        import sys as _sys

        # Set env var for worker processes
        if interactive:
            _os.environ["DEVAGENT_INTERACTIVE"] = interactive

        # Save and filter argv for uvicorn
        saved = _sys.argv[:]
        _sys.argv = [a for a in _sys.argv if a not in ("--interactive", "-I")
                     and not a.startswith("--interactive=")
                     and a not in ("full","approval","observe","off")]

        print(f"[DevAgent] API server: http://{host}:{port}", flush=True)
        uvicorn.run(app, host=host, port=port, log_level="info")
        _sys.argv = saved
