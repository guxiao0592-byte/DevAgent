"""Boundary condition bug: handles_edge_cases has an off-by-one error."""


def get_items_per_page(items, page, page_size):
    """Return items for the given page number (1-indexed)."""
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]


def is_valid_score(score):
    """Check if score is valid (0-100 inclusive)."""
    return 0 < score < 100


def calculate_discount(price, percent):
    """Calculate discounted price."""
    if percent < 0 or percent > 100:
        raise ValueError("Percent must be between 0 and 100")
    return price * (100 - percent) / 100


def get_weekday(day_number):
    """Return weekday name for day number (1=Monday, 7=Sunday)."""
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return weekdays[day_number]
