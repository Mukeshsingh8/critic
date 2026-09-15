import { describe, expect, it } from "vitest";
import { buildDecision, isExpired, parseRequest } from "../src/protocol";

const BLOCK = JSON.stringify({
  id: "1757880000123-sess0001-0a1b",
  kind: "block",
  created: 1757880000.123,
  deadline: 1757880180.123,
  options: ["uphold", "queue", "overrule"],
  session_id: "sess0001",
  file: "src/a.ts",
  reason: "Critics rejected this change",
  findings: [{
    severity: "major", kind: "correctness", file: "src/a.ts", line: 12,
    issue: "Null deref on empty list", evidence: "", suggestion: "guard",
  }],
});

const COMMIT = JSON.stringify({
  id: "1757880000999-sess0001-0c3d",
  kind: "commit",
  created: 1757880000.9,
  deadline: 1757880300.9,
  options: ["commit", "edit", "later"],
  session_id: "sess0001",
  phase: { number: 3, title: "Critic runner", total: 11, files: ["scripts/ccalib/runner.py"] },
  plan_rel: "docs/superpowers/plans/p.md",
  command: "git add scripts/ccalib/runner.py \\\n  && git commit -m \"[phase-3] Critic runner\"",
  message: "[phase-3] Critic runner",
  checks: { tests: true, critics: true, commit: false, tree: true },
});

describe("parseRequest", () => {
  it("reads a block request including its findings", () => {
    const request = parseRequest(BLOCK);
    expect(request?.kind).toBe("block");
    expect(request?.file).toBe("src/a.ts");
    expect(request?.options).toEqual(["uphold", "queue", "overrule"]);
    expect(request?.findings?.[0].issue).toBe("Null deref on empty list");
    expect(request?.sessionId).toBe("sess0001");
  });

  it("reads a commit request including its phase", () => {
    const request = parseRequest(COMMIT);
    expect(request?.kind).toBe("commit");
    expect(request?.phase?.number).toBe(3);
    expect(request?.phase?.total).toBe(11);
    expect(request?.message).toBe("[phase-3] Critic runner");
    expect(request?.planRel).toBe("docs/superpowers/plans/p.md");
  });

  it("rejects malformed input rather than throwing", () => {
    for (const bad of ["", "{ not json", "[]", "null", "42", '{"id":"x"}',
                       '{"id":"x","kind":"nope","options":["a"],"deadline":1}',
                       '{"id":"x","kind":"block","options":[],"deadline":1}',
                       '{"kind":"block","options":["a"],"deadline":1}']) {
      expect(parseRequest(bad)).toBeNull();
    }
  });

  it("drops non-string options and malformed findings", () => {
    const request = parseRequest(JSON.stringify({
      id: "x", kind: "block", created: 1, deadline: 2,
      options: ["uphold", 7, null, "queue"],
      findings: ["nope", { severity: "minor", kind: "reuse", file: "a.ts", issue: "dup" }],
    }));
    expect(request?.options).toEqual(["uphold", "queue"]);
    expect(request?.findings).toHaveLength(1);
    expect(request?.findings?.[0].file).toBe("a.ts");
  });
});

describe("isExpired", () => {
  it("compares the deadline in seconds against a millisecond clock", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    expect(isExpired(request, 1757880100_000)).toBe(false);
    expect(isExpired(request, 1757880300_000)).toBe(true);
  });
});

describe("buildDecision", () => {
  it("accepts a choice the request offered", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    const decision = buildDecision(request, "overrule", { note: "test-only helper" });
    expect(decision?.id).toBe(request.id);
    expect(decision?.choice).toBe("overrule");
    expect(decision?.note).toBe("test-only helper");
    expect(typeof decision?.ts).toBe("number");
  });

  it("refuses a choice the request did not offer", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    expect(buildDecision(request, "commit")).toBeNull();
  });

  it("carries ok and error for a commit", () => {
    const request = parseRequest(COMMIT);
    if (!request) throw new Error("unreachable");
    const decision = buildDecision(request, "commit", { ok: false, error: "nothing to commit" });
    expect(decision?.ok).toBe(false);
    expect(decision?.error).toBe("nothing to commit");
  });
});
