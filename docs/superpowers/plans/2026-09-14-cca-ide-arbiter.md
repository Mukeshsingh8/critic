# CCA IDE Arbiter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the arbiter's seat into the editor — a VS Code extension that renders the critic loop live and holds Claude while the human answers the two decisions that are theirs to make: a critic block, and the phase commit.

**Architecture:** A file mailbox under `.cca/`. The extension writes a heartbeat; the hooks write a request and poll for a decision; the extension renders buttons and writes the answer. The hooks stay the lock — the extension is a view with buttons. Absence, silence, malformed input and any exception all fall back to exactly today's behaviour.

**Tech Stack:** Python 3.9 stdlib only (existing `ccalib` package, `unittest`). TypeScript 5 + esbuild + vitest for the extension; no UI framework; VS Code API ^1.85 with no proprietary APIs, so one `.vsix` serves Marketplace and Open VSX (Cursor, Windsurf, Antigravity).

**Spec:** [`docs/superpowers/specs/2026-09-14-cca-ide-arbiter-design.md`](../specs/2026-09-14-cca-ide-arbiter-design.md)

## Global Constraints

- **Python 3.9.6, stdlib only.** `unittest`, no pytest, no PEP 604 unions, no `match`, no f-strings with `=`. Type comments (`# type: (...) -> ...`), never annotations — match the existing `ccalib` style exactly.
- **The bridge can only relax a gate when a human acts, never by inaction.** Absent heartbeat, stale heartbeat, timeout, malformed decision, unknown `choice`, any exception → the hook produces exactly the output it produces today. There is no path where installing the extension makes the plugin weaker.
- **Claude never runs `git add`, `git commit`, `git push` or `git init` against a real project.** The extension runs git as the *human's click*. Python tests create throwaway repos in `tempfile.mkdtemp()` only. No task ends in a commit to this repository.
- **`CCA_INNER=1` guard stays first** in every hook entrypoint, before any other work.
- **Zero model calls in the test suites.** Python uses a fake `claude` binary on `PATH`; TypeScript uses vitest against pure modules with no editor.
- **TypeScript: never `any`.** `strict: true`. The VS Code API is imported only in the adapter files named in Task 9 onward; every module under vitest runs without an editor.
- **Atomic writes everywhere in `.cca/`:** write to a temp file in the same directory, then `os.replace` / `fs.renameSync`. A reader must never see a partial file.
- **Hook budgets are shipped constants:** `PostToolUse` 600s, `Stop` 900s, mirrored in `arbiter.HOOK_BUDGETS`. If `hooks/hooks.json` changes, that constant changes in the same commit.
- **Event log is append-only and schema-checked.** A new event type must be added to `events.EVENT_TYPES` or `append()` raises.
- **Test-count baseline: 260 Python tests, green, measured at plan time.** The
  repository was being edited while this plan was written, so treat the DELTA in
  each task as the contract, not the absolute. If `python3 -m unittest discover
  tests` reports something other than 260 before Task 1, adjust every later
  expectation by the same offset and say so.
- **Run the full suite, not a subset,** before claiming a task passes: `python3 -m unittest discover tests` (221 tests green today) and, from Task 6 on, `npm --prefix extension test`.

## File Structure

**Python — the bridge (all under `scripts/`):**

| File | Responsibility |
|---|---|
| `ccalib/arbiter.py` *(new)* | The entire mailbox: id minting, atomic write, presence, sweep, request/poll loop. The only module that knows the protocol. |
| `ccalib/config.py` *(modify)* | One-level-deep merge so a partial `arbiter` or `phases` block keeps its sibling defaults; `arbiter` defaults. |
| `ccalib/events.py` *(modify)* | Two new event types. |
| `ccalib/gate.py` *(modify)* | `skip_tests` parameter so a post-commit re-evaluation does not re-run a 600s test command. |
| `critic_hook.py` *(modify)* | On a block vote, ask; branch on uphold / queue / overrule. |
| `drain_hook.py` *(modify)* | Commit command into the `phase_gate` payload; on rung `commit`, ask; on a successful commit, re-evaluate. |
| `hooks/hooks.json` *(modify)* | Timeouts raised to fit a human in the loop. |

**TypeScript — the extension (all under `extension/`):**

| File | Responsibility |
|---|---|
| `src/protocol.ts` | Request/decision shapes, parsing, validation, expiry. Pure. |
| `src/eventlog.ts` | Incremental tail of `events.jsonl` by byte offset; truncation and partial-line handling. Pure (injected reader). |
| `src/cfmd.ts` | Parse `CF.md` entries; compute the line ranges for Overrule and the replacement heading for Wontfix. Pure. |
| `src/fsatomic.ts` | tmp-then-rename JSON write. Thin, node `fs` only. |
| `src/extension.ts` | Activation, workspace root resolution, wiring, disposables. |
| `src/heartbeat.ts` | `arbiter.json` every 5s; removed on deactivate. |
| `src/decisions.ts` | Watches `pending/`; modal per request; runs git for a commit; writes the decision. |
| `src/dashboard.ts` | Webview panel; forwards tailed events; pending strip. |
| `src/cfTree.ts` | `TreeDataProvider` over `CF.md` with Overrule / Wontfix actions via `WorkspaceEdit`. |
| `src/statusBar.ts` | `CCA · phase 3/11 · 2 open · ⏳`. |
| `src/nextSession.ts` | Terminal running `claude` after a phase ships. |
| `media/dashboard.js` | Today's `dashboard/index.html` script with `poll()` replaced by a message listener. |

Files that change together live together: the protocol lives in exactly two places (`arbiter.py`, `protocol.ts`) and nowhere else. The extension never parses a plan, never re-derives a phase, and never re-implements a gate rung — it renders what the request carries.

---

### Task 1: Config deep merge and the new event types

The groundwork every later Python task needs. Today `config.load` does `cfg.update(user)`, so a user writing `{"arbiter": {"block_wait": 60}}` silently deletes `enabled`, `commit_wait` and `stale_after`. The same latent bug already applies to `phases`.

**Files:**
- Modify: `scripts/ccalib/config.py:8-44`
- Modify: `scripts/ccalib/events.py:7-11`
- Test: `tests/test_config.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `config.DEFAULTS["arbiter"] == {"enabled": True, "block_wait": 180, "commit_wait": 300, "stale_after": 15}`
  - `config.load(root) -> Dict[str, Any]` — unchanged signature, now merging nested dicts one level deep
  - `events.EVENT_TYPES` additionally contains `"arbiter_pending"` and `"arbitration"`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_config.py`, inside the existing `TestLoad` class:

```python
    def test_partial_nested_block_keeps_sibling_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 60}}, fh)
        arbiter = config.load(self.tmp)["arbiter"]
        self.assertEqual(arbiter["block_wait"], 60)
        self.assertEqual(arbiter["stale_after"], 15)
        self.assertEqual(arbiter["commit_wait"], 300)
        self.assertTrue(arbiter["enabled"])

    def test_partial_phases_block_keeps_sibling_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"test_command": "make test"}}, fh)
        phases = config.load(self.tmp)["phases"]
        self.assertEqual(phases["test_command"], "make test")
        self.assertTrue(phases["enabled"])
        self.assertEqual(phases["done"], [])

    def test_lists_are_replaced_wholesale_not_merged(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"critics": [{"name": "solo", "model": "claude-haiku-4-5",
                                    "brief": "correctness", "timeout": 30}]}, fh)
        self.assertEqual(len(config.load(self.tmp)["critics"]), 1)

    def test_arbiter_defaults(self):
        arbiter = config.load(self.tmp)["arbiter"]
        self.assertTrue(arbiter["enabled"])
        self.assertEqual(arbiter["block_wait"], 180)
        self.assertEqual(arbiter["commit_wait"], 300)
        self.assertEqual(arbiter["stale_after"], 15)

    def test_defaults_are_not_mutated_between_loads(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 1}}, fh)
        config.load(self.tmp)
        self.assertEqual(config.load(tempfile.mkdtemp())["arbiter"]["block_wait"], 180)
```

Append to `tests/test_events.py`, inside `TestEvents`:

```python
    def test_arbiter_event_types_are_accepted(self):
        events.append("arbiter_pending", {"id": "x", "kind": "block"}, root=self.tmp)
        events.append("arbitration", {"id": "x", "choice": "uphold"}, root=self.tmp)
        kinds = [e["type"] for e in events.read_all(self.tmp)]
        self.assertEqual(kinds, ["arbiter_pending", "arbitration"])
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config tests.test_events -v`
Expected: FAIL — `KeyError: 'arbiter'` on the defaults tests, `ValueError: unknown event type: arbiter_pending` on the events test.

- [x] **Step 3: Add the arbiter defaults and the deep merge**

In `scripts/ccalib/config.py`, add to `DEFAULTS` after the `"phases"` block:

```python
    "arbiter": {
        "enabled": True,
        "block_wait": 180,      # seconds to hold a blocked edit for the human
        "commit_wait": 300,     # seconds to hold the phase gate for the human
        "stale_after": 15,      # heartbeat older than this means "no arbiter"
    },
```

Replace the merge in `load()` — the `if isinstance(user, dict): cfg.update(user)` line — with a call to a new helper, and add the helper above `load`:

```python
def _merge(base, user):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """Merge one level deep. A user who sets `{"arbiter": {"block_wait": 60}}`
    means "change that one knob", not "delete the other three". Lists are
    replaced wholesale -- the critic roster is a complete statement, not a
    patch."""
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base
```

and in `load()`:

```python
    if isinstance(user, dict):
        _merge(cfg, user)
    return cfg
```

- [x] **Step 4: Add the event types**

In `scripts/ccalib/events.py`, extend `EVENT_TYPES`:

```python
EVENT_TYPES = frozenset([
    "edit_started", "layer0_result", "critic_dispatched", "critic_verdict",
    "vote", "queued", "blocked", "drain_blocked", "session_end",
    "phase_context", "phase_fenced", "phase_gate",
    "arbiter_pending", "arbitration",
])  # type: FrozenSet[str]
```

- [x] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: OK, 266 tests (260 + 6).

---

### Task 2: Arbiter primitives — id, atomic write, presence, sweep

The mailbox's mechanical parts, with no waiting yet. Everything here is synchronous and trivially testable.

**Files:**
- Create: `scripts/ccalib/arbiter.py`
- Test: `tests/test_arbiter.py`

**Interfaces:**
- Consumes: `config.load` (Task 1) for the `arbiter` block; `events.append`
- Produces:
  - `HOOK_BUDGETS = {"block": 600, "commit": 900}`, `SAFETY_MARGIN = 10`, `POLL_INTERVAL = 0.25`, `SWEEP_GRACE = 60`
  - `paths(root) -> Dict[str, str]` with keys `beat`, `pending`, `decisions`
  - `new_id(session_id: str) -> str`
  - `write_json(path: str, data: Dict[str, Any]) -> None`
  - `read_json(path: str) -> Optional[Dict[str, Any]]`
  - `present(root: str, cfg: Dict[str, Any]) -> bool`
  - `sweep(root: str, now: Optional[float] = None) -> None`

- [x] **Step 1: Write the failing test**

```python
# tests/test_arbiter.py
import json, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter, config


def beat(root, age=0.0, **extra):
    """Write a heartbeat file aged `age` seconds into the past."""
    os.makedirs(os.path.join(root, ".cca"), exist_ok=True)
    data = {"pid": os.getpid(), "ts": time.time() - age, "host": "test", "version": "0.1.0"}
    data.update(extra)
    with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
        json.dump(data, fh)


class TestPrimitives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)

    def test_id_is_unique_and_filename_safe(self):
        made = {arbiter.new_id("sess/../../etc") for _ in range(50)}
        self.assertEqual(len(made), 50)
        for ident in made:
            self.assertNotIn("/", ident)
            self.assertNotIn("..", ident)

    def test_id_survives_an_empty_session(self):
        self.assertTrue(arbiter.new_id(""))

    def test_write_json_is_atomic_and_leaves_no_temp_file(self):
        target = os.path.join(self.tmp, ".cca", "pending", "a.json")
        arbiter.write_json(target, {"hello": "world"})
        with open(target) as fh:
            self.assertEqual(json.load(fh)["hello"], "world")
        self.assertEqual(os.listdir(os.path.dirname(target)), ["a.json"])

    def test_read_json_returns_none_for_missing_corrupt_and_non_object(self):
        missing = os.path.join(self.tmp, "nope.json")
        self.assertIsNone(arbiter.read_json(missing))
        corrupt = os.path.join(self.tmp, "bad.json")
        with open(corrupt, "w") as fh:
            fh.write("{ not json")
        self.assertIsNone(arbiter.read_json(corrupt))
        listy = os.path.join(self.tmp, "list.json")
        with open(listy, "w") as fh:
            fh.write("[1, 2]")
        self.assertIsNone(arbiter.read_json(listy))


class TestPresent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)

    def test_absent_without_a_heartbeat(self):
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_present_with_a_fresh_heartbeat(self):
        beat(self.tmp)
        self.assertTrue(arbiter.present(self.tmp, self.cfg))

    def test_absent_with_a_stale_heartbeat(self):
        beat(self.tmp, age=60)
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_disabled_in_config(self):
        beat(self.tmp)
        self.cfg["arbiter"]["enabled"] = False
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_heartbeat_is_corrupt(self):
        os.makedirs(os.path.join(self.tmp, ".cca"), exist_ok=True)
        with open(os.path.join(self.tmp, ".cca", "arbiter.json"), "w") as fh:
            fh.write("{ not json")
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_timestamp_is_not_a_number(self):
        beat(self.tmp, ts="soon")
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_heartbeat_is_from_the_future(self):
        """A clock skew that reads as -3600s must not read as 'fresh'."""
        beat(self.tmp, age=-3600)
        self.assertFalse(arbiter.present(self.tmp, self.cfg))


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _pending(self, name, deadline):
        arbiter.write_json(os.path.join(self.tmp, ".cca", "pending", name),
                           {"id": name[:-5], "deadline": deadline})

    def _decision(self, name):
        arbiter.write_json(os.path.join(self.tmp, ".cca", "decisions", name),
                           {"id": name[:-5], "choice": "uphold"})

    def test_expired_pending_is_removed(self):
        self._pending("old.json", deadline=time.time() - 3600)
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_live_pending_survives(self):
        self._pending("live.json", deadline=time.time() + 3600)
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), ["live.json"])

    def test_corrupt_pending_is_removed(self):
        os.makedirs(os.path.join(self.tmp, ".cca", "pending"), exist_ok=True)
        with open(os.path.join(self.tmp, ".cca", "pending", "bad.json"), "w") as fh:
            fh.write("{ not json")
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_orphan_decision_is_removed_once_stale(self):
        self._decision("orphan.json")
        arbiter.sweep(self.tmp, now=time.time() + 3600)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), [])

    def test_a_decision_for_a_live_pending_is_kept(self):
        self._pending("live.json", deadline=time.time() + 3600)
        self._decision("live.json")
        arbiter.sweep(self.tmp, now=time.time() + 3600)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), ["live.json"])

    def test_a_fresh_orphan_decision_is_kept(self):
        """A decision written microseconds before its pending file appears must
        not be swept out from under the hook."""
        self._decision("racing.json")
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), ["racing.json"])

    def test_sweep_on_a_project_with_no_cca_dir_is_silent(self):
        arbiter.sweep(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_arbiter -v`
Expected: FAIL — `ImportError: cannot import name 'arbiter'`.

- [x] **Step 3: Write the module**

```python
# scripts/ccalib/arbiter.py
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
```

- [x] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_arbiter -v`
Expected: PASS, 18 tests.

- [x] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: OK, 284 tests (260 + 24).

---

### Task 3: `arbiter.request()` — the wait loop

The heart of the bridge. Writes a request, polls for an answer, cleans up, records what happened, and returns `None` for every flavour of "no human said anything".

**Files:**
- Modify: `scripts/ccalib/arbiter.py` (append to the module from Task 2)
- Test: `tests/test_arbiter.py` (append)

**Interfaces:**
- Consumes: everything from Task 2
- Produces:
  - `wait_for(cfg: Dict[str, Any], kind: str, elapsed: float) -> float`
  - `request(root: str, cfg: Dict[str, Any], kind: str, payload: Dict[str, Any], elapsed: float = 0.0) -> Optional[Dict[str, Any]]`
  - The returned dict is the decision file's contents with a validated `choice` key. `None` means "act as today".
  - Emits `arbiter_pending` `{id, kind, wait}` and `arbitration` `{id, kind, choice, note, ok, waited, timed_out, present}`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_arbiter.py`:

```python
import threading


def answer_after(root, delay, choice, **extra):
    """A fake arbiter: wait for the request to appear, then answer it.
    Returns the thread so a test can join it."""
    def run():
        deadline = time.time() + 10
        pending_dir = os.path.join(root, ".cca", "pending")
        ident = None
        while time.time() < deadline and ident is None:
            names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
            names = [n for n in names if n.endswith(".json")]
            if names:
                ident = names[0]
            else:
                time.sleep(0.01)
        if ident is None:
            return
        time.sleep(delay)
        data = {"id": ident[:-5], "choice": choice, "ts": time.time()}
        data.update(extra)
        arbiter.write_json(os.path.join(root, ".cca", "decisions", ident), data)

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
    return thread


BLOCK = {"options": ["uphold", "queue", "overrule"], "file": "src/a.ts",
         "reason": "critics rejected", "findings": [], "session_id": "abc123"}


class TestWaitFor(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_uses_the_configured_wait_when_the_budget_allows(self):
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=0.0), 180.0)
        self.assertEqual(arbiter.wait_for(self.cfg, "commit", elapsed=0.0), 300.0)

    def test_clamps_to_what_is_left_of_the_hook_budget(self):
        # block budget 600, 500s already spent, 10s margin -> 90s left
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=500.0), 90.0)

    def test_never_returns_a_negative_wait(self):
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=10000.0), 0.0)

    def test_a_non_numeric_wait_falls_back_to_the_default(self):
        self.cfg["arbiter"]["block_wait"] = "soon"
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=0.0), 180.0)


class TestRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 3

    def _events(self, kind):
        from ccalib import events as ev
        return [e["payload"] for e in ev.read_all(self.tmp) if e["type"] == kind]

    def test_no_heartbeat_means_no_request_and_no_wait(self):
        started = time.time()
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertLess(time.time() - started, 1.0)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))
        self.assertFalse(self._events("arbitration")[0]["present"])

    def test_stale_heartbeat_means_no_request(self):
        beat(self.tmp, age=60)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertFalse(self._events("arbitration")[0]["present"])

    def test_disabled_means_no_request(self):
        beat(self.tmp)
        self.cfg["arbiter"]["enabled"] = False
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))

    def test_a_decision_is_returned_and_both_files_are_removed(self):
        beat(self.tmp)
        answer_after(self.tmp, 0.1, "overrule", note="test-only helper")
        decision = arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(decision["choice"], "overrule")
        self.assertEqual(decision["note"], "test-only helper")
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), [])

    def test_the_request_carries_the_payload_and_a_deadline(self):
        beat(self.tmp)
        seen = {}

        def capture():
            pending_dir = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 5
            while time.time() < deadline:
                names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
                if names:
                    seen.update(arbiter.read_json(os.path.join(pending_dir, names[0])) or {})
                    arbiter.write_json(os.path.join(self.tmp, ".cca", "decisions", names[0]),
                                       {"id": seen.get("id"), "choice": "uphold"})
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=capture)
        thread.daemon = True
        thread.start()
        arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        thread.join(timeout=5)
        self.assertEqual(seen["kind"], "block")
        self.assertEqual(seen["file"], "src/a.ts")
        self.assertEqual(seen["options"], ["uphold", "queue", "overrule"])
        self.assertGreater(seen["deadline"], seen["created"])

    def test_timeout_returns_none_and_cleans_up(self):
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 0.5
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])
        record = self._events("arbitration")[0]
        self.assertTrue(record["timed_out"])
        self.assertTrue(record["present"])

    def test_a_choice_outside_options_is_ignored(self):
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 1
        answer_after(self.tmp, 0.05, "delete-the-repo")
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))

    def test_a_malformed_decision_does_not_end_the_wait(self):
        """Garbage must be ignored, and a good answer arriving afterwards
        must still be honoured."""
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 4

        def scribble_then_answer():
            pending_dir = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 5
            while time.time() < deadline:
                names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
                if names:
                    target = os.path.join(self.tmp, ".cca", "decisions", names[0])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "w") as fh:
                        fh.write("{ not json")
                    time.sleep(0.5)
                    arbiter.write_json(target, {"id": names[0][:-5], "choice": "queue"})
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=scribble_then_answer)
        thread.daemon = True
        thread.start()
        decision = arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(decision["choice"], "queue")

    def test_leftovers_from_a_crash_are_swept_before_asking(self):
        beat(self.tmp)
        arbiter.write_json(os.path.join(self.tmp, ".cca", "pending", "ancient.json"),
                           {"id": "ancient", "deadline": time.time() - 3600})
        self.cfg["arbiter"]["block_wait"] = 0.5
        arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_zero_budget_skips_the_request_entirely(self):
        beat(self.tmp)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK),
                                          elapsed=10000.0))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))

    def test_an_internal_error_returns_none_rather_than_raising(self):
        beat(self.tmp)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", {"options": "not a list"}))

    def test_commit_decisions_carry_ok_and_error_through(self):
        beat(self.tmp)
        self.cfg["arbiter"]["commit_wait"] = 3
        payload = {"options": ["commit", "edit", "later"], "session_id": "s"}
        answer_after(self.tmp, 0.1, "commit", ok=False, error="nothing to commit")
        decision = arbiter.request(self.tmp, self.cfg, "commit", payload)
        self.assertEqual(decision["choice"], "commit")
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["error"], "nothing to commit")
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_arbiter -v`
Expected: FAIL — `AttributeError: module 'ccalib.arbiter' has no attribute 'wait_for'`.

- [x] **Step 3: Append the wait loop to `scripts/ccalib/arbiter.py`**

```python
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
    events.append("arbiter_pending",
                  {"id": ident, "kind": kind, "wait": round(wait, 1)}, root)

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
```

- [x] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_arbiter -v`
Expected: PASS, 34 tests in tests/test_arbiter.py. It takes ~6s: the timeout tests really wait.

- [x] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: OK, 300 tests (260 + 40).

---

### Task 4: `critic_hook` — live arbitration on a block

**Files:**
- Modify: `scripts/critic_hook.py:14-85`
- Test: `tests/test_hooks.py` (append a class)

**Interfaces:**
- Consumes: `arbiter.request` (Task 3)
- Produces: no new symbols. Behaviour: on a `block` vote with an arbiter present, the hook asks with `options: ["uphold", "queue", "overrule"]` and a payload of `{file, reason, findings, session_id, options}`. `uphold` / `None` → today's block. `queue` → `CF.md` + `additionalContext`. `overrule` → silence.

- [x] **Step 1: Write the failing test**

Append to `tests/test_hooks.py`:

```python
import threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter as arbiter_mod

FAIL_JSON = ('{"verdict":"fail","findings":[{"severity":"major",'
             '"kind":"correctness","file":"src/a.ts","line":1,'
             '"issue":"Null deref on empty list","evidence":"","suggestion":"guard"}]}')


class TestCriticArbitration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 5}}, fh)

    def _beat(self, age=0.0):
        with open(os.path.join(self.tmp, ".cca", "arbiter.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "ts": time.time() - age, "host": "test"}, fh)

    def _env(self):
        return {"PATH": fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + FAIL_JSON + "'")
                        + os.pathsep + os.environ["PATH"]}

    def _event(self):
        return {"tool_name": "Edit", "cwd": self.tmp, "session_id": "sess0001",
                "tool_input": {"file_path": "src/a.ts", "old_string": "a", "new_string": "b"}}

    def _answer(self, choice, **extra):
        def run():
            pending = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 20
            while time.time() < deadline:
                names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
                names = [n for n in names if n.endswith(".json")]
                if names:
                    data = {"id": names[0][:-5], "choice": choice}
                    data.update(extra)
                    arbiter_mod.write_json(
                        os.path.join(self.tmp, ".cca", "decisions", names[0]), data)
                    return
                time.sleep(0.01)
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return thread

    def test_no_arbiter_blocks_exactly_as_today(self):
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_uphold_blocks(self):
        self._beat()
        self._answer("uphold")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_overrule_lets_the_edit_stand_silently(self):
        self._beat()
        self._answer("overrule", note="test-only helper")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CF.md")))

    def test_overrule_records_the_note(self):
        self._beat()
        self._answer("overrule", note="test-only helper")
        run(CRITIC, self._event(), self.tmp, self._env())
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            records = [json.loads(ln) for ln in fh if ln.strip()]
        notes = [r["payload"]["note"] for r in records if r["type"] == "arbitration"]
        self.assertIn("test-only helper", notes)

    def test_queue_writes_cf_md_and_does_not_block(self):
        self._beat()
        self._answer("queue")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        # "non-blocking" contains "block", so check the decision, not the text
        self.assertNotIn("decision", json.loads(out))
        self.assertIn("hookSpecificOutput", json.loads(out))
        with open(os.path.join(self.tmp, "CF.md")) as fh:
            self.assertIn("Null deref", fh.read())

    def test_stale_heartbeat_blocks_without_waiting(self):
        self._beat(age=120)
        started = time.time()
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertLess(time.time() - started, 20)

    def test_arbiter_disabled_blocks_without_asking(self):
        self._beat()
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"enabled": False}}, fh)
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))

    def test_a_passing_review_never_asks_the_human(self):
        self._beat()
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        _code, out, _err = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_hooks.TestCriticArbitration -v`
Expected: FAIL — `test_overrule_lets_the_edit_stand_silently` gets a block; the queue and note tests fail too. The two "no arbiter" tests already pass.

- [x] **Step 3: Wire the hook**

In `scripts/critic_hook.py`, add `import time` at the top, then change the import line to include `arbiter`:

```python
    from ccalib import arbiter, config, events, feedback, payload, runner, vote
```

Record the start immediately after the imports inside `main()`:

```python
    started = time.time()
```

Replace the whole `if decision["decision"] == "block": ... elif ... == "queue": ...` tail with:

```python
    action = decision["decision"]
    if action == "block":
        answer = arbiter.request(root, cfg, "block", {
            "file": rel,
            "reason": decision["reason"],
            "findings": decision["findings"],
            "session_id": event.get("session_id", ""),
            "options": ["uphold", "queue", "overrule"],
        }, elapsed=time.time() - started)
        # No answer means no arbiter, or an arbiter who said nothing. Either
        # way the critics' own decision stands -- the bridge only ever relaxes
        # a gate when a human actually acts.
        action = (answer or {}).get("choice", "uphold")
        if action == "uphold":
            action = "block"

    if action == "block":
        events.append("blocked", {"file": rel}, root)
        print(json.dumps({
            "decision": "block",
            "reason": "Critics rejected the change to %s:\n%s" % (rel, decision["reason"]),
        }))
    elif action == "queue":
        feedback.append(decision["findings"], rel, root)
        events.append("queued", {"file": rel, "count": len(decision["findings"])}, root)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Critics queued %d non-blocking finding(s) on %s in CF.md."
                                 % (len(decision["findings"]), rel),
        }}))
    return 0
```

Note what this does *not* change: a `queue` vote still reaches the same `elif` branch it reaches today, and a `pass` vote still falls through silently without the human ever being asked.

- [x] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_hooks -v`
Expected: PASS, all classes.

- [x] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: OK, 308 tests (260 + 48).

---

### Task 5: `drain_hook` — live arbitration on the phase commit

The gate's commit rung becomes a question. Two supporting changes come with it: `gate.evaluate` learns to skip the test rung (re-running a 600s test command after the commit would blow the hook budget), and the `phase_gate` event starts carrying the commit command so the extension never has to parse a plan.

**Files:**
- Modify: `scripts/ccalib/gate.py:81-100`
- Modify: `scripts/drain_hook.py:44-90`
- Modify: `hooks/hooks.json`
- Test: `tests/test_gate.py` (append)
- Test: `tests/test_gate_hooks.py` (append a class)

**Interfaces:**
- Consumes: `arbiter.request` (Task 3)
- Produces:
  - `gate.evaluate(root, cfg, phase, shipped, dirty=None, phase_list=None, skip_tests=False)` — new trailing keyword only; every existing call is unaffected
  - `phase_gate` event payload gains `command` (display string) and `message` (`[phase-N] Title`)
  - The `commit` request payload: `{phase: {number, title, total, files}, plan_rel, command, message, checks, session_id, options: ["commit", "edit", "later"]}`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
class TestSkipTests(unittest.TestCase):
    def test_skip_tests_does_not_run_the_command(self):
        import tempfile as tf
        counter = os.path.join(tf.mkdtemp(), "runs")
        root = tf.mkdtemp()
        cfg = {"phases": {"test_command":
                          "python3 -c \"open(r'%s','a').write('x')\"" % counter}}
        phase = {"number": 1, "title": "One", "files": ["a.py"],
                 "start_line": 0, "end_line": 1}
        gate.evaluate(root, cfg, phase, {}, dirty=[], skip_tests=True)
        self.assertFalse(os.path.exists(counter))

    def test_without_the_flag_the_command_runs(self):
        import tempfile as tf
        counter = os.path.join(tf.mkdtemp(), "runs")
        root = tf.mkdtemp()
        cfg = {"phases": {"test_command":
                          "python3 -c \"open(r'%s','a').write('x')\"" % counter}}
        phase = {"number": 1, "title": "One", "files": ["a.py"],
                 "start_line": 0, "end_line": 1}
        gate.evaluate(root, cfg, phase, {}, dirty=[])
        self.assertTrue(os.path.exists(counter))
```

Append to `tests/test_gate_hooks.py`:

```python
import threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter as arbiter_mod


def beat(root, age=0.0):
    os.makedirs(os.path.join(root, ".cca"), exist_ok=True)
    with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
        json.dump({"pid": os.getpid(), "ts": time.time() - age, "host": "test"}, fh)


def enable_arbiter(root, **overrides):
    path = os.path.join(root, ".cca", "config.json")
    with open(path) as fh:
        cfg = json.load(fh)
    arbiter_cfg = {"commit_wait": 20}
    arbiter_cfg.update(overrides)
    cfg["arbiter"] = arbiter_cfg
    with open(path, "w") as fh:
        json.dump(cfg, fh)


def answer_commit(root, choice="commit", do_git=True, ok=True, error=""):
    """Stand in for the extension: read the request, optionally really run git,
    then write the decision."""
    def run():
        pending = os.path.join(root, ".cca", "pending")
        deadline = time.time() + 30
        while time.time() < deadline:
            names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
            names = [n for n in names if n.endswith(".json")]
            if names:
                request = arbiter_mod.read_json(os.path.join(pending, names[0])) or {}
                committed = ok
                if do_git and ok:
                    files = [f for f in request.get("phase", {}).get("files", [])
                             if os.path.exists(os.path.join(root, f))]
                    if request.get("plan_rel"):
                        files.append(request["plan_rel"])
                    try:
                        git(root, "add", "--", *files)
                        git(root, "commit", "-m", request.get("message", "x"))
                    except subprocess.CalledProcessError:
                        committed = False
                arbiter_mod.write_json(
                    os.path.join(root, ".cca", "decisions", names[0]),
                    {"id": request.get("id"), "choice": choice,
                     "ok": committed, "error": error})
                return
            time.sleep(0.01)
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
    return thread


def events_of(root, kind):
    path = os.path.join(root, ".cca", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(ln)["payload"] for ln in fh
                if ln.strip() and json.loads(ln)["type"] == kind]


class TestCommitArbitration(unittest.TestCase):
    def _started(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        return root

    def test_no_arbiter_blocks_with_the_command_as_today(self):
        root = self._started()
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("git add", json.loads(out)["reason"])

    def test_later_blocks_with_the_command(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root, choice="later", do_git=False)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("git add", json.loads(out)["reason"])
        self.assertIn("[phase-1]", json.loads(out)["reason"])

    def test_commit_creates_the_commit_and_allows_the_stop(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")
        log = subprocess.check_output(["git", "log", "--format=%s"], cwd=root,
                                      universal_newlines=True)
        self.assertIn("[phase-1] One", log)

    def test_the_committed_plan_carries_the_ticked_checkbox(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        show = subprocess.check_output(
            ["git", "show", "HEAD:docs/superpowers/plans/p.md"], cwd=root,
            universal_newlines=True)
        self.assertIn("- [x] **Step 1: x**", show)

    def test_a_failed_commit_blocks_with_the_git_error(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root, do_git=False, ok=False, error="nothing to commit, working tree clean")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        reason = json.loads(out)["reason"]
        self.assertIn("nothing to commit", reason)
        self.assertIn("git add", reason)

    # NOTE: the spec's "commit ok, later-phase file dirty" row has no test here
    # because it cannot happen. A later-phase file dirty BEFORE the commit stops
    # the ladder at the tree rung, so the human is never asked; nothing can make
    # a file appear between the commit and the re-evaluation. What IS reachable
    # is leftover unlisted work, which is what this test covers.
    def test_leftover_work_after_the_commit_blocks_with_the_next_phase(self):
        root = self._started()
        with open(os.path.join(root, "stray.py"), "w") as fh:
            fh.write("belongs to no phase")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        reason = json.loads(out)["reason"]
        self.assertIn("Phase 2", reason)
        self.assertIn("[phase-2]", reason)

    def test_the_human_is_asked_at_most_once_per_stop(self):
        root = self._started()
        with open(os.path.join(root, "stray.py"), "w") as fh:
            fh.write("belongs to no phase")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(len(events_of(root, "arbiter_pending")), 1)

    def test_the_phase_gate_event_carries_the_command(self):
        root = self._started()
        _code, _out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        payloads = events_of(root, "phase_gate")
        self.assertIn("git add", payloads[-1]["command"])
        self.assertEqual(payloads[-1]["message"], "[phase-1] One")

    def test_tests_are_not_rerun_after_the_commit(self):
        counter = os.path.join(tempfile.mkdtemp(), "runs")
        root = project(test_command="python3 -c \"open(r'%s','a').write('x')\"" % counter)
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        with open(counter) as fh:
            self.assertEqual(fh.read(), "x")

    def test_failing_tests_never_reach_the_human(self):
        root = project(test_command="python3 -c 'import sys; sys.exit(1)'")
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        enable_arbiter(root)
        beat(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("tests are failing", json.loads(out)["reason"])
        self.assertEqual(events_of(root, "arbiter_pending"), [])

    def test_open_cf_items_never_reach_the_human(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.py\n- finding\n")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("CF.md", json.loads(out)["reason"])
        self.assertEqual(events_of(root, "arbiter_pending"), [])
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_gate.TestSkipTests tests.test_gate_hooks.TestCommitArbitration -v`
Expected: FAIL — `TypeError: evaluate() got an unexpected keyword argument 'skip_tests'`, and the arbitration tests block with the command instead of committing.

- [x] **Step 3: Teach `gate.evaluate` to skip the test rung**

In `scripts/ccalib/gate.py`, change the signature and the first rung:

```python
def evaluate(root, cfg, phase, shipped, dirty=None, phase_list=None, skip_tests=False):
    # type: (str, Dict[str, Any], Dict[str, Any], Dict[int, str], Optional[List[str]], Optional[List[Dict[str, Any]]], bool) -> Dict[str, Any]
    checks = {"tests": None, "critics": None, "commit": None, "tree": None}

    # Rung 1 -- tests. Skipped only when they have already passed in this same
    # Stop: re-running a 600s command after the commit would run the hook past
    # its budget and throw away the decision the human just made.
    outcome = None if skip_tests else _run_tests(
        root, (cfg.get("phases") or {}).get("test_command", ""))
```

- [x] **Step 4: Rewrite `_phase_gate` in `scripts/drain_hook.py`**

Add `import time` at the top. Change the call site in `main()` from `return _phase_gate(root)` to `return _phase_gate(root, event, started)`, and add `started = time.time()` as the first line of `main()` after the `CCA_INNER` guard. Replace `_phase_gate` entirely with:

```python
def _commit_reason(phase, total, command):
    """The text the operator sees when the commit is theirs to run."""
    return ("Phase %s/%s -- %s is ready but has not shipped.\n\n"
            "Commit it before moving on. The plan file is included so the ticked "
            "steps ride along and the tree ends clean:\n\n  %s\n"
            % (phase["number"], total, phase["title"], command))


def _blocked(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _phase_gate(root, event, started):
    """Rungs 1, 3 and 4 of the ladder. Rung 2 (CF.md) fires before this."""
    from ccalib import arbiter, config, events, gate, gitstate, phases

    cfg = config.load(root)
    if not (cfg.get("phases") or {}).get("enabled", True):
        return 0

    phase_list = phases.load(root, cfg)
    if not phase_list:
        return 0

    shipped = gitstate.phase_commits(root)
    if shipped is None:
        return 0

    current = phases.current(phase_list, shipped, cfg)
    if not current:
        return 0

    plan_path = phases.active_plan_path(root, cfg) or ""
    plan_rel = os.path.relpath(plan_path, root) if plan_path else ""
    command = gate.commit_command(current, plan_rel)
    message = "[phase-%s] %s" % (current["number"], current["title"])

    result = gate.evaluate(root, cfg, current, shipped, phase_list=phase_list)
    events.append("phase_gate", {"phase": current["number"], "ok": result["ok"],
                                 "rung": result["rung"], "checks": result["checks"],
                                 "command": command, "message": message}, root)
    if result["ok"]:
        return 0

    if result["rung"] != "commit":
        return _blocked("Phase %s/%s -- %s cannot ship yet.\n\n%s" % (
            current["number"], len(phase_list), current["title"], result["reason"]))

    # Ticking before the question, not after, so the plan file is already
    # staged-worthy when the human clicks Commit.
    gate.tick_steps(plan_path, current)

    answer = arbiter.request(root, cfg, "commit", {
        "phase": {"number": current["number"], "title": current["title"],
                  "total": len(phase_list), "files": list(current["files"])},
        "plan_rel": plan_rel,
        "command": command,
        "message": message,
        "checks": result["checks"],
        "session_id": event.get("session_id", ""),
        "options": ["commit", "edit", "later"],
    }, elapsed=time.time() - started)

    choice = (answer or {}).get("choice", "later")
    if choice not in ("commit", "edit"):
        return _blocked(_commit_reason(current, len(phase_list), command))

    if not (answer or {}).get("ok"):
        return _blocked(
            "The phase-%s commit did not go through:\n\n%s\n\nRun it yourself when "
            "you have fixed the cause:\n\n  %s\n"
            % (current["number"], (answer or {}).get("error") or "git gave no error text",
               command))

    return _after_commit(root, cfg, phase_list)


def _after_commit(root, cfg, phase_list):
    """The human committed. Re-derive from git and check what is left -- but
    never ask a second time in one Stop."""
    from ccalib import events, gate, gitstate, phases

    shipped = gitstate.phase_commits(root)
    if shipped is None:
        return 0

    current = phases.current(phase_list, shipped, cfg)
    if not current:
        return 0  # every phase has shipped

    result = gate.evaluate(root, cfg, current, shipped,
                           phase_list=phase_list, skip_tests=True)
    events.append("phase_gate", {"phase": current["number"], "ok": result["ok"],
                                 "rung": result["rung"], "checks": result["checks"],
                                 "command": "", "message": ""}, root)
    if result["ok"]:
        return 0

    if result["rung"] == "commit":
        plan_path = phases.active_plan_path(root, cfg) or ""
        plan_rel = os.path.relpath(plan_path, root) if plan_path else ""
        return _blocked(_commit_reason(current, len(phase_list),
                                       gate.commit_command(current, plan_rel)))

    return _blocked("Phase %s/%s -- %s cannot ship yet.\n\n%s" % (
        current["number"], len(phase_list), current["title"], result["reason"]))
```

- [x] **Step 5: Raise the hook timeouts**

In `hooks/hooks.json`, change the `PostToolUse` hook's `"timeout": 300` to `"timeout": 600` and the `Stop` hook's `"timeout": 600` to `"timeout": 900`. These two numbers are the ones mirrored in `arbiter.HOOK_BUDGETS`; if one moves, both move.

- [x] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_gate tests.test_gate_hooks -v`
Expected: PASS.

- [x] **Step 7: Verify the constants agree with the shipped hooks**

Run:
```bash
python3 - <<'CHECK'
import json, sys
sys.path.insert(0, "scripts")
from ccalib import arbiter
hooks = json.load(open("hooks/hooks.json"))["hooks"]
got = {"block": hooks["PostToolUse"][0]["hooks"][0]["timeout"],
       "commit": hooks["Stop"][0]["hooks"][0]["timeout"]}
assert got == arbiter.HOOK_BUDGETS, (got, arbiter.HOOK_BUDGETS)
print("budgets agree:", got)
CHECK
```
Expected: `budgets agree: {'block': 600, 'commit': 900}`

- [x] **Step 8: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: OK, 321 tests (260 + 61). The Python half of the bridge is complete and works with no extension present.

---

### Task 6: Extension scaffold and the protocol module

The TypeScript half starts with the one thing that must agree with Python byte for byte: the mailbox format.

**Files:**
- Create: `extension/package.json`
- Create: `extension/tsconfig.json`
- Create: `extension/.vscodeignore`
- Create: `extension/src/protocol.ts`
- Create: `extension/src/fsatomic.ts`
- Create: `extension/test/protocol.test.ts`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the request shape written by `arbiter._request` (Task 3)
- Produces:
  - `type RequestKind = "block" | "commit"`
  - `interface Finding { severity, kind, file, line, issue, evidence, suggestion }`
  - `interface PhaseRef { number, title, total, files }`
  - `interface ArbiterRequest { id, kind, created, deadline, options, sessionId, file?, reason?, findings?, phase?, planRel?, command?, message? }`
  - `interface Decision { id, choice, note?, ok?, error?, ts }`
  - `parseRequest(text: string): ArbiterRequest | null`
  - `isExpired(request: ArbiterRequest, now: number): boolean`
  - `buildDecision(request: ArbiterRequest, choice: string, extra?: { note?: string; ok?: boolean; error?: string }): Decision | null`
  - `writeJsonAtomic(target: string, data: unknown): void`

- [x] **Step 1: Create the scaffold**

`extension/package.json`:

```json
{
  "name": "cca-arbiter",
  "displayName": "CCA Arbiter",
  "description": "Arbitrate CCA critic blocks and phase commits without leaving the editor",
  "version": "0.1.0",
  "license": "MIT",
  "publisher": "cca",
  "private": true,
  "engines": { "vscode": "^1.85.0" },
  "categories": ["Other"],
  "main": "./dist/extension.js",
  "activationEvents": ["workspaceContains:.cca"],
  "contributes": {
    "commands": [
      { "command": "cca.openDashboard", "title": "CCA: Open Dashboard" },
      { "command": "cca.overrule", "title": "CCA: Overrule this finding", "icon": "$(trash)" },
      { "command": "cca.wontfix", "title": "CCA: Mark wontfix", "icon": "$(check)" },
      { "command": "cca.openFinding", "title": "CCA: Open finding" }
    ],
    "views": {
      "explorer": [{ "id": "ccaFeedback", "name": "CCA Critic Feedback" }]
    },
    "menus": {
      "view/item/context": [
        { "command": "cca.overrule", "when": "view == ccaFeedback && viewItem == ccaOpen", "group": "inline" },
        { "command": "cca.wontfix", "when": "view == ccaFeedback && viewItem == ccaOpen", "group": "inline" }
      ]
    }
  },
  "scripts": {
    "build": "esbuild src/extension.ts --bundle --platform=node --target=node18 --format=cjs --external:vscode --outfile=dist/extension.js",
    "watch": "npm run build -- --sourcemap --watch",
    "vscode:prepublish": "npm run build -- --minify",
    "typecheck": "tsc --noEmit",
    "test": "vitest run && npm run typecheck",
    "package": "vsce package --no-dependencies"
  },
  "devDependencies": {
    "@types/node": "^22.10.0",
    "@types/vscode": "^1.85.0",
    "@vscode/vsce": "^3.2.0",
    "esbuild": "^0.24.0",
    "typescript": "^5.7.0",
    "vitest": "^2.1.0"
  }
}
```

`extension/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM"],
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noImplicitOverride": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["src/**/*.ts", "test/**/*.ts"]
}
```

`extension/.vscodeignore`:

```
.vscode/**
test/**
src/**
node_modules/**
tsconfig.json
vitest.config.ts
**/*.map
```

Append to the repository's root `.gitignore`:

```
extension/node_modules/
extension/dist/
extension/*.vsix
```

Then install:

```bash
npm --prefix extension install
```

- [x] **Step 2: Write the failing test**

```typescript
// extension/test/protocol.test.ts
import { describe, expect, it } from "vitest";
import { buildDecision, isExpired, parseRequest } from "../src/protocol";

const BLOCK = JSON.stringify({
  id: "1757880000123-sess0001-0a1b",
  kind: "block",
  created: 1757880000.123,
  deadline: 1757880180.123,
  options: ["uphold", "queue", "overrule"],
  session_id: "sess0001",
  file: "src/a.ts",
  reason: "Critics rejected this change",
  findings: [{
    severity: "major", kind: "correctness", file: "src/a.ts", line: 12,
    issue: "Null deref on empty list", evidence: "", suggestion: "guard",
  }],
});

const COMMIT = JSON.stringify({
  id: "1757880000999-sess0001-0c3d",
  kind: "commit",
  created: 1757880000.9,
  deadline: 1757880300.9,
  options: ["commit", "edit", "later"],
  session_id: "sess0001",
  phase: { number: 3, title: "Critic runner", total: 11, files: ["scripts/ccalib/runner.py"] },
  plan_rel: "docs/superpowers/plans/p.md",
  command: "git add scripts/ccalib/runner.py \\\n  && git commit -m \"[phase-3] Critic runner\"",
  message: "[phase-3] Critic runner",
  checks: { tests: true, critics: true, commit: false, tree: true },
});

describe("parseRequest", () => {
  it("reads a block request including its findings", () => {
    const request = parseRequest(BLOCK);
    expect(request?.kind).toBe("block");
    expect(request?.file).toBe("src/a.ts");
    expect(request?.options).toEqual(["uphold", "queue", "overrule"]);
    expect(request?.findings?.[0].issue).toBe("Null deref on empty list");
    expect(request?.sessionId).toBe("sess0001");
  });

  it("reads a commit request including its phase", () => {
    const request = parseRequest(COMMIT);
    expect(request?.kind).toBe("commit");
    expect(request?.phase?.number).toBe(3);
    expect(request?.phase?.total).toBe(11);
    expect(request?.message).toBe("[phase-3] Critic runner");
    expect(request?.planRel).toBe("docs/superpowers/plans/p.md");
  });

  it("rejects malformed input rather than throwing", () => {
    for (const bad of ["", "{ not json", "[]", "null", "42", '{"id":"x"}',
                       '{"id":"x","kind":"nope","options":["a"],"deadline":1}',
                       '{"id":"x","kind":"block","options":[],"deadline":1}',
                       '{"kind":"block","options":["a"],"deadline":1}']) {
      expect(parseRequest(bad)).toBeNull();
    }
  });

  it("drops non-string options and malformed findings", () => {
    const request = parseRequest(JSON.stringify({
      id: "x", kind: "block", created: 1, deadline: 2,
      options: ["uphold", 7, null, "queue"],
      findings: ["nope", { severity: "minor", kind: "reuse", file: "a.ts", issue: "dup" }],
    }));
    expect(request?.options).toEqual(["uphold", "queue"]);
    expect(request?.findings).toHaveLength(1);
    expect(request?.findings?.[0].file).toBe("a.ts");
  });
});

describe("isExpired", () => {
  it("compares the deadline in seconds against a millisecond clock", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    expect(isExpired(request, 1757880100_000)).toBe(false);
    expect(isExpired(request, 1757880300_000)).toBe(true);
  });
});

describe("buildDecision", () => {
  it("accepts a choice the request offered", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    const decision = buildDecision(request, "overrule", { note: "test-only helper" });
    expect(decision?.id).toBe(request.id);
    expect(decision?.choice).toBe("overrule");
    expect(decision?.note).toBe("test-only helper");
    expect(typeof decision?.ts).toBe("number");
  });

  it("refuses a choice the request did not offer", () => {
    const request = parseRequest(BLOCK);
    if (!request) throw new Error("unreachable");
    expect(buildDecision(request, "commit")).toBeNull();
  });

  it("carries ok and error for a commit", () => {
    const request = parseRequest(COMMIT);
    if (!request) throw new Error("unreachable");
    const decision = buildDecision(request, "commit", { ok: false, error: "nothing to commit" });
    expect(decision?.ok).toBe(false);
    expect(decision?.error).toBe("nothing to commit");
  });
});
```

- [x] **Step 3: Run the test to verify it fails**

Run: `npm --prefix extension test`
Expected: FAIL — cannot resolve `../src/protocol`.

- [x] **Step 4: Write the modules**

```typescript
// extension/src/protocol.ts
// The mailbox format. This file and scripts/ccalib/arbiter.py are the only two
// places that know it; everything else consumes these types.

export type RequestKind = "block" | "commit";

export interface Finding {
  readonly severity: string;
  readonly kind: string;
  readonly file: string;
  readonly line: number | string;
  readonly issue: string;
  readonly evidence: string;
  readonly suggestion: string;
}

export interface PhaseRef {
  readonly number: number;
  readonly title: string;
  readonly total: number;
  readonly files: readonly string[];
}

export interface GateChecks {
  readonly tests: boolean | null;
  readonly critics: boolean | null;
  readonly commit: boolean | null;
  readonly tree: boolean | null;
}

export interface ArbiterRequest {
  readonly id: string;
  readonly kind: RequestKind;
  readonly created: number;
  readonly deadline: number;
  readonly options: readonly string[];
  readonly sessionId: string;
  readonly file?: string;
  readonly reason?: string;
  readonly findings?: readonly Finding[];
  readonly phase?: PhaseRef;
  readonly planRel?: string;
  readonly command?: string;
  readonly message?: string;
}

export interface Decision {
  readonly id: string;
  readonly choice: string;
  readonly ts: number;
  readonly note?: string;
  readonly ok?: boolean;
  readonly error?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function asFindings(value: unknown): Finding[] {
  if (!Array.isArray(value)) return [];
  const out: Finding[] = [];
  for (const item of value) {
    const record = asRecord(item);
    if (!record) continue;
    const file = asString(record.file);
    if (!file) continue;
    const line = record.line;
    out.push({
      severity: asString(record.severity, "minor"),
      kind: asString(record.kind),
      file,
      line: typeof line === "number" || typeof line === "string" ? line : 0,
      issue: asString(record.issue),
      evidence: asString(record.evidence),
      suggestion: asString(record.suggestion),
    });
  }
  return out;
}

function asPhase(value: unknown): PhaseRef | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  return {
    number: asNumber(record.number),
    title: asString(record.title),
    total: asNumber(record.total),
    files: asStrings(record.files),
  };
}

export function parseRequest(text: string): ArbiterRequest | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  const record = asRecord(raw);
  if (!record) return null;

  const id = asString(record.id);
  const kind = asString(record.kind);
  const options = asStrings(record.options);
  if (!id || (kind !== "block" && kind !== "commit") || options.length === 0) return null;

  return {
    id,
    kind,
    created: asNumber(record.created),
    deadline: asNumber(record.deadline),
    options,
    sessionId: asString(record.session_id),
    file: asString(record.file) || undefined,
    reason: asString(record.reason) || undefined,
    findings: asFindings(record.findings),
    phase: asPhase(record.phase),
    planRel: asString(record.plan_rel) || undefined,
    command: asString(record.command) || undefined,
    message: asString(record.message) || undefined,
  };
}

/** `deadline` is a POSIX time in SECONDS (Python's `time.time()`); `now` is
 *  milliseconds (`Date.now()`). Getting this wrong expires everything
 *  instantly, so the conversion lives here and nowhere else. */
export function isExpired(request: ArbiterRequest, now: number): boolean {
  return now / 1000 >= request.deadline;
}

export function buildDecision(
  request: ArbiterRequest,
  choice: string,
  extra: { note?: string; ok?: boolean; error?: string } = {},
): Decision | null {
  if (!request.options.includes(choice)) return null;
  const decision: Decision = {
    id: request.id,
    choice,
    ts: Date.now() / 1000,
    ...(extra.note === undefined ? {} : { note: extra.note }),
    ...(extra.ok === undefined ? {} : { ok: extra.ok }),
    ...(extra.error === undefined ? {} : { error: extra.error }),
  };
  return decision;
}
```

```typescript
// extension/src/fsatomic.ts
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
```

- [x] **Step 5: Run the test to verify it passes**

Run: `npm --prefix extension test`
Expected: PASS, 8 tests, then `tsc --noEmit` clean.

---

### Task 7: `eventlog.ts` — incremental tail

**Files:**
- Create: `extension/src/eventlog.ts`
- Create: `extension/test/eventlog.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `interface CcaEvent { type: string; ts: number; payload: Record<string, unknown> }`
  - `interface FileSlice { size(): Promise<number>; read(from: number, to: number): Promise<Uint8Array> }`
  - `class EventLogTail { poll(slice: FileSlice): Promise<CcaEvent[]>; reset(): void }`

- [x] **Step 1: Write the failing test**

```typescript
// extension/test/eventlog.test.ts
import { describe, expect, it } from "vitest";
import { EventLogTail, type FileSlice } from "../src/eventlog";

class FakeLog implements FileSlice {
  private bytes: Uint8Array = new Uint8Array();

  append(text: string): void {
    const extra = Buffer.from(text, "utf8");
    this.bytes = Buffer.concat([Buffer.from(this.bytes), extra]);
  }

  replace(text: string): void {
    this.bytes = Buffer.from(text, "utf8");
  }

  async size(): Promise<number> {
    return this.bytes.byteLength;
  }

  async read(from: number, to: number): Promise<Uint8Array> {
    return this.bytes.slice(from, to);
  }
}

const line = (type: string, payload: Record<string, unknown> = {}): string =>
  `${JSON.stringify({ type, ts: 1.0, payload })}\n`;

describe("EventLogTail", () => {
  it("returns nothing for an empty log", async () => {
    expect(await new EventLogTail().poll(new FakeLog())).toEqual([]);
  });

  it("returns only what is new on each poll", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("edit_started", { file: "a.ts" }));
    expect(await tail.poll(log)).toHaveLength(1);
    expect(await tail.poll(log)).toHaveLength(0);
    log.append(line("vote", { decision: "pass" }));
    const next = await tail.poll(log);
    expect(next).toHaveLength(1);
    expect(next[0].type).toBe("vote");
    expect(next[0].payload.decision).toBe("pass");
  });

  it("holds a partial trailing line until it is complete", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    const whole = line("vote", { decision: "block" });
    log.append(whole.slice(0, 10));
    expect(await tail.poll(log)).toEqual([]);
    log.append(whole.slice(10));
    expect(await tail.poll(log)).toHaveLength(1);
  });

  it("skips a corrupt line without losing the ones around it", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("vote") + "NOT JSON\n" + line("queued"));
    const seen = await tail.poll(log);
    expect(seen.map((event) => event.type)).toEqual(["vote", "queued"]);
  });

  it("skips a line that is valid JSON but not an event", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append('[1,2,3]\n"just a string"\n{"nope":1}\n' + line("vote"));
    expect(await tail.poll(log)).toHaveLength(1);
  });

  it("restarts from the beginning when the log is truncated", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("edit_started") + line("vote"));
    expect(await tail.poll(log)).toHaveLength(2);
    log.replace(line("queued"));
    const seen = await tail.poll(log);
    expect(seen).toHaveLength(1);
    expect(seen[0].type).toBe("queued");
  });

  it("survives a multibyte character split across two reads", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    const whole = line("queued", { file: "src/café.ts" });
    const bytes = Buffer.from(whole, "utf8");
    const split = bytes.indexOf(Buffer.from("é", "utf8")) + 1;
    log.append(bytes.slice(0, split).toString("binary"));
    await tail.poll(log);
    log.replace(whole);
    // replace() is not a truncation here -- the file only grew
    const seen = await tail.poll(log);
    expect(seen).toHaveLength(1);
    expect(seen[0].payload.file).toBe("src/café.ts");
  });

  it("reset() makes the next poll replay everything", async () => {
    const log = new FakeLog();
    const tail = new EventLogTail();
    log.append(line("vote"));
    await tail.poll(log);
    tail.reset();
    expect(await tail.poll(log)).toHaveLength(1);
  });
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `npm --prefix extension test`
Expected: FAIL — cannot resolve `../src/eventlog`.

- [x] **Step 3: Write the module**

```typescript
// extension/src/eventlog.ts

export interface CcaEvent {
  readonly type: string;
  readonly ts: number;
  readonly payload: Record<string, unknown>;
}

/** A file, as this module needs it. Injected so the parsing can be tested
 *  without touching a disk. */
export interface FileSlice {
  size(): Promise<number>;
  read(from: number, to: number): Promise<Uint8Array>;
}

function parseEvent(line: string): CcaEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(trimmed);
  } catch {
    return null; // a corrupt line must never break the feed
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.type !== "string") return null;
  const payload = record.payload;
  return {
    type: record.type,
    ts: typeof record.ts === "number" ? record.ts : 0,
    payload:
      typeof payload === "object" && payload !== null && !Array.isArray(payload)
        ? (payload as Record<string, unknown>)
        : {},
  };
}

export class EventLogTail {
  private offset = 0;
  private partial = "";
  private decoder = new TextDecoder("utf-8");

  async poll(slice: FileSlice): Promise<CcaEvent[]> {
    const size = await slice.size();
    if (size < this.offset) this.reset(); // truncated or replaced
    if (size === this.offset) return [];

    const chunk = await slice.read(this.offset, size);
    this.offset = size;

    // `stream: true` is what makes a multibyte character split across two
    // reads survive: the decoder holds the incomplete sequence for next time.
    const text = this.partial + this.decoder.decode(chunk, { stream: true });
    const lines = text.split("\n");
    this.partial = lines.pop() ?? "";

    const events: CcaEvent[] = [];
    for (const line of lines) {
      const event = parseEvent(line);
      if (event) events.push(event);
    }
    return events;
  }

  reset(): void {
    this.offset = 0;
    this.partial = "";
    this.decoder = new TextDecoder("utf-8");
  }
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `npm --prefix extension test`
Expected: PASS, 16 tests total.

---

### Task 8: `cfmd.ts` — parsing and editing CF.md

`CF.md` guarantees that entry headings are the only lines starting with `## ` (documented in `feedback.py`). This module relies on exactly that and nothing else.

**Files:**
- Create: `extension/src/cfmd.ts`
- Create: `extension/test/cfmd.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `interface CfEntry { heading: string; startLine: number; endLine: number; wontfix: boolean; body: readonly string[] }`
  - `parseCf(text: string): CfEntry[]`
  - `wontfixHeading(heading: string, reason: string): string`

- [x] **Step 1: Write the failing test**

```typescript
// extension/test/cfmd.test.ts
import { describe, expect, it } from "vitest";
import { parseCf, wontfixHeading } from "../src/cfmd";

const CF = [
  "# Critic Feedback (CF.md)",
  "",
  "Open items block the session from finishing. To arbitrate: fix the item and",
  "delete its heading, or insert a [wontfix] tag into the heading with a reason.",
  "",
  "## 2026-09-14 10:00:00 -- src/a.ts",
  "- **major / correctness** `src/a.ts:12` -- Null deref on empty list",
  "  - suggestion: guard",
  "",
  "## 2026-09-14 10:05:00 -- src/b.ts [wontfix] intentional duplication",
  "- **minor / reuse** `src/b.ts:4` -- Looks like tidy()",
  "",
  "## 2026-09-14 10:09:00 -- src/c.ts",
  "- **minor / convention** `src/c.ts:1` -- Prefer text over varchar",
  "",
].join("\n");

describe("parseCf", () => {
  it("finds every entry and nothing else", () => {
    const entries = parseCf(CF);
    expect(entries).toHaveLength(3);
    expect(entries[0].heading).toBe("## 2026-09-14 10:00:00 -- src/a.ts");
  });

  it("marks wontfix entries", () => {
    const entries = parseCf(CF);
    expect(entries.map((entry) => entry.wontfix)).toEqual([false, true, false]);
  });

  it("gives each entry a line range that stops at the next heading", () => {
    const entries = parseCf(CF);
    expect(entries[0].startLine).toBe(5);
    expect(entries[0].endLine).toBe(9);
    expect(entries[1].startLine).toBe(9);
  });

  it("runs the last entry to the end of the file", () => {
    const entries = parseCf(CF);
    const lines = CF.split("\n");
    expect(entries[2].endLine).toBe(lines.length);
  });

  it("keeps the body lines of an entry", () => {
    expect(parseCf(CF)[0].body).toEqual([
      "- **major / correctness** `src/a.ts:12` -- Null deref on empty list",
      "  - suggestion: guard",
      "",
    ]);
  });

  it("is not fooled by the header text or by an h1/h3", () => {
    expect(parseCf("# Title\n### Task 3\nbody\n")).toEqual([]);
  });

  it("returns nothing for an empty file", () => {
    expect(parseCf("")).toEqual([]);
  });

  it("detects [WontFix] regardless of case, matching open_items()", () => {
    expect(parseCf("## a [WONTFIX] because\n")[0].wontfix).toBe(true);
  });
});

describe("wontfixHeading", () => {
  it("appends the tag and the reason", () => {
    expect(wontfixHeading("## 2026-09-14 -- src/a.ts", "test-only helper"))
      .toBe("## 2026-09-14 -- src/a.ts [wontfix] test-only helper");
  });

  it("collapses newlines so the heading stays one line", () => {
    expect(wontfixHeading("## x", "two\nlines  here"))
      .toBe("## x [wontfix] two lines here");
  });

  it("still tags when no reason is given", () => {
    expect(wontfixHeading("## x", "   ")).toBe("## x [wontfix]");
  });

  it("leaves an already-tagged heading alone", () => {
    const tagged = "## x [wontfix] done";
    expect(wontfixHeading(tagged, "again")).toBe(tagged);
  });
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `npm --prefix extension test`
Expected: FAIL — cannot resolve `../src/cfmd`.

- [x] **Step 3: Write the module**

```typescript
// extension/src/cfmd.ts
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
```

- [x] **Step 4: Run the test to verify it passes**

Run: `npm --prefix extension test`
Expected: PASS, 28 tests total.

---

### Task 9: Activation, heartbeat, event feed, status bar

From here on the modules import `vscode` and cannot run under vitest — there is no editor. Verification is `tsc --noEmit` plus a scripted manual check in the Extension Development Host. That is stated rather than papered over: an adapter that only forwards is worth less test machinery than the pure modules it forwards to.

**Files:**
- Create: `extension/src/extension.ts`
- Create: `extension/src/heartbeat.ts`
- Create: `extension/src/feed.ts`
- Create: `extension/src/statusBar.ts`
- Create: `extension/.vscode/launch.json`

**Interfaces:**
- Consumes: `EventLogTail`, `FileSlice`, `CcaEvent` (Task 7); `writeJsonAtomic` (Task 6)
- Produces:
  - `resolveRoot(folders): string | undefined`
  - `class Heartbeat { constructor(root: string, version: string); start(): void; dispose(): void }`
  - `class EventFeed { constructor(root: string); readonly onEvents: vscode.Event<readonly CcaEvent[]>; get history(): readonly CcaEvent[]; poll(): Promise<void>; dispose(): void }`
  - `class StatusBar { consume(events: readonly CcaEvent[]): void; setOpenItems(n: number): void; setPending(n: number): void; dispose(): void }`

- [x] **Step 1: Write `heartbeat.ts`**

```typescript
// extension/src/heartbeat.ts
import * as fs from "node:fs";
import * as path from "node:path";
import { writeJsonAtomic } from "./fsatomic";

const INTERVAL_MS = 5000;

/** Presence, and nothing else. The hooks read this file to decide whether a
 *  human is reachable; if it stops being written they stop waiting within
 *  `arbiter.stale_after` seconds. */
export class Heartbeat {
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(private readonly root: string, private readonly version: string) {}

  start(): void {
    this.beat();
    this.timer = setInterval(() => this.beat(), INTERVAL_MS);
  }

  private beat(): void {
    try {
      writeJsonAtomic(path.join(this.root, ".cca", "arbiter.json"), {
        pid: process.pid,
        ts: Date.now() / 1000,
        host: "vscode",
        version: this.version,
      });
    } catch {
      // A read-only or vanished workspace must not take the extension down.
      // A missing heartbeat simply means the hooks stop asking.
    }
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    try {
      fs.rmSync(path.join(this.root, ".cca", "arbiter.json"), { force: true });
    } catch {
      /* already gone */
    }
  }
}
```

- [x] **Step 2: Write `feed.ts`**

```typescript
// extension/src/feed.ts
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
```

- [x] **Step 3: Write `statusBar.ts`**

```typescript
// extension/src/statusBar.ts
import * as vscode from "vscode";
import type { CcaEvent } from "./eventlog";

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export class StatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private current: number | null = null;
  private total: number | null = null;
  private open = 0;
  private pending = 0;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = "cca.openDashboard";
    this.render();
    this.item.show();
  }

  consume(events: readonly CcaEvent[]): void {
    for (const event of events) {
      if (event.type === "phase_context") {
        this.current = numberOrNull(event.payload.current);
        this.total = numberOrNull(event.payload.total) ?? this.total;
      } else if (event.type === "phase_gate" && this.current === null) {
        this.current = numberOrNull(event.payload.phase);
      }
    }
    this.render();
  }

  setOpenItems(count: number): void {
    this.open = count;
    this.render();
  }

  setPending(count: number): void {
    this.pending = count;
    this.render();
  }

  private render(): void {
    const parts = ["CCA"];
    if (this.current !== null) {
      parts.push(this.total !== null ? `phase ${this.current}/${this.total}` : `phase ${this.current}`);
    }
    if (this.open > 0) parts.push(`${this.open} open`);
    if (this.pending > 0) parts.push("$(watch) waiting on you");
    this.item.text = parts.join(" · ");
    this.item.tooltip = this.pending > 0
      ? "CCA is holding Claude for your decision"
      : "Open the CCA dashboard";
    this.item.backgroundColor = this.pending > 0
      ? new vscode.ThemeColor("statusBarItem.warningBackground")
      : undefined;
  }

  dispose(): void {
    this.item.dispose();
  }
}
```

- [x] **Step 4: Write `extension.ts`**

The wiring for Tasks 10–12 is included now so the later tasks only add files, never re-edit activation. Write it with the three imports commented out, then uncomment each as its task lands.

```typescript
// extension/src/extension.ts
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { EventFeed } from "./feed";
import { Heartbeat } from "./heartbeat";
import { StatusBar } from "./statusBar";

/** The project the hooks are writing into: the first workspace folder that has
 *  a `.cca/`, else the first folder at all (the user invoked the command in a
 *  project where the plugin has not run yet). */
export function resolveRoot(folders: readonly vscode.WorkspaceFolder[] | undefined): string | undefined {
  if (!folders || folders.length === 0) return undefined;
  const withCca = folders.find((folder) => fs.existsSync(path.join(folder.uri.fsPath, ".cca")));
  return (withCca ?? folders[0]).uri.fsPath;
}

export function activate(context: vscode.ExtensionContext): void {
  const root = resolveRoot(vscode.workspace.workspaceFolders);
  if (!root) return;

  const version = String(context.extension.packageJSON.version ?? "0.0.0");
  const heartbeat = new Heartbeat(root, version);
  heartbeat.start();

  const feed = new EventFeed(root);
  const status = new StatusBar();
  feed.onEvents((events) => status.consume(events));
  status.consume(feed.history);

  context.subscriptions.push(heartbeat, feed, status);

  // Task 10 adds: const decisions = new DecisionWatcher(root, status);
  // Task 11 adds: registerDashboard(context, root, feed, decisions);
  // Task 12 adds: registerFeedbackTree(context, root, status);
}

export function deactivate(): void {
  // Disposables registered above handle the heartbeat removal.
}
```

`extension/.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run CCA Arbiter",
      "type": "extensionHost",
      "request": "launch",
      "args": ["--extensionDevelopmentPath=${workspaceFolder}", "${workspaceFolder}/../test-project"],
      "outFiles": ["${workspaceFolder}/dist/**/*.js"],
      "preLaunchTask": "npm: build"
    }
  ]
}
```

- [x] **Step 5: Build and typecheck**

Run: `npm --prefix extension run build && npm --prefix extension test`
Expected: esbuild writes `dist/extension.js`; vitest 28 tests pass; `tsc --noEmit` is clean.

- [x] **Step 6: Verify the heartbeat against the real Python**

```bash
cd extension && node -e '
const { Heartbeat } = require("./dist/extension.js");
' 2>/dev/null || true
python3 - <<'CHECK'
import json, os, sys, tempfile, time
sys.path.insert(0, "scripts")
from ccalib import arbiter, config
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, ".cca"))
# exactly what heartbeat.ts writes
with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
    json.dump({"pid": 1, "ts": time.time(), "host": "vscode", "version": "0.1.0"}, fh)
assert arbiter.present(root, config.load(root)), "python does not see the heartbeat"
print("heartbeat shape accepted by arbiter.present")
CHECK
```
Expected: `heartbeat shape accepted by arbiter.present`

---

### Task 10: `decisions.ts` — the modals and the commit

**Files:**
- Create: `extension/src/git.ts`
- Create: `extension/src/decisions.ts`
- Modify: `extension/src/extension.ts` (uncomment the Task 10 line)

**Interfaces:**
- Consumes: `parseRequest`, `isExpired`, `buildDecision`, `writeJsonAtomic`, `ArbiterRequest` (Task 6); `StatusBar` (Task 9)
- Produces:
  - `runGit(root: string, args: readonly string[]): Promise<{ ok: boolean; error: string }>`
  - `class DecisionWatcher { constructor(root: string, status: StatusBar); readonly onPending: vscode.Event<readonly ArbiterRequest[]>; readonly onShipped: vscode.Event<number>; get pending(): readonly ArbiterRequest[]; dispose(): void }`

- [x] **Step 1: Write `git.ts`**

```typescript
// extension/src/git.ts
import { execFile } from "node:child_process";

export interface GitResult {
  readonly ok: boolean;
  readonly error: string;
}

/** `execFile`, never a shell: a branch name or a commit message with a quote
 *  in it must not become part of a command line. */
export function runGit(root: string, args: readonly string[]): Promise<GitResult> {
  return new Promise((resolve) => {
    execFile("git", [...args], { cwd: root, timeout: 60_000 }, (error, _stdout, stderr) => {
      if (error) {
        resolve({ ok: false, error: (stderr || error.message).trim() });
      } else {
        resolve({ ok: true, error: "" });
      }
    });
  });
}
```

- [x] **Step 2: Write `decisions.ts`**

```typescript
// extension/src/decisions.ts
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { writeJsonAtomic } from "./fsatomic";
import { runGit } from "./git";
import { buildDecision, isExpired, parseRequest, type ArbiterRequest } from "./protocol";
import type { StatusBar } from "./statusBar";

const LABELS: Readonly<Record<string, string>> = {
  uphold: "Uphold block",
  queue: "Queue to CF.md",
  overrule: "Overrule…",
  commit: "Commit",
  edit: "Edit message…",
  later: "Later",
};

const EXPIRY_SWEEP_MS = 2000;

export class DecisionWatcher implements vscode.Disposable {
  private readonly pendingDir: string;
  private readonly decisionsDir: string;
  private readonly watcher: vscode.FileSystemWatcher;
  private readonly timer: ReturnType<typeof setInterval>;
  private readonly live = new Map<string, ArbiterRequest>();
  private readonly claimed = new Set<string>();
  private readonly pendingEmitter = new vscode.EventEmitter<readonly ArbiterRequest[]>();
  private readonly shippedEmitter = new vscode.EventEmitter<number>();

  readonly onPending = this.pendingEmitter.event;
  readonly onShipped = this.shippedEmitter.event;

  constructor(private readonly root: string, private readonly status: StatusBar) {
    this.pendingDir = path.join(root, ".cca", "pending");
    this.decisionsDir = path.join(root, ".cca", "decisions");
    this.watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(vscode.Uri.file(this.pendingDir), "*.json"),
    );
    this.watcher.onDidCreate(() => this.scan());
    this.watcher.onDidChange(() => this.scan());
    this.watcher.onDidDelete(() => this.scan());
    this.timer = setInterval(() => this.scan(), EXPIRY_SWEEP_MS);
    this.scan();
  }

  get pending(): readonly ArbiterRequest[] {
    return [...this.live.values()];
  }

  private scan(): void {
    let names: string[] = [];
    try {
      names = fs.readdirSync(this.pendingDir).filter((name) => name.endsWith(".json"));
    } catch {
      names = []; // directory not created yet, or already swept
    }

    const now = Date.now();
    const alive = new Set<string>();
    for (const name of names) {
      let text: string;
      try {
        text = fs.readFileSync(path.join(this.pendingDir, name), "utf8");
      } catch {
        continue; // mid-rename; the next scan sees it
      }
      const request = parseRequest(text);
      if (!request || isExpired(request, now)) continue;
      alive.add(request.id);
      if (!this.live.has(request.id)) {
        this.live.set(request.id, request);
        void this.ask(request);
      }
    }

    // A request the hook has answered, swept or given up on stops being ours.
    for (const id of [...this.live.keys()]) {
      if (!alive.has(id)) {
        this.live.delete(id);
        this.claimed.delete(id);
      }
    }

    this.status.setPending(this.live.size);
    this.pendingEmitter.fire(this.pending);
  }

  private async ask(request: ArbiterRequest): Promise<void> {
    if (this.claimed.has(request.id)) return;
    this.claimed.add(request.id);

    const buttons = request.options.map((option) => LABELS[option] ?? option);
    const picked = await vscode.window.showWarningMessage(
      headline(request),
      { modal: true, detail: detail(request) },
      ...buttons,
    );
    // Dismissed. Write nothing: the hook's own default is the safe answer.
    if (picked === undefined) return;

    const choice = request.options[buttons.indexOf(picked)];
    if (choice === undefined) return;

    if (choice === "overrule") {
      const note = await vscode.window.showInputBox({
        prompt: "Why is this finding wrong? (recorded in the event log)",
        placeHolder: "e.g. the helper is test-only",
      });
      this.write(request, choice, { note: note ?? "" });
      return;
    }

    if (choice === "commit" || choice === "edit") {
      await this.commit(request, choice);
      return;
    }

    this.write(request, choice);
  }

  private async commit(request: ArbiterRequest, choice: string): Promise<void> {
    let message = request.message ?? "";
    if (choice === "edit") {
      const edited = await vscode.window.showInputBox({
        prompt: "Commit message",
        value: message,
        valueSelection: [message.length, message.length],
      });
      if (edited === undefined) return; // cancelled; let the hook time out to Later
      message = edited;
    }
    if (!message.trim()) {
      this.write(request, choice, { ok: false, error: "empty commit message" });
      return;
    }

    const files = [...(request.phase?.files ?? [])];
    if (request.planRel) files.push(request.planRel);
    const present = files.filter((file) => fs.existsSync(path.join(this.root, file)));
    if (present.length === 0) {
      this.write(request, choice, {
        ok: false,
        error: "none of this phase's files exist on disk; nothing to add",
      });
      return;
    }

    const added = await runGit(this.root, ["add", "--", ...present]);
    if (!added.ok) {
      this.write(request, choice, { ok: false, error: added.error });
      return;
    }
    const committed = await runGit(this.root, ["commit", "-m", message]);
    this.write(request, choice, { ok: committed.ok, error: committed.error });
    if (committed.ok && request.phase) this.shippedEmitter.fire(request.phase.number);
  }

  private write(
    request: ArbiterRequest,
    choice: string,
    extra: { note?: string; ok?: boolean; error?: string } = {},
  ): void {
    const decision = buildDecision(request, choice, extra);
    if (!decision) return;
    try {
      writeJsonAtomic(path.join(this.decisionsDir, `${request.id}.json`), decision);
    } catch (error) {
      void vscode.window.showErrorMessage(
        `CCA could not write your decision: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  dispose(): void {
    clearInterval(this.timer);
    this.watcher.dispose();
    this.pendingEmitter.dispose();
    this.shippedEmitter.dispose();
  }
}

function headline(request: ArbiterRequest): string {
  if (request.kind === "commit" && request.phase) {
    return `Phase ${request.phase.number}/${request.phase.total} — ${request.phase.title} is ready to ship.`;
  }
  return `Critics rejected the change to ${request.file ?? "this file"}.`;
}

function detail(request: ArbiterRequest): string {
  if (request.kind === "commit") {
    return [request.command ?? "", "", "Commit runs as you, from this workspace."]
      .join("\n")
      .trim();
  }
  const findings = (request.findings ?? [])
    .map((finding) => `• [${finding.severity}] ${finding.file}:${finding.line} — ${finding.issue}`)
    .join("\n");
  return findings || request.reason || "";
}
```

- [x] **Step 3: Wire it into `extension.ts`**

Replace the Task 10 comment with:

```typescript
  const decisions = new DecisionWatcher(root, status);
  context.subscriptions.push(decisions);
```

and add the import: `import { DecisionWatcher } from "./decisions";`

- [x] **Step 4: Build and typecheck**

Run: `npm --prefix extension run build && npm --prefix extension test`
Expected: clean build, 28 vitest tests pass, `tsc --noEmit` clean.

- [x] **Step 5: Verify the decision shape against the real Python**

```bash
python3 - <<'CHECK'
import json, os, sys, tempfile, threading, time
sys.path.insert(0, "scripts")
from ccalib import arbiter, config
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, ".cca"))
with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
    json.dump({"pid": 1, "ts": time.time(), "host": "vscode", "version": "0.1.0"}, fh)

def fake_extension():
    pending = os.path.join(root, ".cca", "pending")
    for _ in range(500):
        names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
        names = [n for n in names if n.endswith(".json")]
        if names:
            request = arbiter.read_json(os.path.join(pending, names[0]))
            # exactly what buildDecision() produces for an overrule
            arbiter.write_json(os.path.join(root, ".cca", "decisions", names[0]),
                               {"id": request["id"], "choice": "overrule",
                                "ts": time.time(), "note": "test-only helper"})
            return
        time.sleep(0.01)

threading.Thread(target=fake_extension, daemon=True).start()
cfg = config.load(root)
cfg["arbiter"]["block_wait"] = 5
answer = arbiter.request(root, cfg, "block",
                         {"options": ["uphold", "queue", "overrule"],
                          "file": "src/a.ts", "reason": "x", "findings": [],
                          "session_id": "s"})
assert answer and answer["choice"] == "overrule" and answer["note"] == "test-only helper", answer
print("decision shape accepted by arbiter.request:", answer["choice"])
CHECK
```
Expected: `decision shape accepted by arbiter.request: overrule`

---

### Task 11: The dashboard webview

Today's `dashboard/index.html` already renders the cast, the phase strip, the stats and the feed. It moves into the webview essentially unchanged: only `poll()` is replaced by a message listener, and a pending-decisions strip is added on top.

**Files:**
- Create: `extension/media/dashboard.css`
- Create: `extension/media/dashboard.js`
- Create: `extension/src/dashboard.ts`
- Modify: `extension/src/extension.ts` (uncomment the Task 11 line)

**Interfaces:**
- Consumes: `EventFeed` (Task 9), `DecisionWatcher` (Task 10)
- Produces: `registerDashboard(context, root, feed, decisions): void`, contributing the `cca.openDashboard` command

- [x] **Step 1: Move the styles**

Create `extension/media/dashboard.css` with the contents of the `<style>` block in `dashboard/index.html` (lines 3–47 of that file), plus these additions at the end:

```css
.pending { background: var(--panel); border: 1px solid var(--warn); border-radius: 8px;
           padding: 12px 14px; margin-bottom: 14px; }
.pending h2 { font-size: 13px; margin: 0 0 6px; color: var(--warn); }
.pending .q { margin: 4px 0; }
.pending .hint { color: var(--dim); font-size: 11px; margin-top: 6px; }
```

- [x] **Step 2: Move the script**

Create `extension/media/dashboard.js` with the contents of the `<script>` block in `dashboard/index.html` (the `CLASS_OF` constant through the `render` function, unchanged), then replace the trailing `poll()` / `setInterval` block with:

```javascript
const vscode = acquireVsCodeApi();
let events = [];

function renderPending(requests) {
  const el = document.getElementById("pending");
  if (!requests || requests.length === 0) { el.hidden = true; return; }
  el.hidden = false;
  let html = "<h2>waiting on you</h2>";
  for (const request of requests) {
    if (request.kind === "commit" && request.phase) {
      html += '<div class="q">phase <b>' + esc(request.phase.number) + "/"
        + esc(request.phase.total) + "</b> — " + esc(request.phase.title)
        + " is ready to ship</div>";
    } else {
      html += '<div class="q">critics rejected <b>' + esc(request.file) + "</b></div>";
    }
  }
  html += '<div class="hint">answer in the dialog, or in the notification if you dismissed it</div>';
  el.innerHTML = html;
}

window.addEventListener("message", (message) => {
  const data = message.data;
  if (data.kind === "reset") { events = data.events.slice(); render(events); }
  else if (data.kind === "events") { events = events.concat(data.events); render(events); }
  else if (data.kind === "pending") { renderPending(data.requests); }
});

vscode.postMessage({ kind: "ready" });
```

- [x] **Step 3: Write `dashboard.ts`**

```typescript
// extension/src/dashboard.ts
import * as vscode from "vscode";
import type { DecisionWatcher } from "./decisions";
import type { EventFeed } from "./feed";

let panel: vscode.WebviewPanel | undefined;

export function registerDashboard(
  context: vscode.ExtensionContext,
  root: string,
  feed: EventFeed,
  decisions: DecisionWatcher,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("cca.openDashboard", () => {
      if (panel) {
        panel.reveal(vscode.ViewColumn.Beside);
        return;
      }
      panel = vscode.window.createWebviewPanel(
        "ccaDashboard",
        "CCA Ensemble",
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        {
          enableScripts: true,
          retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
        },
      );
      const view = panel.webview;
      view.html = html(view, context.extensionUri, root);

      const ready = view.onDidReceiveMessage((message: { kind?: string }) => {
        if (message.kind !== "ready") return;
        void view.postMessage({ kind: "reset", events: feed.history });
        void view.postMessage({ kind: "pending", requests: decisions.pending });
      });
      const streaming = feed.onEvents((events) => {
        void view.postMessage({ kind: "events", events });
      });
      const waiting = decisions.onPending((requests) => {
        void view.postMessage({ kind: "pending", requests });
      });

      panel.onDidDispose(() => {
        ready.dispose();
        streaming.dispose();
        waiting.dispose();
        panel = undefined;
      });
    }),
  );
}

function html(view: vscode.Webview, extensionUri: vscode.Uri, root: string): string {
  const css = view.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", "dashboard.css"));
  const js = view.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", "dashboard.js"));
  const nonce = Array.from({ length: 32 }, () =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"[
      Math.floor(Math.random() * 62)
    ]).join("");
  const project = root.split(/[\\/]/).pop() ?? root;
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src ${view.cspSource}; script-src 'nonce-${nonce}';">
<link rel="stylesheet" href="${css}">
<title>CCA Ensemble</title>
</head>
<body>
<div class="wrap">
  <h1>CCA ENSEMBLE — ${escapeHtml(project)}</h1>
  <div class="pending" id="pending" hidden></div>
  <div class="cast">
    <div>coder: <b>claude-opus-5</b> &nbsp;·&nbsp; arbiter: <b>you, in this editor</b></div>
    <div>critics: <b>claude-opus-5</b> (design) &nbsp; <b>claude-fable-5-1</b> (correctness)</div>
  </div>
  <div class="cast" id="phase-strip" hidden></div>
  <div class="stats" id="stats"></div>
  <div id="feed"><div class="empty">waiting for the first edit…</div></div>
</div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
```

- [x] **Step 4: Wire it into `extension.ts`**

Replace the Task 11 comment with `registerDashboard(context, root, feed, decisions);` and add `import { registerDashboard } from "./dashboard";`.

- [x] **Step 5: Build and typecheck**

Run: `npm --prefix extension run build && npm --prefix extension test`
Expected: clean build, all vitest tests pass, `tsc --noEmit` clean.

---

### Task 12: The CF.md tree and the next-session button

**Files:**
- Create: `extension/src/cfTree.ts`
- Create: `extension/src/nextSession.ts`
- Modify: `extension/src/extension.ts` (uncomment the Task 12 line)

**Interfaces:**
- Consumes: `parseCf`, `wontfixHeading`, `CfEntry` (Task 8); `StatusBar` (Task 9); `DecisionWatcher.onShipped` (Task 10)
- Produces:
  - `registerFeedbackTree(context, root, status): void`
  - `offerNextPhase(root: string, shipped: number): void`

- [x] **Step 1: Write `cfTree.ts`**

```typescript
// extension/src/cfTree.ts
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { parseCf, wontfixHeading, type CfEntry } from "./cfmd";
import type { StatusBar } from "./statusBar";

// A DISCRIMINATED union. TypeScript cannot subtract a non-discriminated member
// from a union, so an `in`-based guard narrows only the positive branch and
// every `if (isGroup(node)) return;` leaves `node` as the whole union.
interface GroupNode {
  readonly kind: "group";
  readonly group: "open" | "wontfix";
}

interface EntryNode {
  readonly kind: "entry";
  readonly entry: CfEntry;
}

type Node = GroupNode | EntryNode;

function isGroup(node: Node): node is GroupNode {
  return node.kind === "group";
}

class FeedbackProvider implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  private entries: CfEntry[] = [];

  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly root: string, private readonly status: StatusBar) {
    this.reload();
  }

  get file(): string {
    return path.join(this.root, "CF.md");
  }

  reload(): void {
    let text = "";
    try {
      text = fs.readFileSync(this.file, "utf8");
    } catch {
      text = ""; // no findings yet
    }
    this.entries = parseCf(text);
    this.status.setOpenItems(this.entries.filter((entry) => !entry.wontfix).length);
    this.changed.fire(undefined);
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      const groups: Node[] = [{ group: "open" }];
      if (this.entries.some((entry) => entry.wontfix)) groups.push({ group: "wontfix" });
      return groups;
    }
    if (!isGroup(node)) return [];
    const wontfix = node.group === "wontfix";
    return this.entries.filter((entry) => entry.wontfix === wontfix).map((entry) => ({ entry }));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (isGroup(node)) {
      const open = this.entries.filter((entry) => !entry.wontfix).length;
      const closed = this.entries.length - open;
      const item = new vscode.TreeItem(
        node.group === "open" ? `Open (${open})` : `Wontfix (${closed})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      item.contextValue = "ccaGroup";
      return item;
    }

    const entry = node.entry;
    const item = new vscode.TreeItem(
      entry.heading.replace(/^##\s*/, ""),
      vscode.TreeItemCollapsibleState.None,
    );
    item.description = entry.body.find((line) => line.startsWith("- "))?.slice(2) ?? "";
    item.tooltip = new vscode.MarkdownString([entry.heading, ...entry.body].join("\n"));
    item.contextValue = entry.wontfix ? "ccaWontfix" : "ccaOpen";
    item.iconPath = new vscode.ThemeIcon(entry.wontfix ? "check" : "warning");
    item.command = {
      command: "cca.openFinding",
      title: "Open finding",
      arguments: [entry.startLine],
    };
    return item;
  }
}

/** Always re-parse the file immediately before editing it. The operator may
 *  have hand-edited CF.md since the tree was built, and a stale line number
 *  would delete the wrong entry. */
async function editEntry(
  file: string,
  heading: string,
  change: (edit: vscode.WorkspaceEdit, uri: vscode.Uri, entry: CfEntry) => void,
): Promise<void> {
  const uri = vscode.Uri.file(file);
  const document = await vscode.workspace.openTextDocument(uri);
  const entry = parseCf(document.getText()).find((candidate) => candidate.heading === heading);
  if (!entry) {
    void vscode.window.showWarningMessage("That CF.md entry is no longer there.");
    return;
  }
  const edit = new vscode.WorkspaceEdit();
  change(edit, uri, entry);
  if (await vscode.workspace.applyEdit(edit)) await document.save();
}

export function registerFeedbackTree(
  context: vscode.ExtensionContext,
  root: string,
  status: StatusBar,
): void {
  const provider = new FeedbackProvider(root, status);
  const view = vscode.window.createTreeView("ccaFeedback", { treeDataProvider: provider });

  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(root), "CF.md"),
  );
  watcher.onDidChange(() => provider.reload());
  watcher.onDidCreate(() => provider.reload());
  watcher.onDidDelete(() => provider.reload());

  context.subscriptions.push(
    view,
    watcher,
    vscode.commands.registerCommand("cca.openFinding", async (line: number) => {
      const document = await vscode.workspace.openTextDocument(vscode.Uri.file(provider.file));
      const editor = await vscode.window.showTextDocument(document);
      const position = new vscode.Position(Math.max(0, line), 0);
      editor.selection = new vscode.Selection(position, position);
      editor.revealRange(new vscode.Range(position, position));
    }),
    vscode.commands.registerCommand("cca.overrule", async (node: Node) => {
      if (isGroup(node)) return;
      const heading = node.entry.heading;
      const confirmed = await vscode.window.showWarningMessage(
        "Delete this finding from CF.md?",
        { modal: true, detail: heading },
        "Overrule",
      );
      if (confirmed !== "Overrule") return;
      await editEntry(provider.file, heading, (edit, uri, entry) => {
        edit.delete(uri, new vscode.Range(entry.startLine, 0, entry.endLine, 0));
      });
    }),
    vscode.commands.registerCommand("cca.wontfix", async (node: Node) => {
      if (isGroup(node)) return;
      const reason = await vscode.window.showInputBox({
        prompt: "Why is this not being fixed?",
        placeHolder: "e.g. deliberate duplication, the two paths diverge next sprint",
      });
      if (reason === undefined) return;
      const heading = node.entry.heading;
      await editEntry(provider.file, heading, (edit, uri, entry) => {
        edit.replace(
          uri,
          new vscode.Range(entry.startLine, 0, entry.startLine, entry.heading.length),
          wontfixHeading(entry.heading, reason),
        );
      });
    }),
  );
}
```

- [x] **Step 2: Write `nextSession.ts`**

```typescript
// extension/src/nextSession.ts
import * as vscode from "vscode";

/** One phase, one session. The plugin does not yet enforce that (it is the
 *  next sub-project); this makes the right thing the easy thing. */
export function offerNextPhase(root: string, shipped: number): void {
  void vscode.window
    .showInformationMessage(`Phase ${shipped} shipped.`, "Start next phase in a new session")
    .then((choice) => {
      if (!choice) return;
      const terminal = vscode.window.createTerminal({
        name: `CCA phase ${shipped + 1}`,
        cwd: root,
      });
      terminal.show();
      terminal.sendText("claude");
    });
}
```

- [x] **Step 3: Wire both into `extension.ts`**

Replace the Task 12 comment with:

```typescript
  registerFeedbackTree(context, root, status);
  decisions.onShipped((phase) => offerNextPhase(root, phase));
```

and add the imports `import { registerFeedbackTree } from "./cfTree";` and `import { offerNextPhase } from "./nextSession";`.

- [x] **Step 4: Build and typecheck**

Run: `npm --prefix extension run build && npm --prefix extension test`
Expected: clean build, all vitest tests pass, `tsc --noEmit` clean.

- [x] **Step 5: Verify the wontfix tag really closes an item in Python**

```bash
python3 - <<'CHECK'
import os, sys, tempfile
sys.path.insert(0, "scripts")
from ccalib import feedback
root = tempfile.mkdtemp()
with open(os.path.join(root, "CF.md"), "w") as fh:
    fh.write(feedback.HEADER)
    fh.write("\n## 2026-09-14 10:00:00 -- src/a.ts\n- **major / correctness** `src/a.ts:1` -- x\n")
assert len(feedback.open_items(root)) == 1
# exactly what wontfixHeading() produces
body = open(os.path.join(root, "CF.md")).read().replace(
    "## 2026-09-14 10:00:00 -- src/a.ts",
    "## 2026-09-14 10:00:00 -- src/a.ts [wontfix] test-only helper")
open(os.path.join(root, "CF.md"), "w").write(body)
assert feedback.open_items(root) == [], feedback.open_items(root)
print("wontfix heading closes the item")
CHECK
```
Expected: `wontfix heading closes the item`

---

### Task 13: README, packaging, and the end-to-end smoke test

**Files:**
- Create: `extension/README.md`
- Modify: `README.md` (repository root — add the extension section, and correct the stale test count)
- Modify: `docs/superpowers/specs/2026-09-14-cca-ide-arbiter-design.md` (record what the first implementation confirmed or contradicted, as the other two specs do)

**Interfaces:**
- Consumes: everything
- Produces: a `.vsix` and documentation

- [x] **Step 1: Write `extension/README.md`**

```markdown
# CCA Arbiter

The arbiter's seat for the [CCA plugin](../README.md), inside the editor.

The Python hooks decide what to ask. This extension renders the question and
returns your answer. It never decides anything on its own, and with it closed
the plugin behaves exactly as it does from the terminal.

## What it does

- **Live dashboard** (`CCA: Open Dashboard`) — the critic feed, the phase strip
  and the tuning stats, streamed from `.cca/events.jsonl`.
- **Block modal** — when the critics reject an edit: *Uphold*, *Queue to CF.md*,
  or *Overrule* with a note. Dismiss it and the block stands.
- **Commit modal** — when a phase is ready: *Commit*, *Edit message…*, *Later*.
  The commit runs as your click, from your workspace, with exactly the files the
  phase gate named.
- **Critic Feedback tree** — `CF.md` as a list, with *Overrule* and *Wontfix…*
  applied as undoable workspace edits.
- **Status bar** — `CCA · phase 3/11 · 2 open · ⏳ waiting on you`.

## Install

```bash
npm --prefix extension install
npm --prefix extension run package      # produces cca-arbiter-0.1.0.vsix
code --install-extension extension/cca-arbiter-0.1.0.vsix
```

Any VS Code 1.85+ host works, including Cursor, Windsurf and Antigravity — the
extension uses no proprietary APIs. It cannot hook a different agent's edits,
though: the enforcement is Claude Code's hooks, so this helps only when Claude
Code is the one editing.

## How it talks to the plugin

A file mailbox under `.cca/`. No ports, no daemon, no sockets.

| File | Written by | Meaning |
|---|---|---|
| `arbiter.json` | extension, every 5s | a human is reachable |
| `pending/<id>.json` | hook | a question, with a deadline |
| `decisions/<id>.json` | extension | the answer |

If the heartbeat is missing or older than `arbiter.stale_after` (15s), the hooks
do not wait at all. Timeout, malformed answer and crash all fall back to the
plugin's own default: **Uphold** for a block, **Later** for a commit.

## Configuration

`.cca/config.json` in the project:

```json
{ "arbiter": { "enabled": true, "block_wait": 180, "commit_wait": 300, "stale_after": 15 } }
```

`"enabled": false` turns the bridge off and leaves the critics and the phase
gate untouched.

## Development

```bash
npm --prefix extension test     # vitest on the pure modules, then tsc --noEmit
npm --prefix extension run build
```

F5 in `extension/` opens an Extension Development Host on `../test-project`.
```

- [x] **Step 2: Update the repository README**

In the root `README.md`: change the "94 tests" line at the end to the real number from `python3 -m unittest discover tests` (321 after Task 5, if the baseline has not moved again), and add this section immediately after "The dashboard":

```markdown
## The IDE arbiter

```bash
npm --prefix extension install && npm --prefix extension run package
code --install-extension extension/cca-arbiter-0.1.0.vsix
```

With the extension running, the two decisions that are yours stop being
paragraphs printed after the fact and become questions asked at the moment they
matter: a critic block (*Uphold* / *Queue* / *Overrule*) and the phase commit
(*Commit* / *Edit message…* / *Later*). The commit runs as your click.

Nothing about this is required. The hooks are still the lock — the extension
only renders the question and returns the answer. Close it, and every gate
behaves exactly as it does from the terminal. See [`extension/README.md`](extension/README.md).
```

- [x] **Step 3: Package the extension**

Run: `npm --prefix extension run package`
Expected: `extension/cca-arbiter-0.1.0.vsix` is produced. Warnings about a missing repository field or LICENSE are acceptable; errors are not.

- [x] **Step 4: Run the whole Python suite and the whole TypeScript suite**

Run:
```bash
python3 -m unittest discover tests && npm --prefix extension test
```
Expected: `OK` with 321 Python tests and 28 vitest tests, then a clean `tsc --noEmit`. Record the real numbers — if either is red, the task is not done.

- [ ] **Step 5: End-to-end smoke test in the Extension Development Host**

This is the only verification that exercises the extension's own code, so do it deliberately rather than by eye.

Prepare a throwaway project (never this repository):

```bash
SMOKE=$(mktemp -d)
mkdir -p "$SMOKE/docs/superpowers/plans" "$SMOKE/src" "$SMOKE/.cca" "$SMOKE/bin"
# The fixture plan is written by python rather than a heredoc on purpose: a
# literal "### Task N:" at the start of a line in THIS document would be parsed
# as a phase of this plan by planparse.py.
python3 - "$SMOKE" <<'EOF'
import os, sys
root = sys.argv[1]
lines = [
    "### Task 1: One", "", "**Files:**", "- Create: `src/a.ts`", "",
    "- [ ] **Step 1: build it**", "",
    "### Task 2: Two", "", "**Files:**", "- Create: `src/b.ts`", "",
    "- [ ] **Step 1: build it**", "",
]
with open(os.path.join(root, "docs", "superpowers", "plans", "p.md"), "w") as fh:
    fh.write("\n".join(lines))
EOF
cat > "$SMOKE/.cca/config.json" <<'EOF'
{ "phases": { "enabled": true, "test_command": "python3 -c 'pass'" },
  "arbiter": { "enabled": true, "block_wait": 120, "commit_wait": 120 } }
EOF
printf '.cca/\nCF.md\nbin/\n' > "$SMOKE/.gitignore"
cat > "$SMOKE/bin/claude" <<'EOF'
#!/bin/sh
cat >/dev/null
printf '{"verdict":"fail","findings":[{"severity":"major","kind":"correctness","file":"src/a.ts","line":1,"issue":"Null deref on empty list","evidence":"list may be empty","suggestion":"guard"}]}'
EOF
chmod +x "$SMOKE/bin/claude"
git -C "$SMOKE" init -q && git -C "$SMOKE" add -A && git -C "$SMOKE" -c user.email=t@e.com -c user.name=T commit -qm seed
echo "$SMOKE"
```

Point `extension/.vscode/launch.json`'s second `args` entry at that path, press F5, and walk the list:

| # | Do this | Expect |
|---|---|---|
| 1 | Extension Development Host opens | status bar shows `CCA`; `.cca/arbiter.json` exists and its `ts` advances every 5s |
| 2 | `CCA: Open Dashboard` | webview opens, "waiting for the first edit…" |
| 3 | From this repository's root, with `SMOKE` still exported: `PATH="$SMOKE/bin:$PATH" python3 scripts/critic_hook.py <<< "{\"tool_name\":\"Edit\",\"cwd\":\"$SMOKE\",\"session_id\":\"s1\",\"tool_input\":{\"file_path\":\"src/a.ts\",\"old_string\":\"a\",\"new_string\":\"b\"}}"` | modal appears within ~1s with three buttons; dashboard shows the pending strip; status bar turns orange |
| 4 | Click **Overrule…**, type a reason | the hook prints nothing and exits; `events.jsonl` has an `arbitration` with your note |
| 5 | Repeat step 3, click **Queue to CF.md** | hook prints `additionalContext`; `CF.md` appears; the tree view lists one open item |
| 6 | In the tree, **Wontfix…** with a reason | the heading gains `[wontfix]`; the item moves to the Wontfix group; status bar open count drops to 0 |
| 7 | Repeat step 3 and dismiss the modal with `Esc` | after `block_wait` the hook prints `{"decision":"block"}` — the default held |
| 8 | `echo x > "$SMOKE/src/a.ts"`, then `python3 scripts/drain_hook.py <<< "{\"stop_hook_active\":false,\"cwd\":\"$SMOKE\",\"session_id\":\"s1\"}"` | commit modal names phase 1/2; clicking **Commit** creates a `[phase-1] One` commit and the hook exits silently |
| 9 | Check `git -C "$SMOKE" show --stat HEAD` | the plan file is in the commit and its checkbox is ticked |
| 10 | Close the Extension Development Host, wait 20s, repeat step 3 | the hook blocks immediately without waiting — the bridge is gone, the gate is not |

Step 10 is the one that matters most: it is the whole failure-mode contract in a single observation.

- [x] **Step 6: Record what the implementation confirmed**

Append a `## 15. Implementation notes` section to the spec listing: which of the three unexercised VS Code APIs in §2 behaved as assumed, anything that had to change, and the real test counts. The other two specs in this repository carry the same kind of after-the-fact correction, and it is the part that stops the spec from quietly drifting away from the code.

---

## Execution Notes

**Order matters.** Tasks 1–5 leave the plugin fully working with no extension in existence — at the end of Task 5 the Python suite is green and the plugin behaves exactly as it does today for anyone without the extension. Tasks 6–8 are pure TypeScript with no editor. Tasks 9–12 are the adapters. Task 13 is where it is proven end to end.

**Two contracts are checked against the real Python, not against a mock:** the heartbeat shape (Task 9, Step 6) and the decision shape (Task 10, Step 5). They exist because a TypeScript test and a Python test can both pass while disagreeing about the format on disk.

**No task commits.** The operator decides when and what to commit. Each task ends with a verification command and its expected output.
