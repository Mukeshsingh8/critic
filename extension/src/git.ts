import { execFile } from "node:child_process";

export interface GitResult {
  readonly ok: boolean;
  readonly error: string;
}

/** `execFile`, never a shell: a branch name or a commit message with a quote
 *  in it must not become part of a command line. */
export function runGit(root: string, args: readonly string[]): Promise<GitResult> {
  return new Promise((resolve) => {
    execFile("git", [...args], { cwd: root, timeout: 60_000 }, (error, _stdout, stderr) => {
      if (error) {
        resolve({ ok: false, error: (stderr || error.message).trim() });
      } else {
        resolve({ ok: true, error: "" });
      }
    });
  });
}
