import * as vscode from "vscode";
import type { CcaEvent } from "./eventlog";

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export class StatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private current: number | null = null;
  private total: number | null = null;
  private open = 0;
  private pending = 0;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = "cca.openDashboard";
    this.render();
    this.item.show();
  }

  consume(events: readonly CcaEvent[]): void {
    for (const event of events) {
      if (event.type === "phase_context") {
        this.current = numberOrNull(event.payload.current);
        this.total = numberOrNull(event.payload.total) ?? this.total;
      } else if (event.type === "phase_gate" && this.current === null) {
        this.current = numberOrNull(event.payload.phase);
      }
    }
    this.render();
  }

  setOpenItems(count: number): void {
    this.open = count;
    this.render();
  }

  setPending(count: number): void {
    this.pending = count;
    this.render();
  }

  private render(): void {
    const parts = ["CCA"];
    if (this.current !== null) {
      parts.push(this.total !== null ? `phase ${this.current}/${this.total}` : `phase ${this.current}`);
    }
    if (this.open > 0) parts.push(`${this.open} open`);
    if (this.pending > 0) parts.push("$(watch) waiting on you");
    this.item.text = parts.join(" · ");
    this.item.tooltip = this.pending > 0
      ? "CCA is holding Claude for your decision"
      : "Open the CCA dashboard";
    this.item.backgroundColor = this.pending > 0
      ? new vscode.ThemeColor("statusBarItem.warningBackground")
      : undefined;
  }

  dispose(): void {
    this.item.dispose();
  }
}
