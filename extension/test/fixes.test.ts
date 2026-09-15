// The queue is one file written by two languages. If these identities ever
// disagree, accepting a fix in the editor creates a duplicate the Python side
// cannot recognise -- so they are pinned against real Python output.
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { acceptFix, fixId, fixKey, readQueue } from "../src/fixes";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const FINDING = {
  file: "src/pricing.ts",
  line: 42,
  severity: "critical",
  kind: "reuse",
  critic: "design",
  issue: "Reimplements applyDiscount()",
  suggestion: "Call applyDiscount() from lib/pricing.ts",
  evidence: "lib/pricing.ts:88",
};

function python(expr: string): string {
  return execFileSync("python3", ["-c", expr], { cwd: REPO, encoding: "utf8" }).trim();
}

const made: string[] = [];
function project(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cca-fix-"));
  made.push(root);
  return root;
}
afterEach(() => {
  for (const root of made.splice(0)) {
    try { fs.rmSync(root, { recursive: true, force: true }); } catch { /* best effort */ }
  }
});

describe("identity agrees with ccalib.fixes", () => {
  it("fixKey matches fix_key", () => {
    const fromPython = python(
      `import sys; sys.path.insert(0,'scripts'); from ccalib import fixes; ` +
      `print(fixes.fix_key({'file':'src/pricing.ts','line':42,` +
      `'suggestion':'Call applyDiscount() from lib/pricing.ts'}))`);
    expect(fixKey(FINDING)).toBe(fromPython);
  });

  it("fixId matches fix_id", () => {
    const fromPython = python(
      `import sys; sys.path.insert(0,'scripts'); from ccalib import fixes; ` +
      `print(fixes.fix_id({'file':'src/pricing.ts','line':42,` +
      `'suggestion':'Call applyDiscount() from lib/pricing.ts'}))`);
    expect(fixId(FINDING)).toBe(fromPython);
  });

  it("agrees on unicode too", () => {
    const fix = { file: "src/café.ts", line: 7, suggestion: "rename to caféTotal" };
    const fromPython = python(
      `import sys; sys.path.insert(0,'scripts'); from ccalib import fixes; ` +
      `print(fixes.fix_id({'file':'src/caf\\u00e9.ts','line':7,` +
      `'suggestion':'rename to caf\\u00e9Total'}))`);
    expect(fixId(fix)).toBe(fromPython);
  });
});

describe("acceptFix", () => {
  it("queues a fix as todo", () => {
    const root = project();
    const item = acceptFix(root, FINDING);
    expect(item.status).toBe("todo");
    expect(readQueue(root)).toHaveLength(1);
  });

  it("accepting twice is one item", () => {
    const root = project();
    acceptFix(root, FINDING);
    acceptFix(root, { ...FINDING });
    expect(readQueue(root)).toHaveLength(1);
  });

  it("a queue the Python side wrote is read and appended to, not clobbered", () => {
    const root = project();
    execFileSync("python3", ["-c",
      `import sys; sys.path.insert(0,'scripts'); from ccalib import fixes; ` +
      `fixes.accept(${JSON.stringify(root)}, {'file':'a.ts','line':1,'suggestion':'from python'})`],
      { cwd: REPO });
    acceptFix(root, FINDING);
    const queue = readQueue(root);
    expect(queue).toHaveLength(2);
    expect(queue.map((i) => i.suggestion)).toContain("from python");
  });

  it("a queue this side wrote is readable by Python", () => {
    const root = project();
    acceptFix(root, FINDING);
    const out = execFileSync("python3", ["-c",
      `import sys; sys.path.insert(0,'scripts'); from ccalib import fixes; ` +
      `print(len(fixes.todo_fixes(${JSON.stringify(root)}))); ` +
      `print(fixes.directive(fixes.todo_fixes(${JSON.stringify(root)})))`],
      { cwd: REPO, encoding: "utf8" });
    expect(out).toContain("1");
    expect(out).toContain("Call applyDiscount() from lib/pricing.ts");
  });

  it("a corrupt queue is an empty queue, not a crash", () => {
    const root = project();
    fs.mkdirSync(path.join(root, ".cca"), { recursive: true });
    fs.writeFileSync(path.join(root, ".cca", "fixes.json"), "{ not json");
    expect(readQueue(root)).toEqual([]);
    expect(acceptFix(root, FINDING).status).toBe("todo");
  });
});
