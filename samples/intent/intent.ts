// Functions that run fine and answer wrong, with examples in comments. Analyst catches the wrong ones.

// True for leap years in the Gregorian calendar: isLeapYear(1900) === false
export function isLeapYear(year: number): boolean {
  return year % 4 === 0;
}

// Limit value to the range low..high: clamp(15, 0, 10) === 10
export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(value, low));
}

// Celsius to Fahrenheit: toFahrenheit(100) === 212
export function toFahrenheit(celsius: number): number {
  return celsius * 9 / 5 + 23;
}

// The larger of two numbers: maxOf(3, 7) === 7
export function maxOf(a: number, b: number): number {
  return a > b ? a : b;
}
