import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { writeJsonAtomic } from "./fsatomic";

/** Mirrors ccalib.fixes: the queue is one JSON file both sides read. */
export interface QueuedFix {
  readonly id: string;
  readonly key: string;
  readonly file: string;
  readonly line: number | string;
  readonly severity: string;
  readonly kind: string;
  readonly critic: string;
  readonly issue: string;
  readonly suggestion: string;
  readonly evidence: string;
  status: string;
  accepted_at: number;
  sent_at: number | null;
  done_at: number | null;
}

export interface FixRequest {
  readonly file?: string;
  readonly line?: number | string;
  readonly severity?: string;
  readonly kind?: string;
  readonly critic?: string;
  readonly issue?: string;
  readonly suggestion?: string;
  readonly evidence?: string;
}

/** "file|line|suggestion" — byte-identical to ccalib.fixes.fix_key. */
export function fixKey(fix: FixRequest): string {
  return `${fix.file ?? ""}|${fix.line ?? ""}|${fix.suggestion ?? ""}`;
}

/** sha1(key)[:12], matching ccalib.fixes.fix_id. Node's crypto is synchronous,
 *  which the browser's SubtleCrypto is not — hence doing it here, not in the
 *  webview. */
export function fixId(fix: FixRequest): string {
  return createHash("sha1").update(fixKey(fix), "utf8").digest("hex").slice(0, 12);
}

function queuePath(root: string): string {
  return path.join(root, ".cca", "fixes.json");
}

export function readQueue(root: string): QueuedFix[] {
  try {
    const raw: unknown = JSON.parse(fs.readFileSync(queuePath(root), "utf8"));
    if (typeof raw !== "object" || raw === null) return [];
    const list = (raw as { fixes?: unknown }).fixes;
    return Array.isArray(list) ? (list.filter((i) => typeof i === "object" && i !== null) as QueuedFix[]) : [];
  } catch {
    return []; // absent or corrupt is an empty queue, never an error
  }
}

/** Queue a fix. Re-accepting a closed one reopens it, exactly as Python does. */
export function acceptFix(root: string, fix: FixRequest): QueuedFix {
  const items = readQueue(root);
  const id = fixId(fix);
  const now = Date.now() / 1000;

  const existing = items.find((item) => item.id === id);
  if (existing) {
    if (existing.status === "done" || existing.status === "dismissed") {
      existing.status = "todo";
      existing.accepted_at = now;
      existing.sent_at = null;
      existing.done_at = null;
      writeJsonAtomic(queuePath(root), { fixes: items });
    }
    return existing;
  }

  const item: QueuedFix = {
    id,
    key: fixKey(fix),
    file: String(fix.file ?? ""),
    line: fix.line ?? 0,
    severity: String(fix.severity ?? "minor"),
    kind: String(fix.kind ?? ""),
    critic: String(fix.critic ?? ""),
    issue: String(fix.issue ?? ""),
    suggestion: String(fix.suggestion ?? ""),
    evidence: String(fix.evidence ?? ""),
    status: "todo",
    accepted_at: now,
    sent_at: null,
    done_at: null,
  };
  items.push(item);
  writeJsonAtomic(queuePath(root), { fixes: items.slice(-200) });
  return item;
}

/** Append the same `fix_accepted` event the Python side writes, so the board
 *  shows the new state on its next read whichever host queued it. */
export function logAccepted(root: string, item: QueuedFix): void {
  const line = JSON.stringify({
    type: "fix_accepted",
    ts: Date.now() / 1000,
    payload: { id: item.id, key: item.key, file: item.file, via: "extension" },
  });
  try {
    fs.mkdirSync(path.join(root, ".cca"), { recursive: true });
    fs.appendFileSync(path.join(root, ".cca", "events.jsonl"), line + "\n", "utf8");
  } catch {
    /* the queue is the source of truth; the event is only for the feed */
  }
}

/** The arbiter's refusal. Recorded as dismissed rather than removed, so the
 *  board shows it was judged and nothing later claims it was done. */
export function skipFix(root: string, fix: FixRequest): QueuedFix {
  const item = acceptFix(root, fix);
  const items = readQueue(root);
  const found = items.find((entry) => entry.id === item.id);
  if (found) {
    found.status = "dismissed";
    found.done_at = Date.now() / 1000;
    writeJsonAtomic(queuePath(root), { fixes: items });
    return found;
  }
  return item;
}

export function logEvent(root: string, type: string, payload: unknown): void {
  const line = JSON.stringify({ type, ts: Date.now() / 1000, payload });
  try {
    fs.mkdirSync(path.join(root, ".cca"), { recursive: true });
    fs.appendFileSync(path.join(root, ".cca", "events.jsonl"), line + "\n", "utf8");
  } catch {
    /* the queue is the source of truth; the event is only for the feed */
  }
}
