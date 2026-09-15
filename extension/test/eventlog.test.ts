import { describe, expect, it } from "vitest";
import { EventLogTail, type FileSlice } from "../src/eventlog";

class FakeLog implements FileSlice {
  private bytes: Buffer = Buffer.alloc(0);

  append(text: string): void {
    this.bytes = Buffer.concat([this.bytes, Buffer.from(text, "utf8")]);
  }

  appendBytes(extra: Buffer): void {
    this.bytes = Buffer.concat([this.bytes, extra]);
  }

  replace(text: string): void {
    this.bytes = Buffer.from(text, "utf8");
  }

  async size(): Promise<number> {
    return this.bytes.byteLength;
  }

  async read(from: number, to: number): Promise<Uint8Array> {
    return this.bytes.subarray(from, to);
  }
}

const line = (type: string, payload: Record<string, unknown> = {}): string =>
  `${JSON.stringify({ type, ts: 1.0, payload })}\n`;

describe("EventLogTail", () => {
  it("returns nothing for an empty log", async () => {
    expect(await new EventLogTail().poll(new FakeLog())).toEqual([]);
  });

  it("returns only what is new on each poll", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("edit_started", { file: "a.ts" }));
    expect(await tail.poll(log)).toHaveLength(1);
    expect(await tail.poll(log)).toHaveLength(0);
    log.append(line("vote", { decision: "pass" }));
    const next = await tail.poll(log);
    expect(next).toHaveLength(1);
    expect(next[0].type).toBe("vote");
    expect(next[0].payload.decision).toBe("pass");
  });

  it("holds a partial trailing line until it is complete", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    const whole = line("vote", { decision: "block" });
    log.append(whole.slice(0, 10));
    expect(await tail.poll(log)).toEqual([]);
    log.append(whole.slice(10));
    expect(await tail.poll(log)).toHaveLength(1);
  });

  it("skips a corrupt line without losing the ones around it", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("vote") + "NOT JSON\n" + line("queued"));
    const seen = await tail.poll(log);
    expect(seen.map((event) => event.type)).toEqual(["vote", "queued"]);
  });

  it("skips a line that is valid JSON but not an event", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append('[1,2,3]\n"just a string"\n{"nope":1}\n' + line("vote"));
    expect(await tail.poll(log)).toHaveLength(1);
  });

  it("restarts from the beginning when the log is truncated", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("edit_started") + line("vote"));
    expect(await tail.poll(log)).toHaveLength(2);
    log.replace(line("queued"));
    const seen = await tail.poll(log);
    expect(seen).toHaveLength(1);
    expect(seen[0].type).toBe("queued");
  });

  it("survives a multibyte character split across two reads", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    const bytes = Buffer.from(line("queued", { file: "src/café.ts" }), "utf8");
    // cut the buffer in the middle of the two-byte é
    const split = bytes.indexOf(Buffer.from("é", "utf8")) + 1;
    log.appendBytes(bytes.subarray(0, split));
    expect(await tail.poll(log)).toEqual([]);
    log.appendBytes(bytes.subarray(split));
    const seen = await tail.poll(log);
    expect(seen).toHaveLength(1);
    expect(seen[0].payload.file).toBe("src/café.ts");
  });

  it("reset() makes the next poll replay everything", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("vote"));
    await tail.poll(log);
    tail.reset();
    expect(await tail.poll(log)).toHaveLength(1);
  });
});
