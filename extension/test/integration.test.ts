// The cross-language end-to-end test: the REAL Python hooks talking to the REAL
// extension protocol code over the real file mailbox. No editor, no model calls.
//
// What this does not cover is the VS Code surface itself -- modals, the tree
// view, the webview. Those need an Extension Development Host and a human.
import { execFileSync, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { writeJsonAtomic } from "../src/fsatomic";
import { buildDecision, parseRequest, type ArbiterRequest } from "../src/protocol";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const CRITIC = path.join(REPO, "scripts", "critic_hook.py");
const DRAIN = path.join(REPO, "scripts", "drain_hook.py");

const PLAN_LINES = [
  "### Task 1: One", "", "**Files:**", "- Create: `src/a.ts`", "",
  "- [ ] **Step 1: build it**", "",
  "### Task 2: Two", "", "**Files:**", "- Create: `src/b.ts`", "",
  "- [ ] **Step 1: build it**", "",
];

const BLOCK_VERDICT = JSON.stringify({
  verdict: "fail",
  findings: [{
    severity: "major", kind: "correctness", file: "src/a.ts", line: 1,
    issue: "Null deref on empty list", evidence: "list may be empty", suggestion: "guard",
  }],
});

const made: string[] = [];

function project(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cca-smoke-"));
  made.push(root);
  fs.mkdirSync(path.join(root, "docs/superpowers/plans"), { recursive: true });
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.mkdirSync(path.join(root, ".cca"), { recursive: true });
  fs.mkdirSync(path.join(root, "bin"), { recursive: true });

  fs.writeFileSync(path.join(root, "docs/superpowers/plans/p.md"), PLAN_LINES.join("\n"));
  fs.writeFileSync(path.join(root, ".cca/config.json"), JSON.stringify({
    phases: { enabled: true, test_command: "python3 -c 'pass'" },
    arbiter: { enabled: true, block_wait: 20, commit_wait: 20 },
  }));
  fs.writeFileSync(path.join(root, ".gitignore"), ".cca/\nCF.md\nbin/\n");
  fs.writeFileSync(path.join(root, "bin/claude"),
    `#!/bin/sh\ncat >/dev/null\nprintf '%s' '${BLOCK_VERDICT}'\n`, { mode: 0o755 });

  const git = (...args: string[]): void => {
    execFileSync("git", args, { cwd: root, stdio: "ignore" });
  };
  git("init");
  git("config", "user.email", "t@example.com");
  git("config", "user.name", "T");
  git("add", "-A");
  git("commit", "-m", "seed");
  return root;
}

/** Exactly what heartbeat.ts writes, through the same atomic writer. */
function beat(root: string, ageSeconds = 0): void {
  writeJsonAtomic(path.join(root, ".cca", "arbiter.json"), {
    pid: process.pid, ts: Date.now() / 1000 - ageSeconds, host: "vscode", version: "0.1.0",
  });
}

/** Stand in for decisions.ts: watch pending/, parse with the real parser,
 *  answer with the real builder. */
function arbiter(
  root: string,
  answer: (request: ArbiterRequest) => { choice: string; extra?: Record<string, unknown> } | null,
): void {
  const pendingDir = path.join(root, ".cca", "pending");
  const decisionsDir = path.join(root, ".cca", "decisions");
  const deadline = Date.now() + 25_000;
  const tick = setInterval(() => {
    if (Date.now() > deadline) { clearInterval(tick); return; }
    let names: string[] = [];
    try {
      names = fs.readdirSync(pendingDir).filter((name) => name.endsWith(".json"));
    } catch { return; }
    for (const name of names) {
      let request: ArbiterRequest | null = null;
      try {
        request = parseRequest(fs.readFileSync(path.join(pendingDir, name), "utf8"));
      } catch { continue; }
      if (!request) continue;
      const reply = answer(request);
      if (!reply) continue;
      const decision = buildDecision(request, reply.choice, reply.extra ?? {});
      if (!decision) continue;
      writeJsonAtomic(path.join(decisionsDir, `${request.id}.json`), decision);
      clearInterval(tick);
      return;
    }
  }, 50);
}

function hook(script: string, root: string, event: Record<string, unknown>): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", [script], {
      cwd: root,
      env: {
        ...process.env,
        PATH: `${path.join(root, "bin")}${path.delimiter}${process.env.PATH ?? ""}`,
        CLAUDE_PROJECT_DIR: root,
        CCA_INNER: "",
      },
    });
    let out = "";
    child.stdout.on("data", (chunk: Buffer) => { out += chunk.toString(); });
    child.on("error", reject);
    child.on("close", () => resolve(out));
    child.stdin.end(JSON.stringify(event));
  });
}

const editEvent = (root: string): Record<string, unknown> => ({
  tool_name: "Edit", cwd: root, session_id: "smoke001",
  tool_input: { file_path: "src/a.ts", old_string: "a", new_string: "b" },
});

const stopEvent = (root: string): Record<string, unknown> =>
  ({ stop_hook_active: false, cwd: root, session_id: "smoke001" });

afterEach(() => {
  for (const root of made.splice(0)) {
    try { fs.rmSync(root, { recursive: true, force: true }); } catch { /* best effort */ }
  }
});

describe("the mailbox, end to end", () => {
  it("blocks when no extension is present", { timeout: 60_000 }, async () => {
    const root = project();
    const out = await hook(CRITIC, root, editEvent(root));
    expect(JSON.parse(out).decision).toBe("block");
  });

  it("blocks when the heartbeat is stale", { timeout: 60_000 }, async () => {
    const root = project();
    beat(root, 120);
    const out = await hook(CRITIC, root, editEvent(root));
    expect(JSON.parse(out).decision).toBe("block");
  });

  it("lets the edit stand when the human overrules, and records the note", async () => {
    const root = project();
    beat(root);
    arbiter(root, () => ({ choice: "overrule", extra: { note: "test-only helper" } }));
    const out = await hook(CRITIC, root, editEvent(root));
    expect(out.trim()).toBe("");
    const log = fs.readFileSync(path.join(root, ".cca", "events.jsonl"), "utf8");
    const notes = log.split("\n").filter(Boolean).map((l) => JSON.parse(l))
      .filter((e) => e.type === "arbitration").map((e) => e.payload.note);
    expect(notes).toContain("test-only helper");
  }, 60_000);

  it("writes CF.md when the human queues instead", { timeout: 60_000 }, async () => {
    const root = project();
    beat(root);
    arbiter(root, () => ({ choice: "queue" }));
    const out = await hook(CRITIC, root, editEvent(root));
    expect(JSON.parse(out)).not.toHaveProperty("decision");
    expect(fs.readFileSync(path.join(root, "CF.md"), "utf8")).toContain("Null deref");
  });

  it("upholds the block when the human dismisses the question", { timeout: 60_000 }, async () => {
    const root = project();
    beat(root);
    arbiter(root, () => null); // dismissed: the extension writes nothing
    const out = await hook(CRITIC, root, editEvent(root));
    expect(JSON.parse(out).decision).toBe("block");
  });

  it("commits the phase when the human clicks Commit", { timeout: 60_000 }, async () => {
    const root = project();
    fs.writeFileSync(path.join(root, "src/a.ts"), "phase 1 work\n");
    beat(root);
    arbiter(root, (request) => {
      // what decisions.ts does: git add the phase's files plus the plan, commit
      const files = [...(request.phase?.files ?? [])];
      if (request.planRel) files.push(request.planRel);
      const present = files.filter((f) => fs.existsSync(path.join(root, f)));
      try {
        execFileSync("git", ["add", "--", ...present], { cwd: root, stdio: "ignore" });
        execFileSync("git", ["commit", "-m", request.message ?? "x"], { cwd: root, stdio: "ignore" });
      } catch {
        return { choice: "commit", extra: { ok: false, error: "git failed" } };
      }
      return { choice: "commit", extra: { ok: true } };
    });
    const out = await hook(DRAIN, root, stopEvent(root));
    expect(out.trim()).toBe("");

    const log = execFileSync("git", ["log", "--format=%s"], { cwd: root, encoding: "utf8" });
    expect(log).toContain("[phase-1] One");
    const planInCommit = execFileSync(
      "git", ["show", "HEAD:docs/superpowers/plans/p.md"], { cwd: root, encoding: "utf8" });
    expect(planInCommit).toContain("- [x] **Step 1: build it**");
  });

  it("blocks with git's own error when the commit fails", { timeout: 60_000 }, async () => {
    const root = project();
    fs.writeFileSync(path.join(root, "src/a.ts"), "phase 1 work\n");
    beat(root);
    arbiter(root, () => ({
      choice: "commit", extra: { ok: false, error: "nothing to commit, working tree clean" },
    }));
    const out = await hook(DRAIN, root, stopEvent(root));
    expect(JSON.parse(out).reason).toContain("nothing to commit");
  });

  it("falls back to the printed command when the human picks Later", { timeout: 60_000 }, async () => {
    const root = project();
    fs.writeFileSync(path.join(root, "src/a.ts"), "phase 1 work\n");
    beat(root);
    arbiter(root, () => ({ choice: "later" }));
    const out = await hook(DRAIN, root, stopEvent(root));
    const reason = JSON.parse(out).reason as string;
    expect(reason).toContain("git add");
    expect(reason).toContain("[phase-1]");
  });
});
