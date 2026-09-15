// The mailbox format. This file and scripts/ccalib/arbiter.py are the only two
// places that know it; everything else consumes these types.

export type RequestKind = "block" | "commit";

export interface Finding {
  readonly severity: string;
  readonly kind: string;
  readonly file: string;
  readonly line: number | string;
  readonly issue: string;
  readonly evidence: string;
  readonly suggestion: string;
}

export interface PhaseRef {
  readonly number: number;
  readonly title: string;
  readonly total: number;
  readonly files: readonly string[];
}

export interface GateChecks {
  readonly tests: boolean | null;
  readonly critics: boolean | null;
  readonly commit: boolean | null;
  readonly tree: boolean | null;
}

export interface ArbiterRequest {
  readonly id: string;
  readonly kind: RequestKind;
  readonly created: number;
  readonly deadline: number;
  readonly options: readonly string[];
  readonly sessionId: string;
  readonly file?: string;
  readonly reason?: string;
  readonly findings?: readonly Finding[];
  readonly phase?: PhaseRef;
  readonly planRel?: string;
  readonly command?: string;
  readonly message?: string;
}

export interface Decision {
  readonly id: string;
  readonly choice: string;
  readonly ts: number;
  readonly note?: string;
  readonly ok?: boolean;
  readonly error?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asFindings(value: unknown): Finding[] {
  if (!Array.isArray(value)) return [];
  const out: Finding[] = [];
  for (const item of value) {
    const record = asRecord(item);
    if (!record) continue;
    const file = asString(record.file);
    if (!file) continue;
    const line = record.line;
    out.push({
      severity: asString(record.severity, "minor"),
      kind: asString(record.kind),
      file,
      line: typeof line === "number" || typeof line === "string" ? line : 0,
      issue: asString(record.issue),
      evidence: asString(record.evidence),
      suggestion: asString(record.suggestion),
    });
  }
  return out;
}

function asPhase(value: unknown): PhaseRef | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  return {
    number: asNumber(record.number),
    title: asString(record.title),
    total: asNumber(record.total),
    files: asStrings(record.files),
  };
}

export function parseRequest(text: string): ArbiterRequest | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  const record = asRecord(raw);
  if (!record) return null;

  const id = asString(record.id);
  const kind = asString(record.kind);
  const options = asStrings(record.options);
  if (!id || (kind !== "block" && kind !== "commit") || options.length === 0) return null;

  return {
    id,
    kind,
    created: asNumber(record.created),
    deadline: asNumber(record.deadline),
    options,
    sessionId: asString(record.session_id),
    file: asString(record.file) || undefined,
    reason: asString(record.reason) || undefined,
    findings: asFindings(record.findings),
    phase: asPhase(record.phase),
    planRel: asString(record.plan_rel) || undefined,
    command: asString(record.command) || undefined,
    message: asString(record.message) || undefined,
  };
}

/** `deadline` is a POSIX time in SECONDS (Python's `time.time()`); `now` is
 *  milliseconds (`Date.now()`). Getting this wrong expires everything
 *  instantly, so the conversion lives here and nowhere else. */
export function isExpired(request: ArbiterRequest, now: number): boolean {
  return now / 1000 >= request.deadline;
}

export function buildDecision(
  request: ArbiterRequest,
  choice: string,
  extra: { note?: string; ok?: boolean; error?: string } = {},
): Decision | null {
  if (!request.options.includes(choice)) return null;
  const decision: Decision = {
    id: request.id,
    choice,
    ts: Date.now() / 1000,
    ...(extra.note === undefined ? {} : { note: extra.note }),
    ...(extra.ok === undefined ? {} : { ok: extra.ok }),
    ...(extra.error === undefined ? {} : { error: extra.error }),
  };
  return decision;
}
