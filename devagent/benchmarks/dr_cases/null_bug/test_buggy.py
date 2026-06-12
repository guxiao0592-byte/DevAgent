"""Tests for null value handling bugs - these should FAIL with the original code."""

from buggy import get_user_name, safe_divide, get_first_item
import pytest


def test_get_user_name_found():
    users = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    assert get_user_name(users, 1) == "Alice"


def test_get_user_name_not_found():
    users = [{"id": 1, "name": "Alice"}]
    with pytest.raises(ValueError, match="User not found"):
        get_user_name(users, 999)


def test_safe_divide_normal():
    assert safe_divide(10, 2) == 5.0


def test_safe_divide_by_zero():
    result = safe_divide(10, 0)
    assert result is None


def test_get_first_item_non_empty():
    assert get_first_item([1, 2, 3]) == 1


def test_get_first_item_empty():
    # Should handle empty list gracefully
    with pytest.raises(IndexError):
        get_first_item([])
