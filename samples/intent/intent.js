// Functions that run fine and answer wrong, with examples in JSDoc. Analyst catches the wrong ones.

/**
 * True for leap years in the Gregorian calendar.
 * @param {number} year
 * @returns {boolean}
 * @example isLeapYear(2024) // => true
 * @example isLeapYear(1900) // => false
 */
function isLeapYear(year) {
  return year % 4 === 0;
}

/**
 * Limit value to the range low..high: clamp(15, 0, 10) => 10
 * @param {number} value
 * @param {number} low
 * @param {number} high
 * @returns {number}
 */
function clamp(value, low, high) {
  return Math.max(low, Math.min(value, low));
}

/**
 * Celsius to Fahrenheit: toFahrenheit(100) => 212
 * @param {number} celsius
 * @returns {number}
 */
function toFahrenheit(celsius) {
  return celsius * 9 / 5 + 23;
}

/**
 * The larger of two numbers: maxOf(3, 7) => 7
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
function maxOf(a, b) {
  return a > b ? a : b;
}

module.exports = { isLeapYear, clamp, toFahrenheit, maxOf };
