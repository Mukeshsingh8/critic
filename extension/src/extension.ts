import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { registerFeedbackTree } from "./cfTree";
import { registerDashboard } from "./dashboard";
import { DecisionWatcher } from "./decisions";
import { EventFeed } from "./feed";
import { Heartbeat } from "./heartbeat";
import { offerNextPhase } from "./nextSession";
import { StatusBar } from "./statusBar";

/** The project the hooks are writing into: the first workspace folder that has
 *  a `.cca/`, else the first folder at all (the user invoked the command in a
 *  project where the plugin has not run yet). */
export function resolveRoot(folders: readonly vscode.WorkspaceFolder[] | undefined): string | undefined {
  if (!folders || folders.length === 0) return undefined;
  const withCca = folders.find((folder) => fs.existsSync(path.join(folder.uri.fsPath, ".cca")));
  return (withCca ?? folders[0]).uri.fsPath;
}

export function activate(context: vscode.ExtensionContext): void {
  const root = resolveRoot(vscode.workspace.workspaceFolders);
  if (!root) return;

  const version = String(context.extension.packageJSON.version ?? "0.0.0");
  const heartbeat = new Heartbeat(root, version);
  heartbeat.start();

  const feed = new EventFeed(root);
  const status = new StatusBar();
  feed.onEvents((events) => status.consume(events));
  status.consume(feed.history);

  context.subscriptions.push(heartbeat, feed, status);

  const decisions = new DecisionWatcher(root, status);
  context.subscriptions.push(decisions);
  registerDashboard(context, root, feed, decisions);
  registerFeedbackTree(context, root, status);
  decisions.onShipped((phase) => offerNextPhase(root, phase));
}

export function deactivate(): void {
  // Disposables registered above handle the heartbeat removal.
}
