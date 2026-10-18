// Correct functions: Specter must report nothing here.

export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(Math.max(x, lo), hi);
}

export function safeAverage(values: number[]): number {
  return values.length === 0 ? 0 : values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function wordCount(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}

export function greet(name: string | null): string {
  return name === null ? "Hello!" : `Hello, ${name}!`;
}
