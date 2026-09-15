"""Append-only event log. The dashboard reads it; the tuning analysis reads it."""
import json
import os
import time
from typing import Any, Dict, FrozenSet, List

EVENT_TYPES = frozenset([
    "edit_started", "layer0_result", "critic_dispatched", "critic_verdict",
    "vote", "queued", "blocked", "drain_blocked", "session_end",
    "phase_context", "phase_fenced", "phase_gate",
    "arbiter_pending", "arbitration",
    "fix_accepted", "fix_dispatched", "fix_resolved", "fix_skipped",
])  # type: FrozenSet[str]


def _log_path(root):
    # type: (str) -> str
    return os.path.join(root, ".cca", "events.jsonl")


def append(event_type, payload, root):
    # type: (str, Dict[str, Any], str) -> None
    if event_type not in EVENT_TYPES:
        raise ValueError("unknown event type: %s" % event_type)
    path = _log_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {"type": event_type, "ts": time.time(), "payload": payload}
    with open(path, "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_all(root):
    # type: (str) -> List[Dict[str, Any]]
    path = _log_path(root)
    if not os.path.exists(path):
        return []
    out = []  # type: List[Dict[str, Any]]
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a corrupt line must never break the dashboard
    return out
