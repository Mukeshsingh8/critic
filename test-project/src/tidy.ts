import { formatCurrency } from "./format";

export interface QuoteLine {
  name: string;
  total: number;
  currency: string;
}

// NEGATIVE CONTROL: correct, reuses the shared helper, linear over a small array.
// Slightly verbose, but nothing here is a defect. Nothing should flag this file.
export function summarise(lines: QuoteLine[]): string {
  const parts: string[] = [];
  for (const line of lines) {
    const price = formatCurrency(line.total, line.currency);
    parts.push(line.name + ": " + price);
  }
  return parts.join(", ");
}
