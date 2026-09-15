"""The IDE bridge -- ask the human, or behave exactly as if no one is there.

The governing rule, and the reason this module is small: the bridge can only
relax a gate when a human ACTS. Silence, absence, a malformed answer and any
exception all mean "as if the extension did not exist".
"""
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

from . import events

# Mirrors hooks/hooks.json. A wait is clamped to what is left of the hook's
# own budget, so a misconfigured `block_wait` can never run past Claude Code's
# timeout and lose a decision the human already made.
HOOK_BUDGETS = {"block": 600, "commit": 900}  # type: Dict[str, int]
SAFETY_MARGIN = 10
POLL_INTERVAL = 0.25
SWEEP_GRACE = 60

_UNSAFE = re.compile(r"[^A-Za-z0-9]")


def paths(root):
    # type: (str) -> Dict[str, str]
    base = os.path.join(root, ".cca")
    return {"beat": os.path.join(base, "arbiter.json"),
            "pending": os.path.join(base, "pending"),
            "decisions": os.path.join(base, "decisions")}


def new_id(session_id):
    # type: (str) -> str
    """`<epoch_ms>-<session>-<rand>`. The session segment is scrubbed to
    alphanumerics: this string becomes a filename."""
    tag = _UNSAFE.sub("", str(session_id))[:8] or "nosess"
    return "%d-%s-%04x" % (int(time.time() * 1000), tag, random.randrange(0x10000))


def write_json(path, data):
    # type: (str, Dict[str, Any]) -> None
    """tmp-then-rename in the same directory, so a reader never sees a
    half-written file and never has to retry."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # The suffix must NOT be `.json`: sweep() enumerates *.json and would
    # treat a half-written temp file as a corrupt request.
    tmp = os.path.join(directory, ".tmp-%d-%s.tmp" % (os.getpid(), os.path.basename(path)))
    with open(tmp, "w") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def read_json(path):
    # type: (str) -> Optional[Dict[str, Any]]
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def settings(cfg):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return cfg.get("arbiter") or {}


def _number(value, fallback):
    # type: (Any, float) -> float
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def present(root, cfg):
    # type: (str, Dict[str, Any]) -> bool
    conf = settings(cfg)
    if not conf.get("enabled", True):
        return False
    heartbeat = read_json(paths(root)["beat"])
    if not heartbeat:
        return False
    age = time.time() - _number(heartbeat.get("ts"), 0.0)
    # A negative age is clock skew, not freshness.
    return 0 <= age < _number(conf.get("stale_after"), 15.0)


def _listdir(directory):
    # type: (str) -> List[str]
    try:
        return [n for n in os.listdir(directory) if n.endswith(".json")]
    except OSError:
        return []


def _unlink(path):
    # type: (str) -> None
    try:
        os.remove(path)
    except OSError:
        pass


def sweep(root, now=None):
    # type: (str, Optional[float]) -> None
    """Delete what a crash left behind: expired requests, and answers to
    requests nobody is waiting for. Called at the start of every request so
    leftovers can never accumulate."""
    moment = time.time() if now is None else now
    where = paths(root)

    live = set()
    for name in _listdir(where["pending"]):
        path = os.path.join(where["pending"], name)
        data = read_json(path)
        if data is None or moment > _number(data.get("deadline"), 0.0) + SWEEP_GRACE:
            _unlink(path)
        else:
            live.add(name)

    for name in _listdir(where["decisions"]):
        if name in live:
            continue
        path = os.path.join(where["decisions"], name)
        try:
            age = moment - os.path.getmtime(path)
        except OSError:
            age = SWEEP_GRACE + 1
        if age > SWEEP_GRACE:
            _unlink(path)


def wait_for(cfg, kind, elapsed):
    # type: (Dict[str, Any], str, float) -> float
    """How long this hook may hold Claude, in seconds.

    Two limits: what the operator configured, and what is left of the hook's
    own budget. The second is not negotiable -- exceeding it means Claude Code
    kills the hook and the human's answer is thrown away."""
    conf = settings(cfg)
    default = 180.0 if kind == "block" else 300.0
    want = _number(conf.get("%s_wait" % kind), default)
    if want <= 0:
        want = default
    budget = HOOK_BUDGETS.get(kind, 600) - elapsed - SAFETY_MARGIN
    return max(0.0, min(want, budget))


def _record(root, kind, ident, decision, waited, was_present, timed_out):
    # type: (str, str, str, Optional[Dict[str, Any]], float, bool, bool) -> None
    answer = decision or {}
    events.append("arbitration", {
        "id": ident,
        "kind": kind,
        "choice": answer.get("choice", ""),
        "note": answer.get("note", ""),
        "ok": answer.get("ok"),
        "waited": round(waited, 2),
        "timed_out": timed_out,
        "present": was_present,
    }, root)


def request(root, cfg, kind, payload, elapsed=0.0):
    # type: (str, Dict[str, Any], str, Dict[str, Any], float) -> Optional[Dict[str, Any]]
    """Ask the human. `None` means "nobody answered -- act as you would today".

    Every failure mode collapses into that same `None`, deliberately: a bridge
    that can fail in interesting ways is a bridge that can weaken the gate."""
    try:
        return _request(root, cfg, kind, payload, elapsed)
    except Exception:
        return None


def _request(root, cfg, kind, payload, elapsed):
    # type: (str, Dict[str, Any], str, Dict[str, Any], float) -> Optional[Dict[str, Any]]
    sweep(root)

    was_present = present(root, cfg)
    if not was_present:
        _record(root, kind, "", None, 0.0, False, False)
        return None

    # A budget already spent is not a timeout -- nobody was ever asked.
    wait = wait_for(cfg, kind, elapsed)
    if wait <= 0:
        _record(root, kind, "", None, 0.0, was_present, False)
        return None

    options = payload.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("a request must offer at least one option")

    ident = new_id(payload.get("session_id", ""))
    where = paths(root)
    pending_path = os.path.join(where["pending"], "%s.json" % ident)
    decision_path = os.path.join(where["decisions"], "%s.json" % ident)

    started = time.time()
    body = dict(payload)
    body.update({"id": ident, "kind": kind,
                 "created": started, "deadline": started + wait})
    write_json(pending_path, body)
    # file and deadline ride along so a board reading only the event log can
    # tell that a request is live, and for how much longer.
    events.append("arbiter_pending",
                  {"id": ident, "kind": kind, "wait": round(wait, 1),
                   "file": payload.get("file", ""),
                   "options": list(options),
                   "deadline": started + wait}, root)

    decision = None  # type: Optional[Dict[str, Any]]
    while True:
        answer = read_json(decision_path)
        if answer is not None and str(answer.get("choice", "")) in options:
            decision = dict(answer)
            decision["choice"] = str(answer["choice"])
            break
        if time.time() >= started + wait:
            break
        time.sleep(POLL_INTERVAL)

    _unlink(pending_path)
    _unlink(decision_path)
    _record(root, kind, ident, decision, time.time() - started, True, decision is None)
    return decision
