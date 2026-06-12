#!/usr/bin/env python3
"""Simple Hello World application."""


def greet(name: str = "World") -> str:
    """Return a greeting message.

    Args:
        name: The name to greet. Defaults to "World".

    Returns:
        A formatted greeting string.

    Examples:
        >>> greet()
        'Hello, World!'
        >>> greet("Alice")
        'Hello, Alice!'
    """
    return f"Hello, {name}!"


def main() -> None:
    """Entry point for the hello world application."""
    print(greet())


if __name__ == "__main__":
    main()
