import * as vscode from "vscode";
import type { DecisionWatcher } from "./decisions";
import type { EventFeed } from "./feed";
import { acceptFix, logAccepted, logEvent, skipFix, type FixRequest } from "./fixes";
import { nudgeTerminal } from "./nextSession";

let panel: vscode.WebviewPanel | undefined;

export function registerDashboard(
  context: vscode.ExtensionContext,
  root: string,
  feed: EventFeed,
  decisions: DecisionWatcher,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("cca.openDashboard", () => {
      if (panel) {
        panel.reveal(vscode.ViewColumn.Beside);
        return;
      }
      panel = vscode.window.createWebviewPanel(
        "ccaDashboard",
        "CCA Ensemble",
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        {
          enableScripts: true,
          retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
        },
      );
      const view = panel.webview;
      view.html = html(view, context.extensionUri, root);

      const ready = view.onDidReceiveMessage(
        (message: { kind?: string; action?: string; choice?: string; fix?: FixRequest }) => {
          if (message.kind === "ready") {
            void view.postMessage({ kind: "reset", events: feed.history });
            void view.postMessage({ kind: "pending", requests: decisions.pending });
            return;
          }
          if (message.kind === "decideFix" && message.fix) {
            if (message.action === "skip") {
              const item = skipFix(root, message.fix);
              logEvent(root, "fix_skipped", {
                id: item.id, key: item.key, file: item.file, via: "extension" });
            } else {
              const item = acceptFix(root, message.fix);
              logAccepted(root, item);
            }
            void feed.poll();
            return;
          }
          if (message.kind === "answerArbiter" && message.choice) {
            const delivered = decisions.answer(message.choice);
            if (!delivered && message.choice === "fix") nudgeTerminal(1);
            void feed.poll();
          }
        },
      );
      const streaming = feed.onEvents((events) => {
        void view.postMessage({ kind: "events", events });
      });
      const waiting = decisions.onPending((requests) => {
        void view.postMessage({ kind: "pending", requests });
      });

      panel.onDidDispose(() => {
        ready.dispose();
        streaming.dispose();
        waiting.dispose();
        panel = undefined;
      });
    }),
  );
}

function html(view: vscode.Webview, extensionUri: vscode.Uri, root: string): string {
  const css = view.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", "dashboard.css"));
  const js = view.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", "dashboard.js"));
  const nonce = Array.from({ length: 32 }, () =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"[
      Math.floor(Math.random() * 62)
    ]).join("");
  const project = root.split(/[\\/]/).pop() ?? root;
  // The body is built by dashboard.js so the standalone server and this
  // webview render the identical board from one source.
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src ${view.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; img-src ${view.cspSource} data:;">
<link rel="stylesheet" href="${css}">
<title>CCA Precinct — ${escapeHtml(project)}</title>
</head>
<body>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
