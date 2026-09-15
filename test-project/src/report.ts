// PLANTED DEFECTS: convention (`any`) and decomposition (four jobs in one function).
export async function buildReport(input: any): Promise<any> {
  const raw = await fetch(input.url).then((r) => r.json());
  if (!raw || !raw.items) throw new Error("bad payload");
  const rows: string[] = [];
  for (const item of raw.items) {
    if (item.status !== "active") continue;
    const price = item.currency === "GBP" ? "£" + item.total : "$" + item.total;
    rows.push(`${item.name}\t${price}\t${item.updatedAt}`);
  }
  const header = "name\tprice\tupdated";
  const body = [header, ...rows].join("\n");
  await fetch(input.sink, { method: "POST", body });
  return { written: rows.length, body };
}
