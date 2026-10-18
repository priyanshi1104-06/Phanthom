<?php
declare(strict_types=1);

// Each function has one bug that a boundary input finds, except is_leap_year: its bug is a wrong
// answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.

function average(array $values): float
{
    return array_sum($values) / count($values);
}

function split_bill(int $totalCents, int $people): int
{
    return intdiv($totalCents, $people);
}

/** @param array<string, string> $user */
function display_name(array $user): string
{
    return ucfirst($user['name']);
}

function parse_port(string $address): int
{
    [, $port] = explode(':', $address);
    return (int) $port;
}

function shout(?string $message): string
{
    return strtoupper($message) . '!';
}

function indent(string $text, int $level): string
{
    return str_repeat('  ', $level) . $text;
}

function find_index(array $items, int $target): int
{
    foreach ($items as $i => $item) {
        if ($item === $target) {
            return $i;
        }
    }
}

function collatz_steps(int $n): int
{
    $steps = 0;
    while ($n !== 1) {
        $n = $n % 2 === 0 ? intdiv($n, 2) : 3 * $n + 1;
        $steps++;
    }
    return $steps;
}

function factorial(int $n): int
{
    if ($n === 0) {
        return 1;
    }
    return $n * factorial($n - 1);
}

function is_leap_year(int $year): bool
{
    return $year % 4 === 0;
}

echo average([1, 2, 3]), PHP_EOL;
