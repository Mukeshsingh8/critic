import * as vscode from "vscode";

/** One phase, one session. The plugin does not yet enforce that (it is the
 *  next sub-project); this makes the right thing the easy thing. */
export function offerNextPhase(root: string, shipped: number): void {
  void vscode.window
    .showInformationMessage(`Phase ${shipped} shipped.`, "Start next phase in a new session")
    .then((choice) => {
      if (!choice) return;
      const terminal = vscode.window.createTerminal({
        name: `CCA phase ${shipped + 1}`,
        cwd: root,
      });
      terminal.show();
      terminal.sendText("claude");
    });
}

/** Best-effort immediacy. The Stop gate and the prompt hook guarantee delivery;
 *  this only shortens the wait when a terminal is already running Claude. The
 *  terminal is revealed first, and the line is left unsent, so nothing is typed
 *  into a window the operator cannot see or did not ask for. */
export function nudgeTerminal(count: number): void {
  const terminal = vscode.window.terminals.find((t) => /claude/i.test(t.name));
  if (!terminal) return;
  terminal.show(true);
  terminal.sendText(
    `Apply the ${count} critic fix${count === 1 ? "" : "es"} I accepted on the CCA dashboard.`,
    false,
  );
}
