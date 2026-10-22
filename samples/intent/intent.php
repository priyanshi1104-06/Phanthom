<?php
// Functions that run fine and answer wrong, with examples in comments. Analyst catches the wrong ones.

/**
 * True for leap years in the Gregorian calendar.
 * is_leap_year(1900) returns false
 */
function is_leap_year(int $year): bool
{
    return $year % 4 === 0;
}

/** Limit value to the range low..high: clamp(15, 0, 10) === 10 */
function clamp(int $value, int $low, int $high): int
{
    return max($low, min($value, $low));
}

/** Celsius to Fahrenheit: to_fahrenheit(100.0) === 212.0 */
function to_fahrenheit(float $celsius): float
{
    return $celsius * 9 / 5 + 23;
}

/** The larger of two numbers: max_of(3, 7) === 7 */
function max_of(int $a, int $b): int
{
    return $a > $b ? $a : $b;
}
