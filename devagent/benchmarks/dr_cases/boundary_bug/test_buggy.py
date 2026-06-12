"""Tests for boundary condition bugs - these should FAIL with the original code."""

from buggy import (
    get_items_per_page,
    is_valid_score,
    calculate_discount,
    get_weekday,
)
import pytest


def test_get_items_per_page():
    items = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert get_items_per_page(items, 1, 3) == [1, 2, 3]
    assert get_items_per_page(items, 2, 3) == [4, 5, 6]
    assert get_items_per_page(items, 4, 3) == [10]  # Last page has 1 item


def test_is_valid_score_boundary():
    assert is_valid_score(0) == True   # Should be valid (boundary)
    assert is_valid_score(100) == True  # Should be valid (boundary)
    assert is_valid_score(50) == True   # Mid-range
    assert is_valid_score(-1) == False  # Invalid
    assert is_valid_score(101) == False # Invalid


def test_calculate_discount():
    assert calculate_discount(100, 0) == 100   # 0% off
    assert calculate_discount(100, 50) == 50   # 50% off
    assert calculate_discount(100, 100) == 0   # 100% off
    with pytest.raises(ValueError):
        calculate_discount(100, 101)


def test_get_weekday():
    assert get_weekday(1) == "Monday"
    assert get_weekday(7) == "Sunday"
