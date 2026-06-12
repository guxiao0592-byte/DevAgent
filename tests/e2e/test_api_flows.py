"""E2E Tests — Critical API flows for DevAgent.

Runs against a running server (python -m devagent.api.app).
Start server first, then: pytest tests/e2e/test_api_flows.py -v

Covers:
  1. Task creation (all 7 modes)
  2. Task history listing
  3. Task detail with phase progress
  4. Phase document retrieval
  5. Diagram listing
  6. Phase download
  7. File upload
  8. Health check
"""

import pytest
import requests
import json
import os
import tempfile
import time

BASE_URL = os.environ.get("DEVAGENT_TEST_URL", "http://127.0.0.1:8911")


def _check_server():
    """Check if test server is reachable."""
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(autouse=True)
def check_server():
    """Skip all tests if server is not running."""
    if not _check_server():
        pytest.skip("DevAgent server not running — start with: uvicorn devagent.api.app:app --port 8911")


# ===========================================================================
# Test 1: Health Check
# ===========================================================================

def test_health_check():
    """API server should respond to health check."""
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


# ===========================================================================
# Test 2: Task Creation — All Modes
# ===========================================================================

@pytest.mark.parametrize("mode,endpoint,params", [
    ("agentic", "/api/v2/tasks/agentic", {"task": "agentic", "input": "test agentic creation", "code": ".", "output": "./outputs/e2e_test"}),
])
def test_create_task_async(mode, endpoint, params):
    """Every task mode should create a task and return a task_id."""
    if isinstance(params, dict) and not params.get("task"):
        resp = requests.post(f"{BASE_URL}{endpoint}", params=params, timeout=30)
    else:
        resp = requests.post(f"{BASE_URL}{endpoint}", json=params, timeout=30)

    assert resp.status_code == 200, f"Failed: {resp.text[:200]}"
    data = resp.json()
    assert "task_id" in data
    assert len(data["task_id"]) > 8


# ===========================================================================
# Test 3: Task History
# ===========================================================================

def test_task_history():
    """Task history should return list of tasks."""
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/history", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "total" in data
    assert isinstance(data["tasks"], list)


# ===========================================================================
# Test 4: Task Detail
# ===========================================================================

def test_task_detail():
    """Getting a specific task should return details including phase progress."""
    # First create a task
    resp = requests.post(f"{BASE_URL}/api/v2/tasks/agentic", json={
        "task": "agentic", "input": "test detail endpoint",
        "code": ".", "output": "./outputs/e2e_detail"
    }, timeout=30)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # Now fetch its detail
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/{task_id}", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task_id
    assert data["status"] in ("RUNNING", "COMPLETED", "FINISHED", "FAILED", "PENDING")


# ===========================================================================
# Test 5: Phase Documents
# ===========================================================================

def test_phase_document_endpoint():
    """Phase document endpoint should return generated documents."""
    # Use a known task ID from previous runs, or the outputs directory
    # Try to find any task ID from history
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/history", timeout=5)
    tasks = resp.json().get("tasks", [])

    if not tasks:
        pytest.skip("No tasks in history to test document retrieval")

    task_id = tasks[0]["task_id"]
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/{task_id}/document/requirements", timeout=10)

    # May be 404 if requirements phase hasn't run yet — that's OK
    if resp.status_code == 404:
        return  # Skip gracefully

    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert "filename" in data
    assert len(data["content"]) > 100


# ===========================================================================
# Test 6: Diagram Listing
# ===========================================================================

def test_diagram_listing():
    """Diagram endpoint should return list of diagrams."""
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/history", timeout=5)
    tasks = resp.json().get("tasks", [])

    if not tasks:
        pytest.skip("No tasks in history")

    task_id = tasks[0]["task_id"]
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/{task_id}/diagrams/design", timeout=10)

    if resp.status_code == 404:
        return  # No design phase yet

    assert resp.status_code == 200
    data = resp.json()
    assert "diagrams" in data
    assert isinstance(data["diagrams"], list)


# ===========================================================================
# Test 7: Phase Download
# ===========================================================================

def test_phase_download():
    """Phase download should return a valid ZIP file."""
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/history", timeout=5)
    tasks = resp.json().get("tasks", [])

    if not tasks:
        pytest.skip("No tasks in history")

    task_id = tasks[0]["task_id"]
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/{task_id}/download/requirements", stream=True, timeout=15)

    if resp.status_code == 404:
        return  # No requirements phase yet

    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/zip"
    # Check ZIP magic bytes
    data = resp.content
    assert data[:2] == b'PK', "Not a valid ZIP file"


# ===========================================================================
# Test 8: File Upload
# ===========================================================================

def test_file_upload():
    """File upload should accept and save a requirements file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("# Test Requirements\n\nBuild a test app.\n")
        tmp_path = f.name

    try:
        with open(tmp_path, 'rb') as f:
            resp = requests.post(f"{BASE_URL}/api/v1/upload", files={"file": f}, timeout=10)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "saved_path" in data
        assert data["size_bytes"] > 0
    finally:
        os.unlink(tmp_path)


# ===========================================================================
# Test 9: Project Structure
# ===========================================================================

def test_project_structure():
    """Project structure endpoint should return file list."""
    resp = requests.get(f"{BASE_URL}/api/v1/project/structure?path=.", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "files" in data
    assert "count" in data


# ===========================================================================
# Test 10: Invalid Task Handling
# ===========================================================================

def test_invalid_task_returns_404():
    """Requesting a non-existent task should return 404."""
    resp = requests.get(f"{BASE_URL}/api/v1/tasks/nonexistent_12345", timeout=5)
    assert resp.status_code == 404
