// Correct functions: Specter must report nothing here.

function safeAverage(values) {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

const wordCount = (text) => text.split(/\s+/).filter(Boolean).length;

function clamp(x, lo = 0, hi = 10) {
  return Math.min(Math.max(x, lo), hi);
}

module.exports = { safeAverage, wordCount, clamp };
