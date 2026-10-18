//! Correct functions: Phantom must report nothing here (false-positive check).
use std::collections::HashMap;

pub fn safe_average(values: &[i32]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().map(|&v| v as f64).sum::<f64>() / values.len() as f64
}

pub fn first_word(text: &str) -> &str {
    text.split_whitespace().next().unwrap_or("")
}

pub fn clamp(x: i32, low: i32, high: i32) -> i32 {
    x.max(low).min(high)
}

pub fn stock_of(stock: &HashMap<String, u32>, item: &str) -> u32 {
    stock.get(item).copied().unwrap_or(0)
}
