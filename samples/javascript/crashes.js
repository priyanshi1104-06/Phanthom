// Each function has one bug that a boundary input finds, except isLeapYear: its bug is a wrong
// answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.
// The JSDoc types tell Specter which values each parameter accepts; isLeapYear has none, so
// Specter guesses from the name and anything it found there would be a warning, not an error.

/** @param {number[]} values */
function average(values) {
  const total = values.reduce((sum, value) => sum + value);
  return total / values.length;
}

/** @param {string} text */
function firstWord(text) {
  const word = text.trim().split(" ")[0];
  return word[0].toUpperCase() + word.slice(1);
}

/** @param {number[]} prices */
function lastPrice(prices) {
  const last = prices[prices.length - 1];
  return last.toFixed(2);
}

/** @param {string} label @param {number} width */
function padLabel(label, width) {
  return " ".repeat(width - label.length) + label;
}

/** @param {string} address */
function parsePort(address) {
  const port = address.split(":")[1];
  return Number(port.trim());
}

/** @param {number} n */
function collatzSteps(n) {
  let steps = 0;
  while (n !== 1) {
    n = n % 2 === 0 ? n / 2 : 3 * n + 1;
    steps += 1;
  }
  return steps;
}

/** @param {number} n */
function factorial(n) {
  if (n === 0) {
    return 1;
  }
  return n * factorial(n - 1);
}

function isLeapYear(year) {
  return year % 4 === 0;
}

module.exports = { average, firstWord, lastPrice, padLabel, parsePort, collatzSteps, factorial, isLeapYear };
console.log(average([1, 2, 3]));
