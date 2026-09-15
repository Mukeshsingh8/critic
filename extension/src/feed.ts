import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { EventLogTail, type CcaEvent, type FileSlice } from "./eventlog";

const HISTORY_CAP = 5000;

class DiskSlice implements FileSlice {
  constructor(private readonly file: string) {}

  async size(): Promise<number> {
    try {
      return (await fs.promises.stat(this.file)).size;
    } catch {
      return 0; // not created yet
    }
  }

  async read(from: number, to: number): Promise<Uint8Array> {
    const length = Math.max(0, to - from);
    if (length === 0) return new Uint8Array();
    const handle = await fs.promises.open(this.file, "r");
    try {
      const buffer = Buffer.alloc(length);
      const { bytesRead } = await handle.read(buffer, 0, length, from);
      return buffer.subarray(0, bytesRead);
    } finally {
      await handle.close();
    }
  }
}

/** Tails `.cca/events.jsonl` and fans new events out to the dashboard and the
 *  status bar. One reader for the whole extension. */
export class EventFeed implements vscode.Disposable {
  private readonly tail = new EventLogTail();
  private readonly slice: DiskSlice;
  private readonly watcher: vscode.FileSystemWatcher;
  private readonly emitter = new vscode.EventEmitter<readonly CcaEvent[]>();
  private readonly seen: CcaEvent[] = [];
  private polling = false;

  readonly onEvents = this.emitter.event;

  constructor(root: string) {
    this.slice = new DiskSlice(path.join(root, ".cca", "events.jsonl"));
    this.watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(vscode.Uri.file(path.join(root, ".cca")), "events.jsonl"),
    );
    this.watcher.onDidChange(() => void this.poll());
    this.watcher.onDidCreate(() => void this.poll());
    this.watcher.onDidDelete(() => {
      this.tail.reset();
      this.seen.length = 0;
    });
    void this.poll();
  }

  get history(): readonly CcaEvent[] {
    return this.seen;
  }

  async poll(): Promise<void> {
    if (this.polling) return; // a burst of writes must not interleave reads
    this.polling = true;
    try {
      const events = await this.tail.poll(this.slice);
      if (events.length === 0) return;
      this.seen.push(...events);
      if (this.seen.length > HISTORY_CAP) this.seen.splice(0, this.seen.length - HISTORY_CAP);
      this.emitter.fire(events);
    } catch {
      /* a mid-write read can fail; the next change event retries */
    } finally {
      this.polling = false;
    }
  }

  dispose(): void {
    this.watcher.dispose();
    this.emitter.dispose();
  }
}
