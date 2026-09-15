import { describe, expect, it } from "vitest";
import { parseCf, wontfixHeading } from "../src/cfmd";

const CF = [
  "# Critic Feedback (CF.md)",
  "",
  "Open items block the session from finishing. To arbitrate: fix the item and",
  "delete its heading, or insert a [wontfix] tag into the heading with a reason.",
  "",
  "## 2026-09-14 10:00:00 -- src/a.ts",
  "- **major / correctness** `src/a.ts:12` -- Null deref on empty list",
  "  - suggestion: guard",
  "",
  "## 2026-09-14 10:05:00 -- src/b.ts [wontfix] intentional duplication",
  "- **minor / reuse** `src/b.ts:4` -- Looks like tidy()",
  "",
  "## 2026-09-14 10:09:00 -- src/c.ts",
  "- **minor / convention** `src/c.ts:1` -- Prefer text over varchar",
  "",
].join("\n");

describe("parseCf", () => {
  it("finds every entry and nothing else", () => {
    const entries = parseCf(CF);
    expect(entries).toHaveLength(3);
    expect(entries[0].heading).toBe("## 2026-09-14 10:00:00 -- src/a.ts");
  });

  it("marks wontfix entries", () => {
    const entries = parseCf(CF);
    expect(entries.map((entry) => entry.wontfix)).toEqual([false, true, false]);
  });

  it("gives each entry a line range that stops at the next heading", () => {
    const entries = parseCf(CF);
    expect(entries[0].startLine).toBe(5);
    expect(entries[0].endLine).toBe(9);
    expect(entries[1].startLine).toBe(9);
  });

  it("runs the last entry to the end of the file", () => {
    const entries = parseCf(CF);
    const lines = CF.split("\n");
    expect(entries[2].endLine).toBe(lines.length);
  });

  it("keeps the body lines of an entry", () => {
    expect(parseCf(CF)[0].body).toEqual([
      "- **major / correctness** `src/a.ts:12` -- Null deref on empty list",
      "  - suggestion: guard",
      "",
    ]);
  });

  it("is not fooled by the header text or by an h1/h3", () => {
    expect(parseCf("# Title\n### Task 3\nbody\n")).toEqual([]);
  });

  it("returns nothing for an empty file", () => {
    expect(parseCf("")).toEqual([]);
  });

  it("detects [WontFix] regardless of case, matching open_items()", () => {
    expect(parseCf("## a [WONTFIX] because\n")[0].wontfix).toBe(true);
  });
});

describe("wontfixHeading", () => {
  it("appends the tag and the reason", () => {
    expect(wontfixHeading("## 2026-09-14 -- src/a.ts", "test-only helper"))
      .toBe("## 2026-09-14 -- src/a.ts [wontfix] test-only helper");
  });

  it("collapses newlines so the heading stays one line", () => {
    expect(wontfixHeading("## x", "two\nlines  here"))
      .toBe("## x [wontfix] two lines here");
  });

  it("still tags when no reason is given", () => {
    expect(wontfixHeading("## x", "   ")).toBe("## x [wontfix]");
  });

  it("leaves an already-tagged heading alone", () => {
    const tagged = "## x [wontfix] done";
    expect(wontfixHeading(tagged, "again")).toBe(tagged);
  });
});
