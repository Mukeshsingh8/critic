"""Build the change payload a critic reviews.

Critics see intent, not just the diff -- the task spec and the project's own
conventions travel with the change, which is what catches "the code does
exactly what the coder said, and what the coder said was wrong".
"""
import os
from typing import Any, Dict

MAX_SECTION = 20000


def _read(root, name):
    # type: (str, str) -> str
    path = os.path.join(root, name)
    if not os.path.exists(path):
        return ""
    try:
        with open(path) as fh:
            return fh.read()[:MAX_SECTION]
    except IOError:
        return ""


def build(tool_name, tool_input, root):
    # type: (str, Dict[str, Any], str) -> str
    path = tool_input.get("file_path", "(unknown)")
    if tool_name == "Edit":
        change = "FILE: %s\n--- BEFORE ---\n%s\n--- AFTER ---\n%s" % (
            path,
            str(tool_input.get("old_string", ""))[:MAX_SECTION],
            str(tool_input.get("new_string", ""))[:MAX_SECTION],
        )
    else:
        change = "FILE: %s\n--- NEW CONTENT ---\n%s" % (
            path, str(tool_input.get("content", ""))[:MAX_SECTION])

    spec = _read(root, "PROMPT.md") or "(no task spec file)"
    conventions = _read(root, "CLAUDE.md") or "(no project conventions file)"

    return (
        "TASK SPEC:\n%s\n\n"
        "PROJECT CONVENTIONS (enforce these, not generic best practices):\n%s\n\n"
        "CHANGE UNDER REVIEW:\n%s\n" % (spec, conventions, change)
    )
