# Buggy Math Operations — DevAgent Repair Demo

## Overview

This is a deliberately crafted **buggy Python project** for demonstrating DevAgent's repair capabilities.

## Project Structure

```
buggy_demo/
├── src/
│   ├── __init__.py
│   └── math_ops.py          ← 8 functions, 4 with bugs
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_math_ops.py      ← 16 test cases exposing the bugs
```

## Bug Inventory

| # | Function | Bug Type | Root Cause | Severity |
|---|----------|----------|------------|----------|
| 1 | `divide()` | **boundary** | No zero-division guard → `ZeroDivisionError` | High |
| 2 | `is_even()` | **logic** | Returns `n % 2 != 0` instead of `n % 2 == 0` | Medium |
| 3 | `get_user_display()` | **null** | Calls `find_user()` which may return `None`, then accesses `user['name']` → `TypeError` | High |
| 4 | `file_read()` | **path** | Raw `open()` without `try/except` → unhandled `FileNotFoundError` | Medium |

## What DevAgent Will Do

When you run `agent --mode repair`, DevAgent's three-layer fault localization engine will:

1. **SBFL (Spectrum-Based Fault Localization)**: Runs the tests, collects pass/fail traces, computes Ochiai suspiciousness scores to rank suspicious lines
2. **Static Analysis (AST)**: Scans the code for boundary/null/exception/logic patterns
3. **LLM Fusion**: Synthesises SBFL scores + static findings into a ranked, reasoned bug report

Then `RepairAgent` generates minimal fix patches and re-runs tests to verify.

## Run the Demo

```bash
# First, run the tests to see the current failures
cd examples/buggy_demo
python -m pytest tests/ -v

# Expected: 4 FAILED (divide_by_zero, is_even, get_user_display, file_read)

# Now let DevAgent fix it
agent --mode repair --workspace examples/buggy_demo --output outputs/repair_demo

# Check the results
cat outputs/repair_demo/05_repair/debug_analysis_report.md
cat outputs/repair_demo/05_repair/patch.diff
```

## Expected Repair Outcomes

After DevAgent fixes:

- `divide()` → adds `if b == 0: raise ValueError(...)` guard
- `is_even()` → corrects to `return n % 2 == 0`
- `get_user_display()` → adds `if user is None: raise ValueError(...)` guard
- `file_read()` → wraps `open()` with `try/except FileNotFoundError`
