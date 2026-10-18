<?php
declare(strict_types=1);

namespace App\Util;

// Correct functions: Specter must report nothing here. percent() refuses a zero on purpose,
// with an exception: that is the function keeping its contract, not a bug.

function clamp(int $value, int $low, int $high): int
{
    if ($low > $high) {
        [$low, $high] = [$high, $low];
    }
    return max($low, min($high, $value));
}

function safe_average(array $values): ?float
{
    if ($values === []) {
        return null;
    }
    return array_sum($values) / count($values);
}

function percent(int $part, int $whole): float
{
    if ($whole === 0) {
        throw new \InvalidArgumentException('whole must not be zero');
    }
    return $part / $whole * 100;
}

final class Money
{
    public static function format(int $cents, string $currency = 'EUR'): string
    {
        return sprintf('%s %.2f', $currency, $cents / 100);
    }
}
