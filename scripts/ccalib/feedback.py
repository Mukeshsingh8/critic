"""CF.md -- the queue and the arbitration surface.

Delete a heading to overrule a critic. Mark it `## [wontfix] ...` to close it
with a justification the arbiter can see later.
"""
import datetime
import os
from typing import Any, Dict, List

# NOTE: this header must never contain a literal "## " sequence. Entry headings
# are the only thing in this file that may start with one, so that tooling (and
# the arbiter's own grep/sed) can target entries unambiguously.
HEADER = (
    "# Critic Feedback (CF.md)\n\n"
    "Open items block the session from finishing. To arbitrate: fix the item and\n"
    "delete its heading, or insert a [wontfix] tag into the heading with a reason.\n"
)


def _path(root):
    # type: (str) -> str
    return os.path.join(root, "CF.md")


def append(findings, file_path, root):
    # type: (List[Dict[str, Any]], str, str) -> None
    if not findings:
        return
    path = _path(root)
    exists = os.path.exists(path)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a") as fh:
        if not exists:
            fh.write(HEADER)
        fh.write("\n## %s -- %s\n" % (stamp, file_path))
        for f in findings:
            fh.write("- **%s / %s** `%s:%s` -- %s\n" % (
                f.get("severity", "minor"), f.get("kind", ""),
                f.get("file", ""), f.get("line", ""), f.get("issue", "")))
            if f.get("evidence"):
                fh.write("  - evidence: %s\n" % f["evidence"])
            if f.get("suggestion"):
                fh.write("  - suggestion: %s\n" % f["suggestion"])


def open_items(root):
    # type: (str) -> List[str]
    path = _path(root)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except IOError:
        return []
    return [ln for ln in lines if ln.startswith("## ") and "[wontfix]" not in ln.lower()]
