
export interface CcaEvent {
  readonly type: string;
  readonly ts: number;
  readonly payload: Record<string, unknown>;
}

/** A file, as this module needs it. Injected so the parsing can be tested
 *  without touching a disk. */
export interface FileSlice {
  size(): Promise<number>;
  read(from: number, to: number): Promise<Uint8Array>;
}

function parseEvent(line: string): CcaEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(trimmed);
  } catch {
    return null; // a corrupt line must never break the feed
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.type !== "string") return null;
  const payload = record.payload;
  return {
    type: record.type,
    ts: typeof record.ts === "number" ? record.ts : 0,
    payload:
      typeof payload === "object" && payload !== null && !Array.isArray(payload)
        ? (payload as Record<string, unknown>)
        : {},
  };
}

export class EventLogTail {
  private offset = 0;
  private partial = "";
  private decoder = new TextDecoder("utf-8");

  async poll(slice: FileSlice): Promise<CcaEvent[]> {
    const size = await slice.size();
    if (size < this.offset) this.reset(); // truncated or replaced
    if (size === this.offset) return [];

    const chunk = await slice.read(this.offset, size);
    this.offset = size;

    // `stream: true` is what makes a multibyte character split across two
    // reads survive: the decoder holds the incomplete sequence for next time.
    const text = this.partial + this.decoder.decode(chunk, { stream: true });
    const lines = text.split("\n");
    this.partial = lines.pop() ?? "";

    const events: CcaEvent[] = [];
    for (const line of lines) {
      const event = parseEvent(line);
      if (event) events.push(event);
    }
    return events;
  }

  reset(): void {
    this.offset = 0;
    this.partial = "";
    this.decoder = new TextDecoder("utf-8");
  }
}
