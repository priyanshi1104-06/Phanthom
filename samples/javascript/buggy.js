// Each function has one bug that a boundary input finds, except isLeapYear: its bug is a wrong
// answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.

function average(values) {
  const total = values.reduce((sum, value) => sum + value);
  return total / values.length;
}

function firstWord(text) {
  const word = text.trim().split(" ")[0];
  return word[0].toUpperCase() + word.slice(1);
}

function lastPrice(prices) {
  const last = prices[prices.length - 1];
  return last.toFixed(2);
}

function padLabel(label, width) {
  return " ".repeat(width - label.length) + label;
}

function parsePort(address) {
  const port = address.split(":")[1];
  return Number(port.trim());
}

function collatzSteps(n) {
  let steps = 0;
  while (n !== 1) {
    n = n % 2 === 0 ? n / 2 : 3 * n + 1;
    steps += 1;
  }
  return steps;
}

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
