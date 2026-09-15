# CCA Ensemble Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Claude Code plugin where the main Opus session writes code, two fresh Claude instances on different models critique every edit, and the human arbitrates — with no external model APIs.

**Architecture:** A `PostToolUse` command hook fans out to parallel headless `claude -p` critics with read-only tool allow-lists, derives a verdict from surviving findings, and votes to block / queue / pass. A `Stop` hook drains the `CF.md` feedback queue. All state is an append-only JSONL event log, which a static polled page renders as a live dashboard.

**Tech Stack:** Python 3.9 stdlib only (`unittest`, `json`, `subprocess`, `concurrent.futures`), plain HTML/CSS/JS for the dashboard, Claude Code `command` hooks.

**Spec:** [`docs/superpowers/specs/2026-09-14-cca-ensemble-plugin-design.md`](../specs/2026-09-14-cca-ensemble-plugin-design.md)

## Global Constraints

- **Python 3.9.6, stdlib only.** No pytest, no third-party packages. Tests use `unittest`. No `match` statements, no PEP 604 `X | Y` annotations — use `typing.List`, `typing.Dict`, `typing.Optional`.
- **No git operations.** The user handles all staging, commits and pushes. Never run `git add`, `git commit`, `git push`, or `git init`. Each task ends with a verification step, not a commit.
- **Fail open, always.** Every hook entrypoint wraps its body in a top-level `try/except` that exits 0. A bug in the critic must never prevent the user from editing.
- **Recursion firewall first.** `CCA_INNER=1` is checked before any other work in every hook entrypoint. Critics are `claude -p` processes inside the same project; without this they load the same hooks and fork-bomb the plan quota.
- **Evidence bar is code, not prompt.** Enforced in `verdict.py`. A brief cannot be trusted to police itself.
- **Model IDs, exact:** `claude-opus-5` (design critic), `claude-fable-5-1` (correctness critic). Never append date suffixes.
- **Project root** is `~/Documents/ChatGPT/CCA`. All paths below are relative to it.

---

### Task 1: Append-only event log

**Files:**
- Create: `scripts/ccalib/__init__.py`
- Create: `scripts/ccalib/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing
- Produces: `append(event_type: str, payload: Dict[str, Any], root: str) -> None`, `read_all(root: str) -> List[Dict[str, Any]]`, and `EVENT_TYPES: FrozenSet[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events.py
import json, os, tempfile, unittest, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import events


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_append_creates_file_and_dir(self):
        events.append("edit_started", {"file": "a.ts"}, root=self.tmp)
        path = os.path.join(self.tmp, ".cca", "events.jsonl")
        self.assertTrue(os.path.exists(path))

    def test_append_writes_one_json_object_per_line(self):
        events.append("edit_started", {"file": "a.ts"}, root=self.tmp)
        events.append("vote", {"decision": "pass"}, root=self.tmp)
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["type"], "edit_started")
        self.assertEqual(json.loads(lines[1])["payload"]["decision"], "pass")

    def test_every_event_carries_type_ts_and_payload(self):
        events.append("vote", {"decision": "block"}, root=self.tmp)
        rec = events.read_all(self.tmp)[0]
        self.assertEqual(set(rec.keys()), {"type", "ts", "payload"})
        self.assertIsInstance(rec["ts"], float)

    def test_read_all_on_missing_file_returns_empty(self):
        self.assertEqual(events.read_all(self.tmp), [])

    def test_read_all_skips_corrupt_lines(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "events.jsonl"), "w") as fh:
            fh.write('{"type":"vote","ts":1.0,"payload":{}}\n')
            fh.write('NOT JSON\n')
            fh.write('{"type":"queued","ts":2.0,"payload":{}}\n')
        self.assertEqual(len(events.read_all(self.tmp)), 2)

    def test_unknown_event_type_is_rejected(self):
        with self.assertRaises(ValueError):
            events.append("not_a_real_event", {}, root=self.tmp)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_events -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccalib'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/events.py
"""Append-only event log. The dashboard reads it; the tuning analysis reads it."""
import json
import os
import time
from typing import Any, Dict, FrozenSet, List

EVENT_TYPES = frozenset([
    "edit_started", "layer0_result", "critic_dispatched", "critic_verdict",
    "vote", "queued", "blocked", "drain_blocked", "session_end",
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
```

Also create an empty `scripts/ccalib/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_events -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Verify (no commit — user handles git)**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 2: Verdict parsing and the evidence bar

This is the component that makes the critics useful rather than noisy. The critic's own `verdict` field is **recorded but not trusted** — the effective verdict is derived from the findings that survive filtering, which removes the "says pass, lists three criticals" class of inconsistency entirely.

**Files:**
- Create: `scripts/ccalib/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: nothing
- Produces: `parse(raw: str, critic: str) -> Dict[str, Any]` returning `{"critic": str, "stated": str, "verdict": str, "findings": List[Dict], "degraded": bool}`; constants `EVIDENCE_REQUIRED_KINDS`, `FORBIDDEN_KINDS`, `HEDGE_MARKERS`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_verdict.py
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import verdict


def payload(findings, stated="fail"):
    return json.dumps({"verdict": stated, "findings": findings})


GOOD_REUSE = {
    "severity": "major", "kind": "reuse", "file": "src/a.ts", "line": 4,
    "issue": "Reimplements currency formatting",
    "evidence": "lib/format.ts:12 exports formatCurrency() with the same signature",
    "suggestion": "Import formatCurrency",
}


class TestExtraction(unittest.TestCase):
    def test_extracts_json_embedded_in_prose(self):
        raw = "Sure! Here is my review:\n" + payload([GOOD_REUSE]) + "\nHope that helps."
        self.assertFalse(verdict.parse(raw, "opus")["degraded"])

    def test_unparseable_output_is_warn_and_degraded(self):
        r = verdict.parse("I could not complete the review.", "opus")
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_empty_output_is_warn_and_degraded(self):
        self.assertTrue(verdict.parse("", "opus")["degraded"])


class TestEvidenceBar(unittest.TestCase):
    def test_reuse_without_evidence_cannot_block(self):
        f = dict(GOOD_REUSE); f["evidence"] = ""
        r = verdict.parse(payload([f]), "opus")
        self.assertEqual(r["findings"][0]["severity"], "minor")
        self.assertEqual(r["verdict"], "warn")

    def test_reuse_with_evidence_can_block(self):
        r = verdict.parse(payload([GOOD_REUSE]), "opus")
        self.assertEqual(r["findings"][0]["severity"], "major")
        self.assertEqual(r["verdict"], "fail")

    def test_complexity_without_evidence_cannot_block(self):
        f = {"severity": "critical", "kind": "complexity", "file": "a.ts",
             "line": 1, "issue": "slow"}
        r = verdict.parse(payload([f]), "fable")
        self.assertEqual(r["findings"][0]["severity"], "minor")
        self.assertEqual(r["verdict"], "warn")

    def test_correctness_does_not_require_evidence(self):
        f = {"severity": "critical", "kind": "correctness", "file": "a.ts",
             "line": 9, "issue": "Null deref when supplier list is empty"}
        r = verdict.parse(payload([f]), "fable")
        self.assertEqual(r["verdict"], "fail")


class TestDropRules(unittest.TestCase):
    def test_hedging_findings_are_dropped(self):
        for hedge in ["Consider extracting this", "You might want to rename it",
                      "It would be cleaner to split this"]:
            f = dict(GOOD_REUSE); f["issue"] = hedge
            r = verdict.parse(payload([f]), "opus")
            self.assertEqual(r["findings"], [], hedge)

    def test_style_findings_are_dropped(self):
        f = {"severity": "major", "kind": "style", "file": "a.ts", "line": 1,
             "issue": "Use single quotes"}
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])

    def test_finding_without_file_is_dropped(self):
        f = dict(GOOD_REUSE); f.pop("file")
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])

    def test_unknown_kind_is_dropped(self):
        f = dict(GOOD_REUSE); f["kind"] = "vibes"
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])


class TestDerivedVerdict(unittest.TestCase):
    def test_verdict_is_derived_not_trusted(self):
        # critic claims pass but reports a real critical
        f = {"severity": "critical", "kind": "correctness", "file": "a.ts",
             "line": 2, "issue": "Deletes rows without a where clause"}
        r = verdict.parse(payload([f], stated="pass"), "fable")
        self.assertEqual(r["stated"], "pass")
        self.assertEqual(r["verdict"], "fail")

    def test_fail_with_no_surviving_findings_becomes_pass(self):
        f = {"severity": "major", "kind": "style", "file": "a.ts", "line": 1,
             "issue": "Use tabs"}
        r = verdict.parse(payload([f], stated="fail"), "opus")
        self.assertEqual(r["verdict"], "pass")

    def test_only_minor_findings_yield_warn(self):
        f = {"severity": "minor", "kind": "decomposition", "file": "a.ts",
             "line": 1, "issue": "Function handles parsing and rendering"}
        self.assertEqual(verdict.parse(payload([f]), "opus")["verdict"], "warn")

    def test_no_findings_yields_pass(self):
        self.assertEqual(verdict.parse(payload([]), "opus")["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_verdict -v`
Expected: FAIL — `ImportError: cannot import name 'verdict'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/verdict.py
"""Parse a critic's output and enforce the evidence bar.

Two rules make the critics useful rather than noisy:
  1. Findings that cannot point at concrete evidence cannot block.
  2. The effective verdict is DERIVED from surviving findings, never taken
     from the critic's own `verdict` field, which can contradict them.
"""
import json
from typing import Any, Dict, FrozenSet, List, Optional

VALID_KINDS = frozenset([
    "reuse", "correctness", "complexity", "decomposition",
    "scope", "convention", "schema",
])  # type: FrozenSet[str]

FORBIDDEN_KINDS = frozenset(["style", "restyle", "preference", "nit"])

EVIDENCE_REQUIRED_KINDS = frozenset(["reuse", "complexity"])

HEDGE_MARKERS = (
    "consider ", "might want", "you may want", "would be cleaner",
    "could be improved", "perhaps ", "it may be worth", "nice to have",
)

VALID_SEVERITIES = ("critical", "major", "minor")

_BLOCKING_SEVERITIES = frozenset(["critical", "major"])


def _extract_json(raw):
    # type: (str) -> Optional[Dict[str, Any]]
    if not raw:
        return None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
    except ValueError:
        return None
    try:
        parsed = json.loads(raw[start:end])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalise(finding):
    # type: (Any) -> Optional[Dict[str, Any]]
    """Return the finding with the evidence bar applied, or None to drop it."""
    if not isinstance(finding, dict):
        return None

    kind = str(finding.get("kind", "")).lower()
    if kind in FORBIDDEN_KINDS or kind not in VALID_KINDS:
        return None

    issue = str(finding.get("issue", "")).strip()
    if not issue:
        return None
    lowered = issue.lower()
    if any(marker in lowered for marker in HEDGE_MARKERS):
        return None  # unactionable by construction

    if not str(finding.get("file", "")).strip():
        return None  # a finding you cannot locate is not a finding

    severity = str(finding.get("severity", "minor")).lower()
    if severity not in VALID_SEVERITIES:
        severity = "minor"

    evidence = str(finding.get("evidence", "")).strip()
    if kind in EVIDENCE_REQUIRED_KINDS and not evidence:
        severity = "minor"  # survives as a note, but can never block

    return {
        "severity": severity,
        "kind": kind,
        "file": str(finding.get("file", "")).strip(),
        "line": finding.get("line", 0),
        "issue": issue,
        "evidence": evidence,
        "suggestion": str(finding.get("suggestion", "")).strip(),
    }


def _derive(findings):
    # type: (List[Dict[str, Any]]) -> str
    if any(f["severity"] in _BLOCKING_SEVERITIES for f in findings):
        return "fail"
    if findings:
        return "warn"
    return "pass"


def parse(raw, critic):
    # type: (str, str) -> Dict[str, Any]
    parsed = _extract_json(raw)
    if parsed is None:
        return {
            "critic": critic, "stated": "", "verdict": "warn",
            "degraded": True,
            "findings": [{
                "severity": "minor", "kind": "correctness",
                "file": "(critic output)", "line": 0,
                "issue": "Critic returned unparseable output",
                "evidence": (raw or "")[:400], "suggestion": "",
            }],
        }

    raw_findings = parsed.get("findings") or []
    if not isinstance(raw_findings, list):
        raw_findings = []

    findings = []  # type: List[Dict[str, Any]]
    for item in raw_findings:
        normalised = _normalise(item)
        if normalised is not None:
            findings.append(normalised)

    return {
        "critic": critic,
        "stated": str(parsed.get("verdict", "")).lower(),
        "verdict": _derive(findings),
        "findings": findings,
        "degraded": False,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_verdict -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 3: The vote

**Files:**
- Create: `scripts/ccalib/vote.py`
- Test: `tests/test_vote.py`

**Interfaces:**
- Consumes: verdict dicts from `verdict.parse`
- Produces: `decide(verdicts: List[Dict], layer0: List[Dict]) -> Dict[str, Any]` returning `{"decision": "block"|"queue"|"pass", "findings": List[Dict], "reason": str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vote.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import vote


def V(critic, verdict_value, findings=None):
    return {"critic": critic, "stated": verdict_value, "verdict": verdict_value,
            "findings": findings or [], "degraded": False}


def F(severity="major", kind="correctness"):
    return {"severity": severity, "kind": kind, "file": "a.ts", "line": 1,
            "issue": "x", "evidence": "e", "suggestion": ""}


class TestVote(unittest.TestCase):
    def test_all_pass_is_pass(self):
        r = vote.decide([V("opus", "pass"), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "pass")

    def test_both_fail_blocks(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "fail", [F()])], [])
        self.assertEqual(r["decision"], "block")

    def test_one_fail_queues(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "queue")

    def test_any_critical_blocks_even_alone(self):
        r = vote.decide([V("opus", "fail", [F("critical")]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "block")

    def test_any_warn_queues(self):
        r = vote.decide([V("opus", "warn", [F("minor")]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "queue")

    def test_layer0_error_blocks_regardless(self):
        layer0 = [{"severity": "critical", "kind": "convention", "file": "a.ts",
                   "line": 3, "issue": "TS2345 type error", "evidence": "tsc",
                   "suggestion": "", "blocking": True}]
        r = vote.decide([V("opus", "pass"), V("fable", "pass")], layer0)
        self.assertEqual(r["decision"], "block")

    def test_layer0_non_blocking_only_queues(self):
        layer0 = [{"severity": "minor", "kind": "reuse", "file": "a.ts", "line": 3,
                   "issue": "duplicate block", "evidence": "jscpd",
                   "suggestion": "", "blocking": False}]
        r = vote.decide([V("opus", "pass"), V("fable", "pass")], layer0)
        self.assertEqual(r["decision"], "queue")

    def test_two_degraded_critics_never_block(self):
        # both critics timed out — must not block the coder
        a = {"critic": "opus", "stated": "", "verdict": "warn", "findings": [F("minor")], "degraded": True}
        b = {"critic": "fable", "stated": "", "verdict": "warn", "findings": [F("minor")], "degraded": True}
        self.assertEqual(vote.decide([a, b], [])["decision"], "queue")

    def test_findings_are_aggregated_across_sources(self):
        r = vote.decide([V("opus", "warn", [F("minor")]), V("fable", "warn", [F("minor")])],
                        [{"severity": "minor", "kind": "reuse", "file": "b.ts", "line": 1,
                          "issue": "dup", "evidence": "jscpd", "suggestion": "", "blocking": False}])
        self.assertEqual(len(r["findings"]), 3)

    def test_reason_is_non_empty_when_not_pass(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "fail", [F()])], [])
        self.assertTrue(r["reason"].strip())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_vote -v`
Expected: FAIL — `ImportError: cannot import name 'vote'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/vote.py
"""Three-outcome arbitration.

A single critic that can block on any nitpick grinds the coder to a halt, so
blocking needs consensus — with two exceptions that are facts rather than
opinions: a deterministic tool error, and any critical finding.
"""
from typing import Any, Dict, List


def _format(findings):
    # type: (List[Dict[str, Any]]) -> str
    lines = []
    for f in findings:
        lines.append("[%s] %s %s:%s — %s%s" % (
            f.get("severity", "minor"), f.get("kind", ""),
            f.get("file", ""), f.get("line", ""), f.get("issue", ""),
            (" → " + f["suggestion"]) if f.get("suggestion") else "",
        ))
    return "\n".join(lines)


def decide(verdicts, layer0):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> Dict[str, Any]
    critic_findings = []  # type: List[Dict[str, Any]]
    for v in verdicts:
        critic_findings.extend(v.get("findings", []))
    all_findings = list(layer0) + critic_findings

    layer0_blocking = any(f.get("blocking") for f in layer0)
    fails = sum(1 for v in verdicts if v.get("verdict") == "fail")
    # a degraded critic (timeout / unparseable) never contributes a critical
    critical = any(
        f.get("severity") == "critical"
        for v in verdicts if not v.get("degraded")
        for f in v.get("findings", [])
    )

    if layer0_blocking:
        return {"decision": "block", "findings": all_findings,
                "reason": "Deterministic checks failed:\n" + _format(layer0)}

    if fails >= 2 or critical:
        return {"decision": "block", "findings": all_findings,
                "reason": "Critics rejected this change:\n" + _format(all_findings)}

    if all_findings:
        return {"decision": "queue", "findings": all_findings,
                "reason": _format(all_findings)}

    return {"decision": "pass", "findings": [], "reason": ""}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_vote -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 4: Critic runner

Tested against a **fake `claude` executable** on `PATH` — a shell script that echoes a canned verdict. This is what makes the runner testable without spending model calls or waiting a minute per test.

**Files:**
- Create: `scripts/ccalib/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `verdict.parse`
- Produces: `build_command(model, brief_path, allowed_tools) -> List[str]`, `run_one(model, brief_path, payload, timeout, critic_name, cwd) -> Dict`, `run_all(critics, payload, cwd) -> List[Dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
import json, os, stat, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import runner

CANNED = json.dumps({"verdict": "fail", "findings": [{
    "severity": "major", "kind": "correctness", "file": "a.ts", "line": 2,
    "issue": "Off-by-one in the loop bound", "evidence": "", "suggestion": "Use <=",
}]})


def fake_claude(tmpdir, body):
    """Put an executable named `claude` on PATH that behaves as `body` says."""
    path = os.path.join(tmpdir, "claude")
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    os.environ["PATH"] = tmpdir + os.pathsep + os.environ["PATH"]
    return path


class TestBuildCommand(unittest.TestCase):
    def test_command_pins_model_and_readonly_tools(self):
        cmd = runner.build_command("claude-opus-5", "/briefs/design.md",
                                   ["Read", "Grep", "Glob"])
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5")
        self.assertIn("--allowedTools", cmd)
        self.assertNotIn("Edit", cmd)
        self.assertNotIn("Write", cmd)

    def test_command_uses_dontask_permission_mode(self):
        cmd = runner.build_command("claude-opus-5", "/b.md", ["Read"])
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")

    def test_command_passes_brief_as_system_prompt_file(self):
        cmd = runner.build_command("claude-opus-5", "/b.md", ["Read"])
        self.assertEqual(cmd[cmd.index("--system-prompt-file") + 1], "/b.md")


class TestRunOne(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.brief = os.path.join(self.tmp, "brief.md")
        with open(self.brief, "w") as fh:
            fh.write("be a critic")
        self._path = os.environ["PATH"]

    def tearDown(self):
        os.environ["PATH"] = self._path

    def test_parses_a_successful_critic_run(self):
        fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + CANNED + "'")
        r = runner.run_one("claude-opus-5", self.brief, "change", 30, "opus", self.tmp)
        self.assertEqual(r["verdict"], "fail")
        self.assertFalse(r["degraded"])

    def test_sets_recursion_firewall_env(self):
        fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
        r = runner.run_one("claude-opus-5", self.brief, "change", 30, "opus", self.tmp)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(os.environ.get("CCA_INNER"), None,
                         "runner must not leak CCA_INNER into the parent env")

    def test_timeout_is_warn_never_fail(self):
        fake_claude(self.tmp, "sleep 5")
        r = runner.run_one("claude-opus-5", self.brief, "change", 1, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_nonzero_exit_is_warn_never_fail(self):
        fake_claude(self.tmp, "exit 3")
        r = runner.run_one("claude-opus-5", self.brief, "change", 10, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_missing_claude_binary_is_warn_never_fail(self):
        os.environ["PATH"] = "/nonexistent"
        r = runner.run_one("claude-opus-5", self.brief, "change", 10, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])


class TestRunAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.brief = os.path.join(self.tmp, "brief.md")
        with open(self.brief, "w") as fh:
            fh.write("critic")
        self._path = os.environ["PATH"]

    def tearDown(self):
        os.environ["PATH"] = self._path

    def test_runs_every_critic_and_labels_results(self):
        fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
        critics = [
            {"name": "design", "model": "claude-opus-5", "brief": self.brief, "timeout": 30},
            {"name": "correctness", "model": "claude-fable-5-1", "brief": self.brief, "timeout": 30},
        ]
        results = runner.run_all(critics, "change", self.tmp)
        self.assertEqual({r["critic"] for r in results}, {"design", "correctness"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_runner -v`
Expected: FAIL — `ImportError: cannot import name 'runner'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/runner.py
"""Spawn headless Claude critics in parallel.

Every failure path degrades to `warn`. A critic must never be able to block
the coder by being slow, broken, or absent.
"""
import concurrent.futures
import os
import subprocess
from typing import Any, Dict, List

from . import verdict as verdict_mod

DEFAULT_TOOLS = ["Read", "Grep", "Glob"]


def build_command(model, brief_path, allowed_tools):
    # type: (str, str, List[str]) -> List[str]
    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt-file", brief_path,
        "--permission-mode", "dontAsk",
        "--allowedTools",
    ]
    cmd.extend(allowed_tools)
    return cmd


def _degraded(critic, reason):
    # type: (str, str) -> Dict[str, Any]
    return {
        "critic": critic, "stated": "", "verdict": "warn", "degraded": True,
        "findings": [{
            "severity": "minor", "kind": "correctness",
            "file": "(critic)", "line": 0,
            "issue": "Critic did not complete: %s" % reason,
            "evidence": "", "suggestion": "",
        }],
    }


def run_one(model, brief_path, payload, timeout, critic_name, cwd):
    # type: (str, str, str, int, str, str) -> Dict[str, Any]
    env = os.environ.copy()
    env["CCA_INNER"] = "1"  # recursion firewall — set on the CHILD only
    cmd = build_command(model, brief_path, DEFAULT_TOOLS)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=cwd, universal_newlines=True,
        )
    except OSError as exc:
        return _degraded(critic_name, "cannot launch claude (%s)" % exc)

    try:
        out, _err = proc.communicate(input=payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return _degraded(critic_name, "timed out after %ss" % timeout)

    if proc.returncode != 0:
        return _degraded(critic_name, "exit code %s" % proc.returncode)

    return verdict_mod.parse(out, critic_name)


def run_all(critics, payload, cwd):
    # type: (List[Dict[str, Any]], str, str) -> List[Dict[str, Any]]
    if not critics:
        return []
    results = []  # type: List[Dict[str, Any]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(critics)) as pool:
        futures = [
            pool.submit(run_one, c["model"], c["brief"], payload,
                        c.get("timeout", 120), c["name"], cwd)
            for c in critics
        ]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # a runner bug must not block the coder
                results.append(_degraded("unknown", "runner error: %s" % exc))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_runner -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 5: Config and the change payload

**Files:**
- Create: `scripts/ccalib/config.py`
- Create: `scripts/ccalib/payload.py`
- Test: `tests/test_config.py`, `tests/test_payload.py`

**Interfaces:**
- Consumes: nothing
- Produces: `config.load(root) -> Dict`, `config.DEFAULTS`, `config.should_review(path, cfg) -> bool`, `config.critics_for(path, cfg, plugin_root) -> List[Dict]`; `payload.build(tool_name, tool_input, root) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import config


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_config_returns_defaults(self):
        cfg = config.load(self.tmp)
        self.assertEqual(cfg["critics"][0]["model"], "claude-opus-5")
        self.assertEqual(cfg["critics"][1]["model"], "claude-fable-5-1")

    def test_user_config_overrides_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"critics": [{"name": "solo", "model": "claude-haiku-4-5",
                                    "brief": "correctness", "timeout": 30}]}, fh)
        cfg = config.load(self.tmp)
        self.assertEqual(len(cfg["critics"]), 1)
        self.assertEqual(cfg["critics"][0]["model"], "claude-haiku-4-5")

    def test_corrupt_config_falls_back_to_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            fh.write("{ not json")
        self.assertEqual(len(config.load(self.tmp)["critics"]), 2)


class TestShouldReview(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_source_files_are_reviewed(self):
        self.assertTrue(config.should_review("src/app.ts", self.cfg))

    def test_ignored_globs_are_skipped(self):
        for path in ["node_modules/x/index.js", ".cca/events.jsonl",
                     "CF.md", "dist/bundle.js", "package-lock.json"]:
            self.assertFalse(config.should_review(path, self.cfg), path)


class TestCriticsFor(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_normal_path_gets_the_two_standing_critics(self):
        names = [c["name"] for c in config.critics_for("src/a.ts", self.cfg, "/plugin")]
        self.assertEqual(names, ["design", "correctness"])

    def test_migration_path_adds_the_schema_critic(self):
        names = [c["name"] for c in
                 config.critics_for("src/migrations/1712-add-col.ts", self.cfg, "/plugin")]
        self.assertIn("schema", names)

    def test_brief_paths_are_absolute_under_plugin_root(self):
        critics = config.critics_for("src/a.ts", self.cfg, "/plugin")
        self.assertEqual(critics[0]["brief"], "/plugin/critics/design.md")


if __name__ == "__main__":
    unittest.main()
```

```python
# tests/test_payload.py
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import payload


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_edit_payload_contains_before_and_after(self):
        text = payload.build("Edit", {"file_path": "a.ts", "old_string": "OLD",
                                      "new_string": "NEW"}, self.tmp)
        self.assertIn("OLD", text)
        self.assertIn("NEW", text)
        self.assertIn("a.ts", text)

    def test_write_payload_contains_content(self):
        text = payload.build("Write", {"file_path": "b.ts", "content": "BODY"}, self.tmp)
        self.assertIn("BODY", text)

    def test_payload_includes_project_conventions_when_present(self):
        with open(os.path.join(self.tmp, "CLAUDE.md"), "w") as fh:
            fh.write("never use any")
        text = payload.build("Write", {"file_path": "b.ts", "content": "x"}, self.tmp)
        self.assertIn("never use any", text)

    def test_payload_includes_task_spec_when_present(self):
        with open(os.path.join(self.tmp, "PROMPT.md"), "w") as fh:
            fh.write("build the widget")
        text = payload.build("Write", {"file_path": "b.ts", "content": "x"}, self.tmp)
        self.assertIn("build the widget", text)

    def test_missing_context_files_do_not_raise(self):
        self.assertIn("b.ts", payload.build("Write", {"file_path": "b.ts",
                                                      "content": "x"}, self.tmp))

    def test_huge_content_is_truncated(self):
        text = payload.build("Write", {"file_path": "b.ts", "content": "x" * 200000},
                             self.tmp)
        self.assertLess(len(text), 100000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_config tests.test_payload -v`
Expected: FAIL — `ImportError: cannot import name 'config'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/config.py
"""Configuration with defaults. The critic roster is data, never a constant —
swapping Fable for Haiku must be a one-line change."""
import fnmatch
import json
import os
from typing import Any, Dict, List

DEFAULTS = {
    "critics": [
        {"name": "design", "model": "claude-opus-5", "brief": "design", "timeout": 120},
        {"name": "correctness", "model": "claude-fable-5-1", "brief": "correctness", "timeout": 120},
    ],
    "path_critics": [
        {"globs": ["*migrations/*", "*migration/*", "*schema/*", "*entities/*", "*models/*"],
         "critic": {"name": "schema", "model": "claude-opus-5", "brief": "schema", "timeout": 120}}
    ],
    "ignore_globs": [
        "*/node_modules/*", "node_modules/*", ".cca/*", "*/.cca/*", "CF.md",
        "*/dist/*", "dist/*", "*/build/*", "build/*", "*.lock", "*lock.json",
        "*/.git/*", "*.min.js", "*/__pycache__/*",
    ],
}  # type: Dict[str, Any]


def load(root):
    # type: (str) -> Dict[str, Any]
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    path = os.path.join(root, ".cca", "config.json")
    if not os.path.exists(path):
        return cfg
    try:
        with open(path) as fh:
            user = json.load(fh)
    except (ValueError, IOError):
        return cfg  # a broken config must not disable the plugin
    if isinstance(user, dict):
        cfg.update(user)
    return cfg


def should_review(path, cfg):
    # type: (str, Dict[str, Any]) -> bool
    normalised = path.replace(os.sep, "/")
    for glob in cfg.get("ignore_globs", []):
        if fnmatch.fnmatch(normalised, glob) or fnmatch.fnmatch("/" + normalised, glob):
            return False
    return True


def critics_for(path, cfg, plugin_root):
    # type: (str, Dict[str, Any], str) -> List[Dict[str, Any]]
    chosen = list(cfg.get("critics", []))
    normalised = path.replace(os.sep, "/")
    for rule in cfg.get("path_critics", []):
        if any(fnmatch.fnmatch(normalised, g) for g in rule.get("globs", [])):
            chosen.append(rule["critic"])
    resolved = []  # type: List[Dict[str, Any]]
    for critic in chosen:
        item = dict(critic)
        item["brief"] = os.path.join(plugin_root, "critics", "%s.md" % critic["brief"])
        resolved.append(item)
    return resolved
```

```python
# scripts/ccalib/payload.py
"""Build the change payload a critic reviews.

Critics see intent, not just the diff — the task spec and the project's own
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_config tests.test_payload -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 6: The CF.md feedback queue

**Files:**
- Create: `scripts/ccalib/feedback.py`
- Test: `tests/test_feedback.py`

**Interfaces:**
- Consumes: finding dicts
- Produces: `append(findings, file_path, root) -> None`, `open_items(root) -> List[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_feedback.py
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import feedback


def F(issue="something wrong"):
    return {"severity": "minor", "kind": "reuse", "file": "a.ts", "line": 1,
            "issue": issue, "evidence": "lib/x.ts:1", "suggestion": "reuse it"}


class TestFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_append_creates_cf_md(self):
        feedback.append([F()], "a.ts", self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "CF.md")))

    def test_each_entry_is_a_heading_plus_findings(self):
        feedback.append([F("first")], "a.ts", self.tmp)
        with open(os.path.join(self.tmp, "CF.md")) as fh:
            body = fh.read()
        self.assertIn("## ", body)
        self.assertIn("first", body)

    def test_open_items_counts_headings(self):
        feedback.append([F()], "a.ts", self.tmp)
        feedback.append([F()], "b.ts", self.tmp)
        self.assertEqual(len(feedback.open_items(self.tmp)), 2)

    def test_wontfix_items_are_not_open(self):
        feedback.append([F()], "a.ts", self.tmp)
        path = os.path.join(self.tmp, "CF.md")
        with open(path) as fh:
            body = fh.read()
        with open(path, "w") as fh:
            fh.write(body.replace("## ", "## [wontfix] ", 1))
        self.assertEqual(feedback.open_items(self.tmp), [])

    def test_missing_file_has_no_open_items(self):
        self.assertEqual(feedback.open_items(self.tmp), [])

    def test_appending_empty_findings_writes_nothing(self):
        feedback.append([], "a.ts", self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CF.md")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_feedback -v`
Expected: FAIL — `ImportError: cannot import name 'feedback'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/feedback.py
"""CF.md — the queue and the arbitration surface.

Delete a heading to overrule a critic. Mark it `## [wontfix] ...` to close it
with a justification the arbiter can see later.
"""
import datetime
import os
from typing import Any, Dict, List

HEADER = (
    "# Critic Feedback (CF.md)\n\n"
    "Open items block the session from finishing. To arbitrate: fix the item and\n"
    "delete its heading, or rename the heading to `## [wontfix] ...` with a reason.\n"
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
        fh.write("\n## %s — %s\n" % (stamp, file_path))
        for f in findings:
            fh.write("- **%s / %s** `%s:%s` — %s\n" % (
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
    return [
        ln for ln in lines
        if ln.startswith("## ") and "[wontfix]" not in ln.lower()
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_feedback -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 7: Hook entrypoints

**Files:**
- Create: `scripts/critic_hook.py`
- Create: `scripts/drain_hook.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: every `ccalib` module
- Produces: two executables reading hook JSON on stdin, writing hook JSON on stdout, exiting 0 unless deliberately blocking

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks.py
import json, os, stat, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CRITIC = os.path.join(ROOT, "scripts", "critic_hook.py")
DRAIN = os.path.join(ROOT, "scripts", "drain_hook.py")


def fake_claude(tmpdir, body):
    path = os.path.join(tmpdir, "claude")
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return tmpdir


def run(script, event, cwd, extra_env=None):
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = cwd
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestCriticHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _event(self, path="src/a.ts"):
        return {"tool_name": "Edit", "cwd": self.tmp,
                "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}

    def test_recursion_firewall_exits_immediately(self):
        code, out, _ = run(CRITIC, self._event(), self.tmp, {"CCA_INNER": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".cca", "events.jsonl")))

    def test_ignored_path_is_skipped(self):
        code, out, _ = run(CRITIC, self._event("node_modules/x/i.js"), self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_all_pass_is_silent_and_exit_zero(self):
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        code, out, _ = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_consensus_fail_emits_block_decision(self):
        canned = ('{"verdict":"fail","findings":[{"severity":"major",'
                  '"kind":"correctness","file":"src/a.ts","line":1,'
                  '"issue":"Null deref on empty list","evidence":"","suggestion":"guard"}]}')
        env = {"PATH": fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + canned + "'")
                       + os.pathsep + os.environ["PATH"]}
        code, out, _ = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_malformed_stdin_never_blocks_the_coder(self):
        proc = subprocess.Popen([sys.executable, CRITIC], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=self.tmp, universal_newlines=True)
        out, _ = proc.communicate("this is not json", timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out.strip(), "")

    def test_events_are_written(self):
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        run(CRITIC, self._event(), self.tmp, env)
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            kinds = [json.loads(l)["type"] for l in fh if l.strip()]
        self.assertIn("edit_started", kinds)
        self.assertIn("critic_verdict", kinds)
        self.assertIn("vote", kinds)


class TestDrainHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_no_cf_file_allows_stop(self):
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_open_items_block_stop(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("# Critic Feedback\n\n## 2026-09-14 10:00:00 — a.ts\n- x\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_hook_active_never_loops(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 10:00:00 — a.ts\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": True}, self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_wontfix_items_allow_stop(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("## [wontfix] 2026-09-14 — a.ts\nreason: intentional\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_hooks -v`
Expected: FAIL — scripts do not exist

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# scripts/critic_hook.py
"""PostToolUse hook. Fails open: any internal error exits 0 silently."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    # The recursion firewall comes before everything else. Critics are
    # `claude -p` processes inside this project; without this they load this
    # very hook and spawn critics of their own.
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import config, events, feedback, payload, runner, vote

    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except ValueError:
        return 0

    tool_name = event.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return 0

    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return 0

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = config.load(root)

    rel = os.path.relpath(file_path, root) if os.path.isabs(file_path) else file_path
    if not config.should_review(rel, cfg):
        return 0

    events.append("edit_started", {"file": rel, "tool": tool_name}, root)

    critics = config.critics_for(rel, cfg, PLUGIN_ROOT)
    for critic in critics:
        events.append("critic_dispatched",
                      {"critic": critic["name"], "model": critic["model"], "file": rel}, root)

    body = payload.build(tool_name, tool_input, root)
    results = runner.run_all(critics, body, root)

    for result in results:
        events.append("critic_verdict", {
            "critic": result["critic"], "verdict": result["verdict"],
            "stated": result.get("stated", ""), "degraded": result.get("degraded", False),
            "findings": result.get("findings", []), "file": rel,
        }, root)

    decision = vote.decide(results, [])
    events.append("vote", {"decision": decision["decision"], "file": rel,
                           "finding_count": len(decision["findings"])}, root)

    if decision["decision"] == "block":
        events.append("blocked", {"file": rel}, root)
        print(json.dumps({
            "decision": "block",
            "reason": "Critics rejected the change to %s:\n%s" % (rel, decision["reason"]),
        }))
    elif decision["decision"] == "queue":
        feedback.append(decision["findings"], rel, root)
        events.append("queued", {"file": rel, "count": len(decision["findings"])}, root)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Critics queued %d non-blocking finding(s) on %s in CF.md."
                                 % (len(decision["findings"]), rel),
        }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a critic bug must never stop the user editing
        sys.exit(0)
```

```python
#!/usr/bin/env python3
# scripts/drain_hook.py
"""Stop hook. Refuses to finish while CF.md has open items."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import events, feedback

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return 0

    if event.get("stop_hook_active"):
        return 0  # already forced one continuation — do not loop

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    items = feedback.open_items(root)
    if not items:
        return 0

    events.append("drain_blocked", {"count": len(items)}, root)
    listing = "\n".join(items[:20])
    print(json.dumps({
        "decision": "block",
        "reason": ("CF.md still has %d open critic item(s). Address each one, then "
                   "delete its heading from CF.md — or mark it '## [wontfix] ...' "
                   "with a one-line justification for the arbiter.\n\n%s"
                   % (len(items), listing)),
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_hooks -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Verify the whole suite**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass, ~59 total

---

### Task 8: Plugin manifest and hook wiring

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`
- Create: `.gitignore`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `scripts/critic_hook.py`, `scripts/drain_hook.py`
- Produces: an installable plugin

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest.py
import json, os, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestManifest(unittest.TestCase):
    def test_plugin_json_is_valid_and_named(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["name"], "cca")
        self.assertTrue(manifest["description"].strip())
        self.assertTrue(manifest["version"].strip())

    def test_hooks_json_wires_both_entrypoints(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        post = hooks["PostToolUse"][0]
        self.assertEqual(post["matcher"], "Edit|Write")
        self.assertIn("critic_hook.py", post["hooks"][0]["command"])
        self.assertIn("drain_hook.py", hooks["Stop"][0]["hooks"][0]["command"])

    def test_hooks_use_the_stable_command_type(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        for event in hooks.values():
            for group in event:
                for hook in group["hooks"]:
                    self.assertEqual(hook["type"], "command")

    def test_hook_commands_reference_plugin_root(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            body = fh.read()
        self.assertIn("CLAUDE_PLUGIN_ROOT", body)

    def test_referenced_scripts_exist(self):
        for script in ("critic_hook.py", "drain_hook.py"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", script)), script)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_manifest -v`
Expected: FAIL — `FileNotFoundError: .claude-plugin/plugin.json`

- [ ] **Step 3: Write minimal implementation**

```json
// .claude-plugin/plugin.json
{
  "name": "cca",
  "description": "Coder / Critic / Arbiter — every edit reviewed by two fresh Claude critics on different models, with a human arbiter",
  "version": "0.1.0",
  "license": "MIT",
  "keywords": ["review", "critic", "ensemble", "hooks"]
}
```

```json
// hooks/hooks.json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/critic_hook.py\"",
            "timeout": 300
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/drain_hook.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

```gitignore
# .gitignore
.cca/
CF.md
__pycache__/
*.pyc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_manifest -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 9: Critic briefs

The briefs are prompts, so they are verified by the planted-defect suite in Task 10 rather than by unit tests. What *is* unit-testable is that every brief exists and states the contract the parser enforces — a brief that forgets to demand JSON produces a permanently degraded critic.

**Files:**
- Create: `critics/design.md`
- Create: `critics/correctness.md`
- Create: `critics/schema.md`
- Test: `tests/test_briefs.py`

**Interfaces:**
- Consumes: nothing
- Produces: three system-prompt files referenced by `config.critics_for`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_briefs.py
import os, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
BRIEFS = ("design", "correctness", "schema")


class TestBriefs(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(ROOT, "critics", "%s.md" % name)) as fh:
            return fh.read()

    def test_all_briefs_exist(self):
        for name in BRIEFS:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "critics", "%s.md" % name)), name)

    def test_every_brief_demands_json_only(self):
        for name in BRIEFS:
            self.assertIn("JSON", self._read(name), name)

    def test_every_brief_forbids_writing_files(self):
        for name in BRIEFS:
            self.assertIn("never", self._read(name).lower(), name)

    def test_every_brief_states_the_evidence_requirement(self):
        for name in BRIEFS:
            self.assertIn("evidence", self._read(name).lower(), name)

    def test_briefs_forbid_hedging(self):
        for name in BRIEFS:
            self.assertIn("consider", self._read(name).lower(), name)

    def test_design_brief_requires_grep_before_claiming_reuse(self):
        self.assertIn("Grep", self._read("design"))

    def test_correctness_brief_calibrates_complexity_to_data_size(self):
        body = self._read("correctness").lower()
        self.assertIn("data size", body)

    def test_schema_brief_covers_production_drift(self):
        body = self._read("schema").lower()
        for term in ("existing rows", "rollback", "cascade"):
            self.assertIn(term, body, term)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_briefs -v`
Expected: FAIL — `FileNotFoundError: critics/design.md`

- [ ] **Step 3: Write the three briefs**

`critics/design.md`:

```markdown
You are the Reuse & Design critic in a Coder/Critic/Arbiter loop. You review one change at a time.

You NEVER write, edit or create files. You only read, search and report.

# Your single question: does this already exist, and is it in the right shape?

Check, in this order:

1. **Reinvention.** Before claiming anything is reinvented you MUST search the repository with Grep
   and Glob. A reuse finding is only valid if you can name the existing symbol and the file and line
   it lives at. If you did not find it, it is not a finding.
2. **Decomposition.** One unit doing several unrelated jobs. Be concrete about which jobs.
3. **Scope creep.** Anything the change adds that the task spec did not ask for.
4. **Project conventions.** The payload contains the project's own conventions. Enforce THOSE.
   Do not invent generic best practices, and do not contradict the project's stated preferences.

# Calibration

- Two blocks that merely look similar are not a DRY violation. Code that changes for different
  reasons should stay separate. Only flag duplication that would have to change in lockstep.
- Do not flag naming, formatting, comments, import order or anything a formatter owns.
- Do not propose refactors unrelated to this change.

# Output contract

Reply with JSON and nothing else. No preamble, no explanation, no code fences.

{"verdict":"pass|warn|fail","findings":[{"severity":"critical|major|minor","kind":"reuse|decomposition|scope|convention","file":"path","line":0,"issue":"what is wrong","evidence":"file:line of the thing that already exists","suggestion":"what to do instead"}]}

Rules that will cause your finding to be DISCARDED by the harness:
- `reuse` findings with an empty `evidence` field cannot block anything.
- Any issue containing "consider", "might want to", "would be cleaner", or "perhaps" is dropped
  as unactionable. State what is wrong, not what might be nicer.
- Findings without a `file` are dropped.
- `kind` values other than reuse, decomposition, scope, convention are dropped.

If the change is fine, return {"verdict":"pass","findings":[]}. Returning no findings is a
successful review, not a failed one.
```

`critics/correctness.md`:

```markdown
You are the Correctness & Complexity critic in a Coder/Critic/Arbiter loop. You review one change
at a time.

You NEVER write, edit or create files. You only read, search and report.

# Your single question: is this wrong?

Check, in this order:

1. **Bugs.** Off-by-one, null and undefined handling, wrong operator, inverted condition,
   unhandled promise rejection, resource left open.
2. **Edge cases.** Empty collection, single element, duplicate keys, missing optional field,
   boundary values, unicode, timezone.
3. **Error paths.** Swallowed exceptions, errors logged and continued, failure states that leave
   data half-written.
4. **Complexity that is wrong for the data size.** A query inside a loop over records. Repeated
   passes over the same collection. Unbounded growth. State the data size it breaks at.
5. **Concurrency and ordering.** Races, non-atomic read-modify-write, assumed ordering.

# Calibration

Complexity is judged against the expected data size, never against theoretical optimality.
O(n^2) over a five-item config array is correct code — do not flag it. An N+1 query across
suppliers is a real finding. If you cannot say what data size the code breaks at, it is not a
complexity finding.

Do not flag style, naming, formatting, or structure — another critic owns those.

# Output contract

Reply with JSON and nothing else. No preamble, no explanation, no code fences.

{"verdict":"pass|warn|fail","findings":[{"severity":"critical|major|minor","kind":"correctness|complexity","file":"path","line":0,"issue":"what breaks and when","evidence":"for complexity: the data size at which this breaks","suggestion":"the fix"}]}

Rules that will cause your finding to be DISCARDED by the harness:
- `complexity` findings with an empty `evidence` field cannot block anything.
- Any issue containing "consider", "might want to", "would be cleaner", or "perhaps" is dropped.
- Findings without a `file` are dropped.

If the change is correct, return {"verdict":"pass","findings":[]}. Returning no findings is a
successful review, not a failed one.
```

`critics/schema.md`:

```markdown
You are the Schema & Migration critic in a Coder/Critic/Arbiter loop. You only see changes to
migrations, schemas and entity definitions, because those fail differently from ordinary code:
rarely, catastrophically, and in production rather than in development.

You NEVER write, edit or create files. You only read, search and report.

# Your single question: what breaks in production that did not break locally?

1. **Existing rows.** A new NOT NULL column with no default, a narrowed type, a new unique
   constraint — all of these pass on an empty dev database and fail on a populated one.
2. **Backwards compatibility.** The currently deployed code runs against this schema during the
   deploy window. A dropped or renamed column breaks it before the new code ships.
3. **Rollback.** Can this migration be reversed without data loss? If not, say so explicitly.
4. **Locks.** Operations that rewrite or exclusively lock a large table.
5. **House rules.** Prefer `text` over `varchar`. Prefer enums over free strings. Prefer soft
   delete. Never `ON DELETE CASCADE`.

# Output contract

Reply with JSON and nothing else. No preamble, no explanation, no code fences.

{"verdict":"pass|warn|fail","findings":[{"severity":"critical|major|minor","kind":"schema","file":"path","line":0,"issue":"what breaks","evidence":"the condition that triggers it, e.g. 'any existing row'","suggestion":"the safe form"}]}

Any issue containing "consider", "might want to", "would be cleaner", or "perhaps" is dropped by
the harness as unactionable. Findings without a `file` are dropped.

If the migration is safe, return {"verdict":"pass","findings":[]}.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_briefs -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 10: Planted-defect project and eval harness

The negative control is the point. A defect set containing only real defects can be passed by a
critic that flags everything, and over-flagging is the failure mode most likely to cause abandonment.

**Files:**
- Create: `test-project/src/format.ts`, `test-project/src/pricing.ts`, `test-project/src/suppliers.ts`, `test-project/src/report.ts`, `test-project/src/tidy.ts`
- Create: `test-project/src/migrations/1712-add-status.ts`
- Create: `test-project/PROMPT.md`, `test-project/CLAUDE.md`
- Create: `scripts/cca_eval.py`
- Test: `tests/test_eval.py`

**Interfaces:**
- Consumes: `ccalib.runner`, `ccalib.config`
- Produces: `eval.CASES: List[Dict]`, `eval.run_case(case, plugin_root) -> Dict`, `eval.summarise(results) -> Dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cca_eval as evalmod

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestCases(unittest.TestCase):
    def test_every_case_file_exists(self):
        for case in evalmod.CASES:
            path = os.path.join(ROOT, "test-project", case["file"])
            self.assertTrue(os.path.exists(path), case["file"])

    def test_there_is_a_negative_control(self):
        controls = [c for c in evalmod.CASES if c["expect"] == "clean"]
        self.assertEqual(len(controls), 1)

    def test_every_defect_names_its_expected_catcher(self):
        for case in evalmod.CASES:
            if case["expect"] == "defect":
                self.assertIn(case["critic"], ("design", "correctness", "schema"), case["id"])
                self.assertTrue(case["kind"])

    def test_all_five_planted_defect_classes_are_present(self):
        kinds = {c["kind"] for c in evalmod.CASES if c["expect"] == "defect"}
        self.assertEqual(kinds, {"reuse", "complexity", "schema", "convention", "decomposition"})


class TestSummarise(unittest.TestCase):
    def test_counts_catches_and_false_positives(self):
        results = [
            {"id": "a", "expect": "defect", "caught": True},
            {"id": "b", "expect": "defect", "caught": False},
            {"id": "c", "expect": "clean", "caught": True},
        ]
        s = evalmod.summarise(results)
        self.assertEqual(s["caught"], 1)
        self.assertEqual(s["missed"], 1)
        self.assertEqual(s["false_positives"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_eval -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval'`

- [ ] **Step 3: Create the planted-defect project**

`test-project/CLAUDE.md`:

```markdown
# Project conventions

- TypeScript: never `any`. Prefer precise interfaces.
- Reuse shared helpers from `src/format.ts` rather than redefining them.
- DB: prefer `text` over `varchar`, enums over free strings, soft delete, no `ON DELETE CASCADE`.
```

`test-project/PROMPT.md`:

```markdown
# Task

Add supplier quote reporting: format quote totals for display, fetch each supplier's quotes,
and render a summary report.
```

`test-project/src/format.ts` — the helper that already exists:

```typescript
export function formatCurrency(amount: number, currency: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(amount);
}
```

`test-project/src/pricing.ts` — **planted: reuse** (design critic):

```typescript
// Reinvents formatCurrency from src/format.ts:1
export function renderPrice(amount: number, currency: string): string {
  const symbol = currency === "GBP" ? "£" : currency === "EUR" ? "€" : "$";
  return symbol + amount.toFixed(2);
}
```

`test-project/src/suppliers.ts` — **planted: complexity** (correctness critic):

```typescript
import { db } from "./db";

// N+1: one query per supplier across the whole supplier table
export async function loadQuotes(supplierIds: string[]) {
  const quotes = [];
  for (const id of supplierIds) {
    const rows = await db.query("SELECT * FROM quotes WHERE supplier_id = $1", [id]);
    quotes.push(...rows);
  }
  return quotes;
}
```

`test-project/src/report.ts` — **planted: convention (`any`) and decomposition**:

```typescript
// One function doing four jobs: fetching, validating, formatting and writing.
export async function buildReport(input: any): Promise<any> {
  const raw = await fetch(input.url).then((r) => r.json());
  if (!raw || !raw.items) throw new Error("bad payload");
  const rows: string[] = [];
  for (const item of raw.items) {
    if (item.status !== "active") continue;
    const price = item.currency === "GBP" ? "£" + item.total : "$" + item.total;
    rows.push(`${item.name}\t${price}\t${item.updatedAt}`);
  }
  const header = "name\tprice\tupdated";
  const body = [header, ...rows].join("\n");
  await fetch(input.sink, { method: "POST", body });
  return { written: rows.length, body };
}
```

`test-project/src/migrations/1712-add-status.ts` — **planted: schema**:

```typescript
export async function up(queryRunner: any): Promise<void> {
  // NOT NULL with no default against a populated table, varchar over text,
  // and a cascading delete.
  await queryRunner.query(`ALTER TABLE quotes ADD COLUMN status varchar(32) NOT NULL`);
  await queryRunner.query(
    `ALTER TABLE quotes ADD CONSTRAINT fk_supplier FOREIGN KEY (supplier_id)
     REFERENCES suppliers(id) ON DELETE CASCADE`
  );
}
```

`test-project/src/tidy.ts` — **the negative control. Nothing should flag this:**

```typescript
import { formatCurrency } from "./format";

export interface QuoteLine {
  name: string;
  total: number;
  currency: string;
}

// Correct, reuses the shared helper, linear over a small array.
// Slightly verbose, but nothing here is a defect.
export function summarise(lines: QuoteLine[]): string {
  const parts: string[] = [];
  for (const line of lines) {
    const price = formatCurrency(line.total, line.currency);
    parts.push(line.name + ": " + price);
  }
  return parts.join(", ");
}
```

- [ ] **Step 4: Write the eval harness**

```python
#!/usr/bin/env python3
# scripts/cca_eval.py
"""Run every critic against the planted-defect project and report catch rate.

Usage: python3 scripts/cca_eval.py
This spends real model calls. It is the measurement, not a unit test.
"""
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ccalib import config, payload, runner  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.join(PLUGIN_ROOT, "test-project")

CASES = [
    {"id": "reuse", "file": "src/pricing.ts", "expect": "defect",
     "critic": "design", "kind": "reuse"},
    {"id": "n_plus_one", "file": "src/suppliers.ts", "expect": "defect",
     "critic": "correctness", "kind": "complexity"},
    {"id": "any_type", "file": "src/report.ts", "expect": "defect",
     "critic": "design", "kind": "convention"},
    {"id": "god_function", "file": "src/report.ts", "expect": "defect",
     "critic": "design", "kind": "decomposition"},
    {"id": "migration", "file": "src/migrations/1712-add-status.ts", "expect": "defect",
     "critic": "schema", "kind": "schema"},
    {"id": "negative_control", "file": "src/tidy.ts", "expect": "clean",
     "critic": "", "kind": ""},
]  # type: List[Dict[str, Any]]


def run_case(case, plugin_root):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    path = os.path.join(PROJECT, case["file"])
    with open(path) as fh:
        content = fh.read()

    cfg = config.load(PROJECT)
    critics = config.critics_for(case["file"], cfg, plugin_root)
    body = payload.build("Write", {"file_path": case["file"], "content": content}, PROJECT)
    results = runner.run_all(critics, body, PROJECT)

    findings = [f for r in results for f in r.get("findings", [])]
    if case["expect"] == "clean":
        caught = bool(findings)  # for the control, any finding is a false positive
    else:
        caught = any(f.get("kind") == case["kind"] for f in findings)

    return {"id": case["id"], "expect": case["expect"], "caught": caught,
            "findings": findings,
            "verdicts": {r["critic"]: r["verdict"] for r in results}}


def summarise(results):
    # type: (List[Dict[str, Any]]) -> Dict[str, int]
    defects = [r for r in results if r["expect"] == "defect"]
    controls = [r for r in results if r["expect"] == "clean"]
    return {
        "caught": sum(1 for r in defects if r["caught"]),
        "missed": sum(1 for r in defects if not r["caught"]),
        "false_positives": sum(1 for r in controls if r["caught"]),
    }


def main():
    results = [run_case(c, PLUGIN_ROOT) for c in CASES]
    summary = summarise(results)
    for r in results:
        label = "OK  " if (r["caught"] == (r["expect"] == "defect")) else "MISS"
        print("%s %-18s verdicts=%s findings=%d"
              % (label, r["id"], r["verdicts"], len(r["findings"])))
    print("\ncaught=%(caught)d missed=%(missed)d false_positives=%(false_positives)d" % summary)
    out = os.path.join(PROJECT, "eval-results.json")
    with open(out, "w") as fh:
        json.dump({"results": results, "summary": summary}, fh, indent=2)
    print("written: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_eval -v`
Expected: PASS, 5 tests

- [ ] **Step 6: Verify the whole suite**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass

---

### Task 11: Live dashboard

**Files:**
- Create: `dashboard/index.html`
- Create: `scripts/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `.cca/events.jsonl`
- Produces: `dashboard.stats(events) -> Dict` (the tuning numbers), and a static page

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dashboard

ROOT = os.path.join(os.path.dirname(__file__), "..")


def E(kind, payload):
    return {"type": kind, "ts": 1.0, "payload": payload}


class TestStats(unittest.TestCase):
    def test_counts_edits_blocks_and_queues(self):
        events = [E("edit_started", {"file": "a"}), E("vote", {"decision": "block"}),
                  E("edit_started", {"file": "b"}), E("vote", {"decision": "queue"})]
        s = dashboard.stats(events)
        self.assertEqual(s["edits"], 2)
        self.assertEqual(s["blocked"], 1)
        self.assertEqual(s["queued"], 1)

    def test_agreement_rate_when_critics_agree(self):
        events = [
            E("critic_verdict", {"critic": "design", "verdict": "pass", "file": "a"}),
            E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a"}),
        ]
        self.assertEqual(dashboard.stats(events)["agreement_rate"], 1.0)

    def test_agreement_rate_when_critics_disagree(self):
        events = [
            E("critic_verdict", {"critic": "design", "verdict": "fail", "file": "a"}),
            E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a"}),
        ]
        self.assertEqual(dashboard.stats(events)["agreement_rate"], 0.0)

    def test_unique_findings_per_critic_shows_who_earns_their_keep(self):
        events = [
            E("critic_verdict", {"critic": "design", "verdict": "fail", "file": "a",
                                 "findings": [{"kind": "reuse", "severity": "major"}]}),
            E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a",
                                 "findings": []}),
        ]
        s = dashboard.stats(events)
        self.assertEqual(s["findings_by_critic"]["design"], 1)
        self.assertEqual(s["findings_by_critic"]["correctness"], 0)

    def test_empty_log_does_not_divide_by_zero(self):
        s = dashboard.stats([])
        self.assertEqual(s["edits"], 0)
        self.assertEqual(s["agreement_rate"], None)


class TestPage(unittest.TestCase):
    def test_page_exists_and_polls_the_event_log(self):
        with open(os.path.join(ROOT, "dashboard", "index.html")) as fh:
            body = fh.read()
        self.assertIn("events.jsonl", body)
        self.assertIn("setInterval", body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_dashboard -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 3: Write the stats module and server**

```python
#!/usr/bin/env python3
# scripts/dashboard.py
"""Serve the CCA dashboard and compute the tuning statistics.

Usage: python3 scripts/dashboard.py [project_root] [port]
"""
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stats(events):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    edits = sum(1 for e in events if e["type"] == "edit_started")
    votes = [e["payload"].get("decision") for e in events if e["type"] == "vote"]

    per_file = {}  # type: Dict[str, List[str]]
    findings_by_critic = {}  # type: Dict[str, int]
    for e in events:
        if e["type"] != "critic_verdict":
            continue
        p = e["payload"]
        per_file.setdefault(p.get("file", ""), []).append(p.get("verdict", ""))
        name = p.get("critic", "?")
        findings_by_critic[name] = findings_by_critic.get(name, 0) + len(p.get("findings", []))

    compared = [v for v in per_file.values() if len(v) >= 2]
    agreement = None  # type: Optional[float]
    if compared:
        agreed = sum(1 for v in compared if len(set(v)) == 1)
        agreement = round(float(agreed) / len(compared), 3)

    return {
        "edits": edits,
        "blocked": votes.count("block"),
        "queued": votes.count("queue"),
        "passed": votes.count("pass"),
        "agreement_rate": agreement,
        "findings_by_critic": findings_by_critic,
    }


def serve(root, port):
    # type: (str, int) -> None
    import http.server
    import socketserver

    target = os.path.join(root, ".cca")
    os.makedirs(target, exist_ok=True)
    shutil.copyfile(os.path.join(PLUGIN_ROOT, "dashboard", "index.html"),
                    os.path.join(target, "index.html"))
    os.chdir(target)

    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print("CCA dashboard: http://127.0.0.1:%d/index.html" % port)
        httpd.serve_forever()


if __name__ == "__main__":
    project = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    listen_port = int(sys.argv[2]) if len(sys.argv) > 2 else 7878
    serve(project, listen_port)
```

`dashboard/index.html` — Ultra-style header, live timeline, tuning stats. Polls `events.jsonl`
every 500ms from the same directory it is served from.

```html
<title>CCA Ensemble</title>
<style>
  :root {
    --bg: #0f1115; --panel: #161a21; --line: #242a34; --fg: #e6e9ef;
    --dim: #8b94a7; --pass: #3fb950; --warn: #d29922; --fail: #f85149; --accent: #6ea8ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .wrap { padding: 16px; max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 15px; margin: 0 0 4px; letter-spacing: .04em; }
  .cast { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
          padding: 12px 14px; margin-bottom: 14px; }
  .cast div { margin: 2px 0; color: var(--dim); }
  .cast b { color: var(--fg); font-weight: 600; }
  .stats { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
  .stat { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
          padding: 10px 14px; min-width: 96px; }
  .stat .n { font-size: 20px; font-weight: 600; }
  .stat .k { color: var(--dim); font-size: 11px; text-transform: uppercase;
             letter-spacing: .06em; }
  .row { background: var(--panel); border: 1px solid var(--line); border-left-width: 3px;
         border-radius: 6px; padding: 8px 12px; margin-bottom: 6px; }
  .row.pass { border-left-color: var(--pass); }
  .row.warn { border-left-color: var(--warn); }
  .row.fail, .row.block { border-left-color: var(--fail); }
  .row .meta { color: var(--dim); font-size: 11px; }
  .f { margin-top: 6px; padding-left: 10px; border-left: 2px solid var(--line); }
  .sev { font-weight: 600; }
  .sev.critical, .sev.major { color: var(--fail); }
  .sev.minor { color: var(--warn); }
  .empty { color: var(--dim); padding: 24px 0; text-align: center; }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f6f7f9; --panel: #fff; --line: #e2e5ea; --fg: #1b1f27; --dim: #6b7280; }
  }
</style>

<div class="wrap">
  <h1>CCA ENSEMBLE</h1>
  <div class="cast">
    <div>coder: <b>claude-opus-5</b> &nbsp;·&nbsp; arbiter: <b>human</b></div>
    <div>critics: <b>claude-opus-5</b> (design) &nbsp; <b>claude-fable-5-1</b> (correctness)</div>
  </div>
  <div class="stats" id="stats"></div>
  <div id="feed"><div class="empty">waiting for the first edit…</div></div>
</div>

<script>
const CLASS_OF = { pass: "pass", warn: "warn", fail: "fail", block: "block", queue: "warn" };

function statTile(n, k) {
  return '<div class="stat"><div class="n">' + n + '</div><div class="k">' + k + '</div></div>';
}

function computeStats(events) {
  const votes = events.filter(e => e.type === "vote").map(e => e.payload.decision);
  const perFile = {};
  const byCritic = {};
  for (const e of events) {
    if (e.type !== "critic_verdict") continue;
    const p = e.payload;
    (perFile[p.file] = perFile[p.file] || []).push(p.verdict);
    byCritic[p.critic] = (byCritic[p.critic] || 0) + (p.findings ? p.findings.length : 0);
  }
  const compared = Object.values(perFile).filter(v => v.length >= 2);
  const agreed = compared.filter(v => new Set(v).size === 1).length;
  return {
    edits: events.filter(e => e.type === "edit_started").length,
    blocked: votes.filter(v => v === "block").length,
    queued: votes.filter(v => v === "queue").length,
    agreement: compared.length ? Math.round((agreed / compared.length) * 100) + "%" : "—",
    byCritic
  };
}

function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function render(events) {
  const s = computeStats(events);
  let tiles = statTile(s.edits, "edits") + statTile(s.blocked, "blocked")
            + statTile(s.queued, "queued") + statTile(s.agreement, "agreement");
  for (const [name, n] of Object.entries(s.byCritic)) tiles += statTile(n, name + " findings");
  document.getElementById("stats").innerHTML = tiles;

  const rows = [];
  for (const e of events.slice().reverse()) {
    if (e.type === "critic_verdict") {
      const p = e.payload;
      let html = '<div class="row ' + (CLASS_OF[p.verdict] || "") + '">'
        + '<b>' + esc(p.critic) + '</b> → ' + esc(p.verdict)
        + (p.degraded ? ' <span class="meta">(degraded)</span>' : '')
        + '<div class="meta">' + esc(p.file) + '</div>';
      for (const f of (p.findings || [])) {
        html += '<div class="f"><span class="sev ' + esc(f.severity) + '">'
          + esc(f.severity) + '/' + esc(f.kind) + '</span> '
          + esc(f.file) + ':' + esc(f.line) + ' — ' + esc(f.issue)
          + (f.evidence ? '<div class="meta">evidence: ' + esc(f.evidence) + '</div>' : '')
          + '</div>';
      }
      rows.push(html + '</div>');
    } else if (e.type === "vote") {
      rows.push('<div class="row ' + (CLASS_OF[e.payload.decision] || "") + '">VOTE → <b>'
        + esc(e.payload.decision) + '</b><div class="meta">' + esc(e.payload.file)
        + '</div></div>');
    } else if (e.type === "edit_started") {
      rows.push('<div class="row">edit: <b>' + esc(e.payload.file) + '</b></div>');
    }
  }
  document.getElementById("feed").innerHTML =
    rows.length ? rows.join("") : '<div class="empty">waiting for the first edit…</div>';
}

async function poll() {
  try {
    const res = await fetch("events.jsonl?t=" + Date.now());
    if (!res.ok) return;
    const text = await res.text();
    const events = [];
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try { events.push(JSON.parse(line)); } catch (_) { /* skip corrupt line */ }
    }
    render(events);
  } catch (_) { /* log not created yet */ }
}

poll();
setInterval(poll, 500);
</script>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_dashboard -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Verify the whole suite**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests -v`
Expected: all tests pass, ~70 total

---

## Deliberately deferred: Layer 0

The spec's Layer 0 (deterministic pre-pass — typecheck, duplicate-block scan, dead-export scan)
is **not implemented by any task in this plan**, and that is a decision rather than an oversight.

- **The seam exists and is tested.** `vote.decide(verdicts, layer0)` takes the layer-0 findings
  list as its second argument, and `tests/test_vote.py` covers both the blocking and non-blocking
  layer-0 paths. `critic_hook.py` passes `[]` today; wiring a producer in is a one-line change.
- **Why deferred:** the tools are stack-specific npm packages (`jscpd`, `knip`, `ts-prune`) while
  this plugin is deliberately Python-3.9-stdlib-only so it installs anywhere. Standardising them
  across TypeScript and Python is open question 1 in the spec and is not yet answered.
- **Consequence today:** duplicate blocks and dead exports are caught only if a model critic
  notices them, which is exactly the waste the layer was meant to remove. Cost per edit is
  therefore at the high end of the spec's estimate until this lands.

This is the first thing to build after the measurement pass below.

## After the plan

Once every task is green, the remaining work is measurement rather than construction:

1. Run `python3 scripts/cca_eval.py` against the planted-defect project. This spends real model calls.
2. Read `caught / missed / false_positives`. A false positive on the negative control is more
   important than a miss — tune the briefs down, not up.
3. Compare `findings_by_critic`. If the correctness critic contributes nothing the design critic
   missed, swap `claude-fable-5-1` for `claude-haiku-4-5` in `.cca/config.json` and re-run. That
   decision is now data, not a hunch.
