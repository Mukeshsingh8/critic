import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { writeJsonAtomic } from "./fsatomic";
import { runGit } from "./git";
import { buildDecision, isExpired, parseRequest, type ArbiterRequest } from "./protocol";
import type { StatusBar } from "./statusBar";

const LABELS: Readonly<Record<string, string>> = {
  uphold: "Uphold block",
  queue: "Queue to CF.md",
  overrule: "Overrule…",
  commit: "Commit",
  edit: "Edit message…",
  later: "Later",
};

const EXPIRY_SWEEP_MS = 2000;

export class DecisionWatcher implements vscode.Disposable {
  private readonly pendingDir: string;
  private readonly decisionsDir: string;
  private readonly watcher: vscode.FileSystemWatcher;
  private readonly timer: ReturnType<typeof setInterval>;
  private readonly live = new Map<string, ArbiterRequest>();
  private readonly claimed = new Set<string>();
  private readonly pendingEmitter = new vscode.EventEmitter<readonly ArbiterRequest[]>();
  private readonly shippedEmitter = new vscode.EventEmitter<number>();

  readonly onPending = this.pendingEmitter.event;
  readonly onShipped = this.shippedEmitter.event;

  constructor(private readonly root: string, private readonly status: StatusBar) {
    this.pendingDir = path.join(root, ".cca", "pending");
    this.decisionsDir = path.join(root, ".cca", "decisions");
    this.watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(vscode.Uri.file(this.pendingDir), "*.json"),
    );
    this.watcher.onDidCreate(() => this.scan());
    this.watcher.onDidChange(() => this.scan());
    this.watcher.onDidDelete(() => this.scan());
    this.timer = setInterval(() => this.scan(), EXPIRY_SWEEP_MS);
    this.scan();
  }

  get pending(): readonly ArbiterRequest[] {
    return [...this.live.values()];
  }

  private scan(): void {
    let names: string[] = [];
    try {
      names = fs.readdirSync(this.pendingDir).filter((name) => name.endsWith(".json"));
    } catch {
      names = []; // directory not created yet, or already swept
    }

    const now = Date.now();
    const alive = new Set<string>();
    for (const name of names) {
      let text: string;
      try {
        text = fs.readFileSync(path.join(this.pendingDir, name), "utf8");
      } catch {
        continue; // mid-rename; the next scan sees it
      }
      const request = parseRequest(text);
      if (!request || isExpired(request, now)) continue;
      alive.add(request.id);
      if (!this.live.has(request.id)) {
        this.live.set(request.id, request);
        void this.ask(request);
      }
    }

    // A request the hook has answered, swept or given up on stops being ours.
    for (const id of [...this.live.keys()]) {
      if (!alive.has(id)) {
        this.live.delete(id);
        this.claimed.delete(id);
      }
    }

    this.status.setPending(this.live.size);
    this.pendingEmitter.fire(this.pending);
  }

  /** Answer whichever live request offers this choice. The board uses it to
   *  act on a block while Claude is still held, instead of queueing for the
   *  next Stop. Returns false when nothing is actually waiting. */
  answer(choice: string): boolean {
    for (const request of this.live.values()) {
      if (!request.options.includes(choice)) continue;
      this.claimed.add(request.id); // the modal must not also fire
      this.write(request, choice);
      return true;
    }
    return false;
  }

  private async ask(request: ArbiterRequest): Promise<void> {
    if (this.claimed.has(request.id)) return;
    this.claimed.add(request.id);

    const buttons = request.options.map((option) => LABELS[option] ?? option);
    const picked = await vscode.window.showWarningMessage(
      headline(request),
      { modal: true, detail: detail(request) },
      ...buttons,
    );
    // Dismissed. Write nothing: the hook's own default is the safe answer.
    if (picked === undefined) return;

    const choice = request.options[buttons.indexOf(picked)];
    if (choice === undefined) return;

    if (choice === "overrule") {
      const note = await vscode.window.showInputBox({
        prompt: "Why is this finding wrong? (recorded in the event log)",
        placeHolder: "e.g. the helper is test-only",
      });
      this.write(request, choice, { note: note ?? "" });
      return;
    }

    if (choice === "commit" || choice === "edit") {
      await this.commit(request, choice);
      return;
    }

    this.write(request, choice);
  }

  private async commit(request: ArbiterRequest, choice: string): Promise<void> {
    let message = request.message ?? "";
    if (choice === "edit") {
      const edited = await vscode.window.showInputBox({
        prompt: "Commit message",
        value: message,
        valueSelection: [message.length, message.length],
      });
      if (edited === undefined) return; // cancelled; let the hook time out to Later
      message = edited;
    }
    if (!message.trim()) {
      this.write(request, choice, { ok: false, error: "empty commit message" });
      return;
    }

    const files = [...(request.phase?.files ?? [])];
    if (request.planRel) files.push(request.planRel);
    const present = files.filter((file) => fs.existsSync(path.join(this.root, file)));
    if (present.length === 0) {
      this.write(request, choice, {
        ok: false,
        error: "none of this phase's files exist on disk; nothing to add",
      });
      return;
    }

    const added = await runGit(this.root, ["add", "--", ...present]);
    if (!added.ok) {
      this.write(request, choice, { ok: false, error: added.error });
      return;
    }
    const committed = await runGit(this.root, ["commit", "-m", message]);
    this.write(request, choice, { ok: committed.ok, error: committed.error });
    if (committed.ok && request.phase) this.shippedEmitter.fire(request.phase.number);
  }

  private write(
    request: ArbiterRequest,
    choice: string,
    extra: { note?: string; ok?: boolean; error?: string } = {},
  ): void {
    const decision = buildDecision(request, choice, extra);
    if (!decision) return;
    try {
      writeJsonAtomic(path.join(this.decisionsDir, `${request.id}.json`), decision);
    } catch (error) {
      void vscode.window.showErrorMessage(
        `CCA could not write your decision: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  dispose(): void {
    clearInterval(this.timer);
    this.watcher.dispose();
    this.pendingEmitter.dispose();
    this.shippedEmitter.dispose();
  }
}

function headline(request: ArbiterRequest): string {
  if (request.kind === "commit" && request.phase) {
    return `Phase ${request.phase.number}/${request.phase.total} — ${request.phase.title} is ready to ship.`;
  }
  return `Critics rejected the change to ${request.file ?? "this file"}.`;
}

function detail(request: ArbiterRequest): string {
  if (request.kind === "commit") {
    return [request.command ?? "", "", "Commit runs as you, from this workspace."]
      .join("\n")
      .trim();
  }
  const findings = (request.findings ?? [])
    .map((finding) => `• [${finding.severity}] ${finding.file}:${finding.line} — ${finding.issue}`)
    .join("\n");
  return findings || request.reason || "";
}
