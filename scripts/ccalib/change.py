"""Summarise what the coder actually did.

The event log recorded that a file changed but never what changed, so the
board could show a verdict without ever showing the thing being judged. This
produces a bounded summary -- small enough to sit in an append-only log that
is written on every single edit, detailed enough to recognise the change.
"""
import difflib
from typing import Any, Dict, List

MAX_LINES = 16
MAX_CHARS = 160


def _clip(text):
    # type: (str) -> str
    line = text.replace("\t", "    ").rstrip("\n")
    return line if len(line) <= MAX_CHARS else line[:MAX_CHARS - 1] + "…"


def _new_content(tool_name, tool_input):
    # type: (str, Dict[str, Any]) -> str
    if tool_name == "NotebookEdit":
        return str(tool_input.get("new_source", ""))
    return str(tool_input.get("content", ""))


def summarise(tool_name, tool_input):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """{added, removed, excerpt, truncated}.

    `excerpt` is a list of {sign, text}: "+" added, "-" removed, " " context.
    """
    if tool_name == "Edit":
        before = str(tool_input.get("old_string", "")).splitlines()
        after = str(tool_input.get("new_string", "")).splitlines()
    else:
        before = []
        after = _new_content(tool_name, tool_input).splitlines()

    rows = []  # type: List[Dict[str, str]]
    added = 0
    removed = 0
    for line in difflib.unified_diff(before, after, n=1, lineterm=""):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            rows.append({"sign": "@", "text": line})
            continue
        sign = line[0] if line[:1] in ("+", "-") else " "
        if sign == "+":
            added += 1
        elif sign == "-":
            removed += 1
        rows.append({"sign": sign, "text": _clip(line[1:] if line[:1] in ("+", "-", " ") else line)})

    truncated = len(rows) > MAX_LINES
    return {
        "added": added,
        "removed": removed,
        "excerpt": rows[:MAX_LINES],
        "truncated": truncated,
    }
