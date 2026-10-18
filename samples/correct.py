"""Correct functions: Phantom must report nothing here (false-positive check)."""


def safe_average(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def word_count(text: str) -> int:
    return len(text.split())


def clamp(x: int, low: int, high: int) -> int:
    return max(low, min(x, high))
