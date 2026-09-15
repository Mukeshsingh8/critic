"""Resolve the current phase and classify a path against phase scope.

Current phase is the lowest-numbered task with no `[phase-N]` commit. Nothing
is stored; the answer is recomputed on every hook invocation, so it survives
any session boundary and cannot drift from git.
"""
import os
from typing import Any, Dict, List, Optional

from . import planparse

PLANS_DIR = os.path.join("docs", "superpowers", "plans")


def active_plan_path(root, cfg):
    # type: (str, Dict[str, Any]) -> Optional[str]
    pinned = (cfg.get("phases") or {}).get("plan") or ""
    if pinned:
        candidate = pinned if os.path.isabs(pinned) else os.path.join(root, pinned)
        return candidate if os.path.exists(candidate) else None

    directory = os.path.join(root, PLANS_DIR)
    if not os.path.isdir(directory):
        return None
    entries = []  # type: List[Any]
    for name in os.listdir(directory):
        if not name.endswith(".md"):
            continue
        full = os.path.join(directory, name)
        try:
            entries.append((os.path.getmtime(full), full))
        except OSError:
            continue
    if not entries:
        return None
    entries.sort()
    return entries[-1][1]


def load(root, cfg):
    # type: (str, Dict[str, Any]) -> List[Dict[str, Any]]
    path = active_plan_path(root, cfg)
    if not path:
        return []
    try:
        with open(path) as fh:
            return planparse.parse(fh.read())
    except (IOError, OSError):
        return []


def current(phase_list, shipped, cfg):
    # type: (List[Dict[str, Any]], Dict[int, str], Dict[str, Any]) -> Optional[Dict[str, Any]]
    done = set(shipped or {})
    done.update((cfg.get("phases") or {}).get("done") or [])
    for phase in sorted(phase_list, key=lambda p: p["number"]):
        if phase["number"] not in done:
            return phase
    return None


def _norm(path):
    # type: (str) -> str
    return os.path.normpath(path).replace(os.sep, "/").lstrip("./")


def _owns(phase, path):
    # type: (Dict[str, Any], str) -> bool
    target = _norm(path)
    for candidate in phase.get("files", []):
        normalised = _norm(candidate)
        if normalised == target or target.endswith("/" + normalised):
            return True
    return False


def classify(path, phase_list, current_phase):
    # type: (str, List[Dict[str, Any]], Optional[Dict[str, Any]]) -> str
    if not current_phase:
        return "unlisted"
    if _owns(current_phase, path):
        return "current"
    for phase in phase_list:
        if not _owns(phase, path):
            continue
        if phase["number"] > current_phase["number"]:
            return "later"
        return "earlier"
    return "unlisted"
