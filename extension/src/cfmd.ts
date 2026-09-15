// CF.md is the arbiter's surface. `feedback.py` guarantees that entry headings
// are the ONLY lines starting with "## " -- this module depends on exactly
// that one invariant and reads nothing else structurally.

export interface CfEntry {
  readonly heading: string;
  readonly startLine: number; // 0-based index of the "## " line
  readonly endLine: number; // exclusive
  readonly wontfix: boolean;
  readonly body: readonly string[];
}

const HEADING = "## ";
const WONTFIX = "[wontfix]";

export function parseCf(text: string): CfEntry[] {
  const lines = text.split("\n");
  const starts: number[] = [];
  lines.forEach((line, index) => {
    if (line.startsWith(HEADING)) starts.push(index);
  });

  return starts.map((start, position) => {
    const end = position + 1 < starts.length ? starts[position + 1] : lines.length;
    const heading = lines[start];
    return {
      heading,
      startLine: start,
      endLine: end,
      wontfix: heading.toLowerCase().includes(WONTFIX),
      body: lines.slice(start + 1, end),
    };
  });
}

/** The heading `open_items()` will stop counting. Appending keeps the original
 *  timestamp and file readable, which matters when reading the log back later. */
export function wontfixHeading(heading: string, reason: string): string {
  if (heading.toLowerCase().includes(WONTFIX)) return heading;
  const clean = reason.replace(/\s+/g, " ").trim();
  return `${heading.trimEnd()} ${WONTFIX}${clean ? ` ${clean}` : ""}`;
}
