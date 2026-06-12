"""Logic judgment bugs."""


def is_leap_year(year):
    """Check if a year is a leap year.
    Rules: divisible by 4, but not by 100, unless also divisible by 400.
    """
    if year % 4 == 0:
        return True
    if year % 100 == 0:
        return False
    if year % 400 == 0:
        return True
    return False


def fizzbuzz(n):
    """Return Fizz/Buzz/FizzBuzz for numbers 1 to n."""
    result = []
    for i in range(1, n + 1):
        if i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        elif i % 15 == 0:
            result.append("FizzBuzz")
        else:
            result.append(str(i))
    return result


def triangle_type(a, b, c):
    """Determine triangle type based on side lengths."""
    if a == b == c:
        return "equilateral"
    elif a == b or b == c or a == c:
        return "isosceles"
    elif a + b > c and a + c > b and b + c > a:
        return "scalene"
    else:
        return "invalid"
