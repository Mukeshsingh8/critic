import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { parseCf, wontfixHeading, type CfEntry } from "./cfmd";
import type { StatusBar } from "./statusBar";

interface GroupNode {
  readonly kind: "group";
  readonly group: "open" | "wontfix";
}

interface EntryNode {
  readonly kind: "entry";
  readonly entry: CfEntry;
}

type Node = GroupNode | EntryNode;

function isGroup(node: Node): node is GroupNode {
  return node.kind === "group";
}

class FeedbackProvider implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  private entries: CfEntry[] = [];

  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly root: string, private readonly status: StatusBar) {
    this.reload();
  }

  get file(): string {
    return path.join(this.root, "CF.md");
  }

  reload(): void {
    let text = "";
    try {
      text = fs.readFileSync(this.file, "utf8");
    } catch {
      text = ""; // no findings yet
    }
    this.entries = parseCf(text);
    this.status.setOpenItems(this.entries.filter((entry) => !entry.wontfix).length);
    this.changed.fire(undefined);
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      const groups: Node[] = [{ kind: "group", group: "open" }];
      if (this.entries.some((entry) => entry.wontfix)) {
        groups.push({ kind: "group", group: "wontfix" });
      }
      return groups;
    }
    if (!isGroup(node)) return [];
    const wontfix = node.group === "wontfix";
    return this.entries
      .filter((entry) => entry.wontfix === wontfix)
      .map((entry): Node => ({ kind: "entry", entry }));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (isGroup(node)) {
      const open = this.entries.filter((entry) => !entry.wontfix).length;
      const closed = this.entries.length - open;
      const item = new vscode.TreeItem(
        node.group === "open" ? `Open (${open})` : `Wontfix (${closed})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      item.contextValue = "ccaGroup";
      return item;
    }

    const entry = node.entry;
    const item = new vscode.TreeItem(
      entry.heading.replace(/^##\s*/, ""),
      vscode.TreeItemCollapsibleState.None,
    );
    item.description = entry.body.find((line: string) => line.startsWith("- "))?.slice(2) ?? "";
    item.tooltip = new vscode.MarkdownString([entry.heading, ...entry.body].join("\n"));
    item.contextValue = entry.wontfix ? "ccaWontfix" : "ccaOpen";
    item.iconPath = new vscode.ThemeIcon(entry.wontfix ? "check" : "warning");
    item.command = {
      command: "cca.openFinding",
      title: "Open finding",
      arguments: [entry.startLine],
    };
    return item;
  }
}

/** Always re-parse the file immediately before editing it. The operator may
 *  have hand-edited CF.md since the tree was built, and a stale line number
 *  would delete the wrong entry. */
async function editEntry(
  file: string,
  heading: string,
  change: (edit: vscode.WorkspaceEdit, uri: vscode.Uri, entry: CfEntry) => void,
): Promise<void> {
  const uri = vscode.Uri.file(file);
  const document = await vscode.workspace.openTextDocument(uri);
  const entry = parseCf(document.getText()).find((candidate) => candidate.heading === heading);
  if (!entry) {
    void vscode.window.showWarningMessage("That CF.md entry is no longer there.");
    return;
  }
  const edit = new vscode.WorkspaceEdit();
  change(edit, uri, entry);
  if (await vscode.workspace.applyEdit(edit)) await document.save();
}

export function registerFeedbackTree(
  context: vscode.ExtensionContext,
  root: string,
  status: StatusBar,
): void {
  const provider = new FeedbackProvider(root, status);
  const view = vscode.window.createTreeView("ccaFeedback", { treeDataProvider: provider });

  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(root), "CF.md"),
  );
  watcher.onDidChange(() => provider.reload());
  watcher.onDidCreate(() => provider.reload());
  watcher.onDidDelete(() => provider.reload());

  context.subscriptions.push(
    view,
    watcher,
    vscode.commands.registerCommand("cca.openFinding", async (line: number): Promise<void> => {
      const document = await vscode.workspace.openTextDocument(vscode.Uri.file(provider.file));
      const editor = await vscode.window.showTextDocument(document);
      const position = new vscode.Position(Math.max(0, line), 0);
      editor.selection = new vscode.Selection(position, position);
      editor.revealRange(new vscode.Range(position, position));
    }),
    vscode.commands.registerCommand("cca.overrule", async (node: Node) => {
      if (isGroup(node)) return;
      const heading = node.entry.heading;
      const confirmed = await vscode.window.showWarningMessage(
        "Delete this finding from CF.md?",
        { modal: true, detail: heading },
        "Overrule",
      );
      if (confirmed !== "Overrule") return;
      await editEntry(provider.file, heading, (edit, uri, entry) => {
        edit.delete(uri, new vscode.Range(entry.startLine, 0, entry.endLine, 0));
      });
    }),
    vscode.commands.registerCommand("cca.wontfix", async (node: Node) => {
      if (isGroup(node)) return;
      const reason = await vscode.window.showInputBox({
        prompt: "Why is this not being fixed?",
        placeHolder: "e.g. deliberate duplication, the two paths diverge next sprint",
      });
      if (reason === undefined) return;
      const heading = node.entry.heading;
      await editEntry(provider.file, heading, (edit, uri, entry) => {
        edit.replace(
          uri,
          new vscode.Range(entry.startLine, 0, entry.startLine, entry.heading.length),
          wontfixHeading(entry.heading, reason),
        );
      });
    }),
  );
}
