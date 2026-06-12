"""Tests for math_ops — designed to expose the four embedded bugs."""

import pytest
from src.math_ops import add, subtract, multiply, divide
from src.math_ops import is_even, find_user, get_user_display, file_read


class TestAddSubtractMultiply:
    """These tests should all PASS — no bugs in these functions."""

    def test_add_positive(self):
        assert add(3, 4) == 7, "add(3, 4) should be 7"

    def test_add_negative(self):
        assert add(-1, -1) == -2, "add(-1, -1) should be -2"

    def test_subtract(self):
        assert subtract(10, 5) == 5, "subtract(10, 5) should be 5"

    def test_multiply(self):
        assert multiply(6, 7) == 42, "multiply(6, 7) should be 42"

    def test_multiply_zero(self):
        assert multiply(100, 0) == 0, "multiply(100, 0) should be 0"


class TestDivide:
    """Tests for divide() — should expose the boundary bug."""

    def test_divide_normal(self):
        assert divide(15, 3) == 5, "divide(15, 3) should be 5"

    def test_divide_negative(self):
        assert divide(-10, 2) == -5, "divide(-10, 2) should be -5"

    def test_divide_by_zero_should_raise(self):
        """This test EXPECTS a ValueError for division by zero.
        But the buggy code throws ZeroDivisionError, so this WILL FAIL."""
        with pytest.raises(ValueError, match="Division by zero"):
            divide(10, 0)


class TestIsEven:
    """Tests for is_even() — should expose the logic bug."""

    def test_is_even_true(self):
        """4 is even → should return True. BUG returns False."""
        assert is_even(4) is True, "4 is even, is_even(4) should return True"

    def test_is_even_false(self):
        """5 is odd → should return False. BUG returns True."""
        assert is_even(5) is False, "5 is odd, is_even(5) should return False"

    def test_zero_is_even(self):
        """0 is even → should return True. BUG returns False."""
        assert is_even(0) is True, "0 is even, is_even(0) should return True"


class TestFindUser:
    """Tests for find_user() — this function works fine."""

    def test_find_existing_user(self):
        user = find_user(1)
        assert user is not None
        assert user["name"] == "Alice"

    def test_find_nonexistent_user(self):
        user = find_user(999)
        assert user is None


class TestGetUserDisplay:
    """Tests for get_user_display() — should expose the null bug."""

    def test_display_existing_user(self):
        assert get_user_display(1) == "User: Alice"

    def test_display_nonexistent_user(self):
        """get_user_display(999) calls find_user(999) → None,
        then tries None['name'] which raises TypeError. This WILL FAIL."""
        with pytest.raises(ValueError, match="User not found"):
            get_user_display(999)


class TestFileRead:
    """Tests for file_read() — should expose the path bug."""

    def test_file_read_existing(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert file_read(str(f)) == "hello"

    def test_file_read_missing(self):
        """Reading a nonexistent file should raise a wrapped FileOpenError.
        The buggy code throws a raw FileNotFoundError without wrapping it,
        so this test WILL FAIL — wrong exception type."""
        success = False
        try:
            file_read("/nonexistent/path/xyz.txt")
        except Exception as e:
            # Buggy code lets raw FileNotFoundError through.
            # Proper code should wrap it in a custom or helpful error.
            if "does not exist" in str(e) or "not found" in str(e).lower():
                success = True
        assert success, (
            "file_read() should raise a wrapped error with a helpful message, "
            "but got raw FileNotFoundError without context"
        )
