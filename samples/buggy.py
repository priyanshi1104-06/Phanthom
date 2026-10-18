"""Ten deliberately buggy functions: Phantom should find a failing input for each.

Each docstring names the bug. is_leap_year is the honest exception: it never crashes, it just
answers wrong for 1900, so finding it needs an intent check (Analyst), not a crash search.
"""


def average(values: list[int]) -> float:
    """Empty list: divides by len([]) == 0."""
    return sum(values) / len(values)


def split_bill(total_cents: int, people: int) -> int:
    """Division by zero when nobody is splitting the bill."""
    return total_cents // people


def pairwise_diffs(values: list[int]) -> list[int]:
    """Off-by-one: the last i + 1 is past the end of the list."""
    return [values[i + 1] - values[i] for i in range(len(values))]


def find_index(items: list[int], target: int) -> int:
    """None path: falls off the end and returns None when target is missing."""
    for i, item in enumerate(items):
        if item == target:
            return i


def display_name(user: dict[str, str]) -> str:
    """Missing key: not every user dict has a "name"."""
    return user["name"].title()


def parse_port(address: str) -> int:
    """Unvalidated parsing: assumes "host:port" with a numeric port."""
    return int(address.split(":")[1])


def initials(full_name: str) -> str:
    """String edge case: "" or a double space gives an empty part, and part[0] fails."""
    return "".join(part[0].upper() for part in full_name.split(" "))


def collatz_steps(n: int) -> int:
    """Infinite loop: n <= 0 never reaches 1."""
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


def factorial(n: int) -> int:
    """Missing base case: negative n recurses forever (RecursionError)."""
    if n == 0:
        return 1
    return n * factorial(n - 1)


def is_leap_year(year: int) -> bool:
    """Wrong answer, no crash: ignores the century rule (1900 was not a leap year)."""
    return year % 4 == 0
