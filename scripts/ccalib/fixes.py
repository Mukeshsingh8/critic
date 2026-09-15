"""The fix queue -- remedies the operator accepted, on their way to the coder.

A critic's `suggestion` is the actionable half of a finding. Accepting one puts
it here; the Stop gate then refuses to finish while any remain, so a fix the
operator asked for cannot be quietly skipped. Nothing here ever edits code --
it carries an instruction, and the coder does the work.
"""
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

TODO = "todo"
SENT = "sent"
DONE = "done"
DISMISSED = "dismissed"

OPEN_STATES = (TODO, SENT)
MAX_FIXES = 200


def _path(root):
    # type: (str) -> str
    return os.path.join(root, ".cca", "fixes.json")


def fix_key(finding):
    # type: (Dict[str, Any]) -> str
    """The same identity as `fix_id`, but readable. The board matches on this
    so it never has to reimplement the hash to know a fix's state."""
    return "%s|%s|%s" % (finding.get("file", ""), finding.get("line", ""),
                         finding.get("suggestion", ""))


def fix_id(finding):
    # type: (Dict[str, Any]) -> str
    """Stable across repeats: accepting the same remedy twice is one item."""
    return hashlib.sha1(fix_key(finding).encode("utf-8")).hexdigest()[:12]


def load(root):
    # type: (str) -> List[Dict[str, Any]]
    try:
        with open(_path(root)) as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        return []
    items = data.get("fixes") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def save(root, items):
    # type: (str, List[Dict[str, Any]]) -> None
    path = _path(root)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".tmp-%d-fixes.tmp" % os.getpid())
    with open(tmp, "w") as fh:
        json.dump({"fixes": items[-MAX_FIXES:]}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def accept(root, finding):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """Queue a remedy. Re-accepting a done or dismissed one reopens it."""
    items = load(root)
    ident = fix_id(finding)
    for item in items:
        if item.get("id") == ident:
            if item.get("status") in (DONE, DISMISSED):
                item["status"] = TODO
                item["accepted_at"] = time.time()
                item["sent_at"] = None
                item["done_at"] = None
                save(root, items)
            return item

    item = {
        "id": ident,
        "key": fix_key(finding),
        "file": str(finding.get("file", "")),
        "line": finding.get("line", 0),
        "severity": str(finding.get("severity", "minor")),
        "kind": str(finding.get("kind", "")),
        "critic": str(finding.get("critic", "")),
        "issue": str(finding.get("issue", "")),
        "suggestion": str(finding.get("suggestion", "")),
        "evidence": str(finding.get("evidence", "")),
        "status": TODO,
        "accepted_at": time.time(),
        "sent_at": None,
        "done_at": None,
    }
    items.append(item)
    save(root, items)
    return item


def skip(root, finding):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """The arbiter's "this one is not worth fixing".

    Recorded rather than discarded, and as DISMISSED rather than DONE, so the
    board can show it was judged and the log never claims work that nobody did.
    """
    items = load(root)
    ident = fix_id(finding)
    for item in items:
        if item.get("id") == ident:
            item["status"] = DISMISSED
            item["done_at"] = time.time()
            save(root, items)
            return item
    item = accept(root, finding)
    return dismiss_item(root, item["id"])


def dismiss_item(root, ident):
    # type: (str, str) -> Dict[str, Any]
    items = load(root)
    for item in items:
        if item.get("id") == ident:
            item["status"] = DISMISSED
            item["done_at"] = time.time()
            save(root, items)
            return item
    return {}


def open_fixes(root):
    # type: (str) -> List[Dict[str, Any]]
    return [item for item in load(root) if item.get("status") in OPEN_STATES]


def todo_fixes(root):
    # type: (str) -> List[Dict[str, Any]]
    return [item for item in load(root) if item.get("status") == TODO]


def _set_status(root, predicate, status, stamp):
    # type: (str, Any, str, str) -> int
    items = load(root)
    changed = 0
    for item in items:
        if item.get("status") in OPEN_STATES and predicate(item):
            item["status"] = status
            item[stamp] = time.time()
            changed += 1
    if changed:
        save(root, items)
    return changed


def mark_sent(root, ids):
    # type: (str, List[str]) -> int
    wanted = set(ids)
    return _set_status(root, lambda item: item.get("id") in wanted, SENT, "sent_at")


def resolve_file(root, path):
    # type: (str, str) -> int
    """A later edit to this file passed review, so its fixes are done.

    The operator's own acceptance is what put them here; a clean verdict on the
    same file is the strongest available evidence they were carried out."""
    return _set_status(root, lambda item: item.get("file") == path, DONE, "done_at")


def dismiss(root, ident):
    # type: (str, str) -> int
    return _set_status(root, lambda item: item.get("id") == ident, DISMISSED, "done_at")


def directive(items):
    # type: (List[Dict[str, Any]]) -> str
    """The instruction handed to the coder. Imperative, specific, and carrying
    the evidence, so it can be acted on without re-reading the log."""
    if not items:
        return ""
    lines = [
        "The operator accepted %d critic fix(es) on the dashboard and is waiting "
        "for them. Implement each one now, then continue:" % len(items),
        "",
    ]
    for index, item in enumerate(items, 1):
        lines.append("%d. %s" % (index, item.get("suggestion") or "(no suggestion recorded)"))
        lines.append("   file: %s:%s" % (item.get("file", "?"), item.get("line", "?")))
        if item.get("issue"):
            lines.append("   because: %s" % item["issue"])
        if item.get("evidence"):
            lines.append("   evidence: %s" % item["evidence"])
        lines.append("")
    lines.append("Do not ask whether to proceed -- these were explicitly accepted.")
    return "\n".join(lines)
