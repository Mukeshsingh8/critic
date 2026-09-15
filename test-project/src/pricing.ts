// PLANTED DEFECT: reuse. Reinvents formatCurrency from src/format.ts:1
export function renderPrice(amount: number, currency: string): string {
  const symbol = currency === "GBP" ? "£" : currency === "EUR" ? "€" : "$";
  return symbol + amount.toFixed(2);
}
