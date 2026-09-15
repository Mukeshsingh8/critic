import * as fs from "node:fs";
import * as path from "node:path";

/** tmp-then-rename, matching `arbiter.write_json`. The temp name must NOT end
 *  in `.json`: the Python sweep enumerates `*.json` and would treat a
 *  half-written temp file as a corrupt request. */
export function writeJsonAtomic(target: string, data: unknown): void {
  const dir = path.dirname(target);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = path.join(dir, `.tmp-${process.pid}-${path.basename(target)}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(data), "utf8");
  fs.renameSync(tmp, target);
}
