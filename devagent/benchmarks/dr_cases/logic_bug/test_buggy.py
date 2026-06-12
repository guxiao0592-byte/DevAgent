"""Tests for logic bugs - these should FAIL with the original code."""

from buggy import is_leap_year, fizzbuzz, triangle_type


def test_is_leap_year():
    assert is_leap_year(2000) == True   # divisible by 400
    assert is_leap_year(2024) == True   # divisible by 4
    assert is_leap_year(1900) == False  # divisible by 100 but not 400
    assert is_leap_year(2023) == False  # not divisible by 4


def test_fizzbuzz():
    result = fizzbuzz(15)
    assert result[2] == "Fizz"       # 3
    assert result[4] == "Buzz"       # 5
    assert result[14] == "FizzBuzz"  # 15
    assert result[0] == "1"          # 1
    assert result[6] == "7"          # 7


def test_fizzbuzz_count():
    result = fizzbuzz(15)
    assert result.count("Fizz") == 4   # 3, 6, 9, 12
    assert result.count("Buzz") == 2   # 5, 10 (but not 15)
    assert result.count("FizzBuzz") == 1  # 15


def test_triangle_type():
    assert triangle_type(3, 3, 3) == "equilateral"
    assert triangle_type(3, 3, 4) == "isosceles"
    assert triangle_type(3, 4, 5) == "scalene"
    assert triangle_type(1, 1, 3) == "invalid"
