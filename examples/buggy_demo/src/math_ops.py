"""A deliberately buggy math operations module for DevAgent repair demo.

This module contains four types of bugs that DevAgent's three-layer
fault localization can detect:

- boundary_bug: divide() — missing zero-division guard
- logic_bug:   is_even() — inverted return value
- null_bug:    get_user_display() — missing None-check
- path_bug:    file_read() — no try/except around open()
"""


def add(a: float, b: float) -> float:
    """Add two numbers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        The sum of a and b.
    """
    return a + b


def subtract(a: float, b: float) -> float:
    """Subtract b from a.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        a minus b.
    """
    return a - b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        The product of a and b.
    """
    return a * b


def divide(a: float, b: float) -> float:
    """BUG (boundary): divide a by b without checking for division by zero.

    Args:
        a: Numerator.
        b: Denominator — ZERO will cause ZeroDivisionError.

    Returns:
        a divided by b.
    """
    return a / b


def is_even(n: int) -> bool:
    """BUG (logic): return value is inverted.

    Args:
        n: An integer.

    Returns:
        True if n is even, False if n is odd — BUT THE LOGIC IS REVERSED.
    """
    return n % 2 != 0


def find_user(user_id: int) -> dict | None:
    """Simulate looking up a user. Returns None when the user is not found.

    Args:
        user_id: The user identifier.

    Returns:
        A user dict if found, else None.
    """
    _users = {1: {"id": 1, "name": "Alice"}, 2: {"id": 2, "name": "Bob"}}
    return _users.get(user_id)


def get_user_display(user_id: int) -> str:
    """BUG (null): calls find_user but does not guard against None.

    Args:
        user_id: The user identifier.

    Returns:
        A display string like "User: Alice".
    """
    user = find_user(user_id)
    return f"User: {user['name']}"


def file_read(path: str) -> str:
    """BUG (path): opens a file without try/except for FileNotFoundError.

    Args:
        path: Filesystem path to read.

    Returns:
        The file contents as a string.
    """
    return open(path).read()
