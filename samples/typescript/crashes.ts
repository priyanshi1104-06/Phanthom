// With types, Specter knows exactly which values are allowed: a finding here needs no assumption.
// isLeapYear's bug is a wrong answer, not a crash: that needs an intent check (Analyst).

export function average(values: number[]): number {
  return values.reduce((sum, value) => sum + value) / values.length;
}

export function findIndex(items: string[], target: string): number {
  for (let i = 0; i < items.length; i++) {
    if (items[i] === target) {
      return i;
    }
  }
}

export function initials(name: string): string {
  return name.split(" ").map((part) => part[0].toUpperCase()).join("");
}

export function chunk(items: number[], size: number): number[][] {
  const chunks: number[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

export function topScore(scores: number[]): string {
  const sorted = [...scores].sort((a, b) => b - a);
  return sorted[0].toFixed(1);
}

export function formatPrice(cents: number, decimals: number): string {
  return (cents / 100).toFixed(decimals);
}

export function shout(message: string | null): string {
  return message.toUpperCase() + "!";
}

export function isLeapYear(year: number): boolean {
  return year % 4 === 0;
}
