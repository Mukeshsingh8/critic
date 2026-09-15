import { db } from "./db";

// PLANTED DEFECT: complexity. N+1 -- one query per supplier.
export async function loadQuotes(supplierIds: string[]) {
  const quotes = [];
  for (const id of supplierIds) {
    const rows = await db.query("SELECT * FROM quotes WHERE supplier_id = $1", [id]);
    quotes.push(...rows);
  }
  return quotes;
}
