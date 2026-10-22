//! Functions that run fine and answer wrong, with examples in doc comments.

/// True for leap years in the Gregorian calendar.
/// assert_eq!(is_leap_year(1900), false);
pub fn is_leap_year(year: i32) -> bool {
    year % 4 == 0
}

/// Limit value to the range low..high.
/// assert_eq!(clamp(15, 0, 10), 10);
pub fn clamp(value: i32, low: i32, high: i32) -> i32 {
    value.min(low).max(low)
}

/// Celsius to Fahrenheit.
/// assert_eq!(to_fahrenheit(100.0), 212.0);
pub fn to_fahrenheit(celsius: f64) -> f64 {
    celsius * 9.0 / 5.0 + 23.0
}

/// The larger of two numbers.
/// assert_eq!(max_of(3, 7), 7);
pub fn max_of(a: i32, b: i32) -> i32 {
    if a > b { a } else { b }
}
