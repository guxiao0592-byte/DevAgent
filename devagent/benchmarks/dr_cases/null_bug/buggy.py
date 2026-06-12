"""Null value handling bugs."""


def find_user(users, user_id):
    """Find user by ID. users is a list of dicts with 'id' and 'name' keys."""
    for user in users:
        if user["id"] == user_id:
            return user
    return None


def get_user_name(users, user_id):
    """Get user name by ID."""
    user = find_user(users, user_id)
    return user["name"]


def safe_divide(a, b):
    """Divide a by b, return None if division is not possible."""
    return a / b


def get_first_item(items):
    """Return the first item in a list."""
    return items[0]
