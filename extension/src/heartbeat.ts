import * as fs from "node:fs";
import * as path from "node:path";
import { writeJsonAtomic } from "./fsatomic";

const INTERVAL_MS = 5000;

/** Presence, and nothing else. The hooks read this file to decide whether a
 *  human is reachable; if it stops being written they stop waiting within
 *  `arbiter.stale_after` seconds. */
export class Heartbeat {
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(private readonly root: string, private readonly version: string) {}

  start(): void {
    this.beat();
    this.timer = setInterval(() => this.beat(), INTERVAL_MS);
  }

  private beat(): void {
    try {
      writeJsonAtomic(path.join(this.root, ".cca", "arbiter.json"), {
        pid: process.pid,
        ts: Date.now() / 1000,
        host: "vscode",
        version: this.version,
      });
    } catch {
      // A read-only or vanished workspace must not take the extension down.
      // A missing heartbeat simply means the hooks stop asking.
    }
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    try {
      fs.rmSync(path.join(this.root, ".cca", "arbiter.json"), { force: true });
    } catch {
      /* already gone */
    }
  }
}
