"""Functions that run fine and answer wrong: Analyst's job (Specter finds no crash in them).

Each docstring says what the function should do; the code does something slightly different.
The ones with examples are caught without any AI; the others need Analyst's AI to read the intent.
"""


def is_leap_year(year: int) -> bool:
    """True for leap years in the Gregorian calendar.

    >>> is_leap_year(2024)
    True
    >>> is_leap_year(1900)
    False
    """
    return year % 4 == 0


def clamp(value: int, low: int, high: int) -> int:
    """Limit value to the range low..high, e.g. clamp(15, 0, 10) == 10."""
    return max(low, min(value, low))


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit: celsius_to_fahrenheit(100) -> 212.0"""
    return celsius * 9 / 5 + 23


def count_vowels(text: str) -> int:
    """Number of vowels (a, e, i, o, u, upper or lower case) in text."""
    return sum(1 for ch in text if ch in "aeiou")


def median(values: list[int]) -> float:
    """The middle value of a non-empty list; for an even count, the mean of the two middle values."""
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])
