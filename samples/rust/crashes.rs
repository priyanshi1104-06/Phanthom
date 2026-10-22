//! Ten deliberately buggy functions: Phantom should find a failing input for each.
//!
//! Each comment names the bug. `is_leap_year` is the honest exception: it never panics, it just
//! answers wrong for 1900, so finding it needs an intent check, not a crash search.
use std::collections::HashMap;

/// Empty slice: divides by a length of zero.
pub fn average(values: &[i32]) -> i32 {
    let total: i32 = values.iter().sum();
    total / values.len() as i32
}

/// Off-by-one: the last i + 1 is past the end of the slice.
pub fn pairwise_diffs(values: &[i32]) -> Vec<i32> {
    let mut diffs = Vec::new();
    for i in 0..values.len() {
        diffs.push(values[i + 1] - values[i]);
    }
    diffs
}

/// Empty slice: max() is None.
pub fn largest(values: &[i64]) -> i64 {
    *values.iter().max().unwrap()
}

/// Unvalidated parsing: assumes whatever follows the ':' is a number.
pub fn parse_port(address: &str) -> u16 {
    let port = address.split(':').nth(1).unwrap_or("80");
    port.parse().unwrap()
}

/// Missing key: not every user record has a "name".
pub fn display_name(user: &HashMap<String, String>) -> String {
    user["name"].to_uppercase()
}

/// Empty slice: len() - 1 underflows (overflow checks are on, as in debug builds).
pub fn middle(values: &[i32]) -> i32 {
    values[(values.len() - 1) / 2]
}

/// Byte slicing: "é" takes two bytes, so [..1] cuts it in half.
pub fn initial(name: &str) -> String {
    if name.is_empty() {
        return String::new();
    }
    name[..1].to_uppercase()
}

/// Infinite loop: n == 0 never reaches 1.
pub fn collatz_steps(mut n: u64) -> u32 {
    let mut steps = 0;
    while n != 1 {
        n = if n % 2 == 0 { n / 2 } else { 3 * n + 1 };
        steps += 1;
    }
    steps
}

/// Missing base case: a negative n recurses until the stack overflows.
pub fn factorial(n: i64) -> i64 {
    if n == 0 {
        return 1;
    }
    n * factorial(n - 1)
}

/// Wrong answer, no panic: ignores the century rule (1900 was not a leap year).
pub fn is_leap_year(year: i32) -> bool {
    year % 4 == 0
}
