"""Tests for path/file handling bugs - these should FAIL with the original code."""

from buggy import read_file_safe, count_lines, write_report, list_files
import pytest
import os
import tempfile


def test_read_file_not_found():
    """Should handle missing file gracefully."""
    result = read_file_safe("/nonexistent/file.txt")
    assert result is None


def test_count_lines_empty_file():
    """Should handle empty file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("")
        tmpname = f.name
    try:
        assert count_lines(tmpname) == 0
    finally:
        os.unlink(tmpname)


def test_count_lines_normal():
    """Should count lines correctly."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("line1\nline2\nline3\n")
        tmpname = f.name
    try:
        assert count_lines(tmpname) == 3
    finally:
        os.unlink(tmpname)


def test_write_report():
    """Should create file and write content."""
    path = "/tmp/test_report.txt"
    write_report(path, "Test content")
    assert os.path.exists(path)
    with open(path, "r") as f:
        assert f.read() == "Test content"
    os.unlink(path)


def test_list_files_nonexistent_dir():
    """Should handle nonexistent directory."""
    result = list_files("/nonexistent_directory_xyz")
    assert result == []
