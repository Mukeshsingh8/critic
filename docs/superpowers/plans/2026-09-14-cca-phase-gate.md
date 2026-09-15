# CCA Phase Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Force a large plan to ship as a sequence of small, individually committed, reviewable increments — with the plugin tracking what shipped and what is next, across sessions.

**Architecture:** A `PreToolUse` fence blocks edits to files owned by *later* phases. A `Stop` gate ladder (tests → critics → commit → clean tree) refuses to finish a phase until it has shipped. Current phase is derived from `git log` — no stored state. Claude never commits; it prints the command.

**Tech Stack:** Python 3.9 stdlib only (`unittest`, `subprocess`, `re`), reusing the existing `ccalib` package.

**Spec:** [`docs/superpowers/specs/2026-09-14-cca-phase-gate-design.md`](../specs/2026-09-14-cca-phase-gate-design.md)

## Global Constraints

- **Python 3.9.6, stdlib only.** `unittest`, no pytest, no PEP 604 unions, no `match`.
- **Claude never runs `git add`, `git commit`, `git push`, or `git init` against a real project.**
  Tests create throwaway repos in `tempfile.mkdtemp()` only. Each task ends with verification, not a commit.
- **Fail open, without exception.** The fence is the most dangerous code here: a false deny stops all
  work. Every uncertain path ALLOWS the edit. A fence that cannot read the plan must never conclude
  "nothing is in scope."
- **`CCA_INNER=1` guard first** in every hook entrypoint, before any other work.
- **Phase tag format:** `[phase-N]` at the start of a commit subject. This is what the gate greps for.

---

### Task 1: Plan parser

**Files:**
- Create: `scripts/ccalib/planparse.py`
- Test: `tests/test_planparse.py`

**Interfaces:**
- Consumes: nothing
- Produces: `parse(text: str) -> List[Dict]` where each dict is
  `{"number": int, "title": str, "files": List[str], "start_line": int, "end_line": int}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_planparse.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import planparse

PLAN = """# Some Plan

Preamble that mentions Task 99 but is not a heading.

### Task 1: Event log

**Files:**
- Create: `scripts/ccalib/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Implement**

### Task 2: Verdict parsing

**Files:**
- Create: `scripts/ccalib/verdict.py`
- Modify: `scripts/critic_hook.py:12-40`
- Test: `tests/test_verdict.py`

- [ ] **Step 1: Something**

### Task 3: No files block

Just prose, no Files section.

- [ ] **Step 1: Something**
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.phases = planparse.parse(PLAN)

    def test_finds_every_task_heading(self):
        self.assertEqual([p["number"] for p in self.phases], [1, 2, 3])

    def test_captures_titles(self):
        self.assertEqual(self.phases[0]["title"], "Event log")

    def test_prose_mentioning_task_is_not_a_phase(self):
        self.assertNotIn(99, [p["number"] for p in self.phases])

    def test_collects_create_modify_and_test_paths(self):
        self.assertEqual(self.phases[1]["files"],
                         ["scripts/ccalib/verdict.py", "scripts/critic_hook.py",
                          "tests/test_verdict.py"])

    def test_strips_line_range_suffix(self):
        self.assertIn("scripts/critic_hook.py", self.phases[1]["files"])
        self.assertNotIn("scripts/critic_hook.py:12-40", self.phases[1]["files"])

    def test_task_without_files_block_has_empty_scope(self):
        self.assertEqual(self.phases[2]["files"], [])

    def test_line_ranges_bound_each_task(self):
        first, second = self.phases[0], self.phases[1]
        self.assertLess(first["start_line"], first["end_line"])
        self.assertLessEqual(first["end_line"], second["start_line"])

    def test_last_task_end_line_reaches_end_of_document(self):
        self.assertEqual(self.phases[-1]["end_line"], len(PLAN.splitlines()))

    def test_garbage_input_returns_empty_not_raises(self):
        self.assertEqual(planparse.parse("no headings here at all"), [])

    def test_empty_input_returns_empty(self):
        self.assertEqual(planparse.parse(""), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_planparse -v`
Expected: FAIL — `ImportError: cannot import name 'planparse'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/planparse.py
"""Parse a writing-plans document into phases.

Reads only what the plan format guarantees: `### Task N: Title` headings and
the backticked paths in the `**Files:**` block beneath each one. Tolerant by
design -- an unrecognised line is skipped, never fatal, because a parse error
must degrade to "no gate", not to "everything is out of scope".
"""
import re
from typing import Any, Dict, List

_HEADING = re.compile(r"^###\s+Task\s+(\d+)\s*:\s*(.+?)\s*$")
_FILE_LINE = re.compile(r"^\s*-\s*(?:Create|Modify|Test)\s*:\s*`([^`]+)`")
_FILES_HEADER = re.compile(r"^\s*\*\*Files:\*\*\s*$")
_ANY_BOLD_HEADER = re.compile(r"^\s*\*\*[A-Za-z][^*]*:\*\*\s*$")


def _strip_range(path):
    # type: (str) -> str
    """`scripts/x.py:12-40` -> `scripts/x.py`"""
    return re.sub(r":\d+(?:-\d+)?$", "", path.strip())


def parse(text):
    # type: (str) -> List[Dict[str, Any]]
    if not text:
        return []
    lines = text.splitlines()

    starts = []  # type: List[Any]
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            starts.append((index, int(match.group(1)), match.group(2)))

    phases = []  # type: List[Dict[str, Any]]
    for position, (index, number, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        files = []  # type: List[str]
        in_files = False
        for line in lines[index:end]:
            if _FILES_HEADER.match(line):
                in_files = True
                continue
            if in_files:
                match = _FILE_LINE.match(line)
                if match:
                    files.append(_strip_range(match.group(1)))
                    continue
                # a different bold header, or a blank line after entries, ends the block
                if _ANY_BOLD_HEADER.match(line):
                    in_files = False
                elif line.strip() and not line.lstrip().startswith("-"):
                    in_files = False
        phases.append({"number": number, "title": title, "files": files,
                       "start_line": index, "end_line": end})
    return phases
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_planparse -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 2: Git state primitives

**Files:**
- Create: `scripts/ccalib/gitstate.py`
- Test: `tests/test_gitstate.py`

**Interfaces:**
- Consumes: nothing
- Produces: `is_repo(root) -> bool`, `phase_commits(root) -> Optional[Dict[int, str]]`,
  `dirty_paths(root) -> Optional[List[str]]`. **`None` means "could not determine"** and every
  caller treats it as "no gate", never as "clean" or "nothing shipped".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gitstate.py
import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import gitstate


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_repo():
    """A throwaway repo in a temp dir. The plugin's own repo is never touched."""
    root = tempfile.mkdtemp()
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    return root


def commit(root, name, message):
    with open(os.path.join(root, name), "w") as fh:
        fh.write("x")
    git(root, "add", name)
    git(root, "commit", "-m", message)


class TestIsRepo(unittest.TestCase):
    def test_true_inside_a_repo(self):
        self.assertTrue(gitstate.is_repo(make_repo()))

    def test_false_outside_a_repo(self):
        self.assertFalse(gitstate.is_repo(tempfile.mkdtemp()))


class TestPhaseCommits(unittest.TestCase):
    def test_empty_when_no_tagged_commits(self):
        root = make_repo()
        commit(root, "a.txt", "just a normal commit")
        self.assertEqual(gitstate.phase_commits(root), {})

    def test_maps_phase_number_to_sha(self):
        root = make_repo()
        commit(root, "a.txt", "[phase-1] Event log")
        commit(root, "b.txt", "[phase-2] Verdict parsing")
        found = gitstate.phase_commits(root)
        self.assertEqual(sorted(found.keys()), [1, 2])
        self.assertTrue(all(len(sha) >= 7 for sha in found.values()))

    def test_tag_must_be_at_the_start_of_the_subject(self):
        root = make_repo()
        commit(root, "a.txt", "mentions [phase-9] in passing")
        self.assertEqual(gitstate.phase_commits(root), {})

    def test_returns_none_outside_a_repo(self):
        self.assertIsNone(gitstate.phase_commits(tempfile.mkdtemp()))


class TestDirtyPaths(unittest.TestCase):
    def test_empty_list_when_clean(self):
        root = make_repo()
        commit(root, "a.txt", "init")
        self.assertEqual(gitstate.dirty_paths(root), [])

    def test_lists_modified_and_untracked(self):
        root = make_repo()
        commit(root, "a.txt", "init")
        with open(os.path.join(root, "a.txt"), "w") as fh:
            fh.write("changed")
        with open(os.path.join(root, "b.txt"), "w") as fh:
            fh.write("new")
        self.assertEqual(sorted(gitstate.dirty_paths(root)), ["a.txt", "b.txt"])

    def test_returns_none_outside_a_repo(self):
        self.assertIsNone(gitstate.dirty_paths(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gitstate -v`
Expected: FAIL — `ImportError: cannot import name 'gitstate'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/gitstate.py
"""Read-only git inspection.

Never mutates anything -- no add, no commit, no init. `None` is returned
whenever the answer cannot be determined, and every caller must treat it as
"no gate" rather than as "clean" or "nothing shipped".
"""
import re
import subprocess
from typing import Dict, List, Optional

_TAG = re.compile(r"^\[phase-(\d+)\]")
_TIMEOUT = 15


def _run(root, args):
    # type: (str, List[str]) -> Optional[str]
    try:
        proc = subprocess.Popen(["git"] + args, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        out, _err = proc.communicate(timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return out


def is_repo(root):
    # type: (str) -> bool
    out = _run(root, ["rev-parse", "--is-inside-work-tree"])
    return bool(out) and out.strip() == "true"


def phase_commits(root):
    # type: (str) -> Optional[Dict[int, str]]
    if not is_repo(root):
        return None
    out = _run(root, ["log", "--format=%h%x09%s"])
    if out is None:
        return None
    found = {}  # type: Dict[int, str]
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        match = _TAG.match(subject.strip())
        if match:
            number = int(match.group(1))
            found.setdefault(number, sha)  # earliest-seen wins on re-commits
    return found


def dirty_paths(root):
    # type: (str) -> Optional[List[str]]
    if not is_repo(root):
        return None
    out = _run(root, ["status", "--porcelain"])
    if out is None:
        return None
    paths = []  # type: List[str]
    for line in out.splitlines():
        if len(line) > 3:
            paths.append(line[3:].strip())
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gitstate -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 3: Phase resolution and scope classification

**Files:**
- Create: `scripts/ccalib/phases.py`
- Modify: `scripts/ccalib/config.py` (add the `phases` defaults block)
- Test: `tests/test_phases.py`

**Interfaces:**
- Consumes: `planparse.parse`, `gitstate.phase_commits`
- Produces: `active_plan_path(root, cfg) -> Optional[str]`, `load(root, cfg) -> List[Dict]`,
  `current(phases, shipped, cfg) -> Optional[Dict]`,
  `classify(path, phases, current_phase) -> str` returning
  `"current" | "later" | "earlier" | "unlisted"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phases.py
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import config, phases

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`
- Test: `tests/test_b.py`

- [ ] **Step 1: x**

### Task 3: Three

**Files:**
- Create: `c.py`

- [ ] **Step 1: x**
"""


def project_with_plan(body=PLAN, name="2026-09-14-p.md"):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, name), "w") as fh:
        fh.write(body)
    return root


class TestActivePlan(unittest.TestCase):
    def test_none_when_no_plans_directory(self):
        cfg = config.load(tempfile.mkdtemp())
        self.assertIsNone(phases.active_plan_path(tempfile.mkdtemp(), cfg))

    def test_finds_the_only_plan(self):
        root = project_with_plan()
        self.assertTrue(phases.active_plan_path(root, config.load(root)).endswith("p.md"))

    def test_config_pin_wins(self):
        root = project_with_plan()
        os.makedirs(os.path.join(root, ".cca"))
        pinned = "docs/superpowers/plans/2026-09-14-p.md"
        with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"enabled": True, "plan": pinned}}, fh)
        cfg = config.load(root)
        self.assertTrue(phases.active_plan_path(root, cfg).endswith("p.md"))


class TestCurrent(unittest.TestCase):
    def setUp(self):
        self.phases = phases.load(project_with_plan(), config.load(tempfile.mkdtemp()))

    def test_first_phase_when_nothing_shipped(self):
        self.assertEqual(phases.current(self.phases, {}, {})["number"], 1)

    def test_lowest_unshipped_phase(self):
        self.assertEqual(phases.current(self.phases, {1: "aaa"}, {})["number"], 2)

    def test_gap_in_shipped_phases_picks_the_lowest_missing(self):
        self.assertEqual(phases.current(self.phases, {1: "a", 3: "c"}, {})["number"], 2)

    def test_none_when_all_shipped(self):
        self.assertIsNone(phases.current(self.phases, {1: "a", 2: "b", 3: "c"}, {}))

    def test_config_done_override_is_honoured(self):
        cfg = {"phases": {"done": [1, 2]}}
        self.assertEqual(phases.current(self.phases, {}, cfg)["number"], 3)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.phases = phases.load(project_with_plan(), config.load(tempfile.mkdtemp()))
        self.current = phases.current(self.phases, {1: "aaa"}, {})  # phase 2

    def test_current_phase_file(self):
        self.assertEqual(phases.classify("b.py", self.phases, self.current), "current")

    def test_later_phase_file(self):
        self.assertEqual(phases.classify("c.py", self.phases, self.current), "later")

    def test_earlier_phase_file(self):
        self.assertEqual(phases.classify("a.py", self.phases, self.current), "earlier")

    def test_unlisted_file(self):
        self.assertEqual(phases.classify("zzz.py", self.phases, self.current), "unlisted")

    def test_everything_unlisted_when_there_is_no_current_phase(self):
        self.assertEqual(phases.classify("c.py", self.phases, None), "unlisted")

    def test_path_matching_is_suffix_tolerant(self):
        self.assertEqual(phases.classify("./b.py", self.phases, self.current), "current")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phases -v`
Expected: FAIL — `ImportError: cannot import name 'phases'`

- [ ] **Step 3: Add the config defaults**

In `scripts/ccalib/config.py`, add to the `DEFAULTS` dict, after `"ignore_globs"`:

```python
    "phases": {
        "enabled": True,
        "plan": "",                 # empty -> newest file in docs/superpowers/plans/
        "test_command": "",         # empty -> the test rung is skipped with a warning
        "done": [],                 # escape hatch for history lost to squash/rebase
    },
```

- [ ] **Step 4: Write minimal implementation**

```python
# scripts/ccalib/phases.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phases -v`
Expected: PASS, 14 tests

- [ ] **Step 6: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 4: The fence (PreToolUse hook)

**Files:**
- Create: `scripts/phase_hook.py`
- Test: `tests/test_phase_hook.py`

**Interfaces:**
- Consumes: `config`, `phases`, `gitstate`, `events`
- Produces: an executable that exits **2 to deny** (reason on stderr) and **0 to allow**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase_hook.py
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOOK = os.path.join(ROOT, "scripts", "phase_hook.py")

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`

- [ ] **Step 1: x**

### Task 3: Three

**Files:**
- Create: `c.py`

- [ ] **Step 1: x**
"""


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def project(plan=PLAN, shipped=()):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, "p.md"), "w") as fh:
        fh.write(plan)
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    for number in shipped:
        name = "shipped%d.txt" % number
        with open(os.path.join(root, name), "w") as fh:
            fh.write("x")
        git(root, "add", name)
        git(root, "commit", "-m", "[phase-%d] done" % number)
    return root


def run(root, path, extra_env=None):
    event = {"tool_name": "Edit", "cwd": root, "tool_input": {"file_path": path}}
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestFence(unittest.TestCase):
    def test_current_phase_file_is_allowed(self):
        root = project(shipped=[1])          # current = 2
        self.assertEqual(run(root, "b.py")[0], 0)

    def test_later_phase_file_is_denied(self):
        root = project(shipped=[1])          # current = 2
        code, _out, err = run(root, "c.py")
        self.assertEqual(code, 2)
        self.assertIn("phase 3", err.lower())

    def test_earlier_phase_file_is_allowed(self):
        root = project(shipped=[1])
        self.assertEqual(run(root, "a.py")[0], 0)

    def test_unlisted_file_is_allowed(self):
        root = project(shipped=[1])
        self.assertEqual(run(root, "zzz.py")[0], 0)

    def test_denial_names_the_current_phase_too(self):
        root = project(shipped=[1])
        _code, _out, err = run(root, "c.py")
        self.assertIn("Two", err)


class TestFailsOpen(unittest.TestCase):
    def test_recursion_firewall_allows(self):
        root = project(shipped=[1])
        self.assertEqual(run(root, "c.py", {"CCA_INNER": "1"})[0], 0)

    def test_no_plan_allows(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_not_a_git_repo_allows(self):
        root = tempfile.mkdtemp()
        plans = os.path.join(root, "docs", "superpowers", "plans")
        os.makedirs(plans)
        with open(os.path.join(plans, "p.md"), "w") as fh:
            fh.write(PLAN)
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_unparseable_plan_allows(self):
        root = project(plan="this document has no task headings")
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_disabled_in_config_allows(self):
        root = project(shipped=[1])
        os.makedirs(os.path.join(root, ".cca"))
        with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"enabled": False}}, fh)
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_malformed_stdin_allows(self):
        root = project(shipped=[1])
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate("not json", timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_all_phases_shipped_allows_everything(self):
        root = project(shipped=[1, 2, 3])
        self.assertEqual(run(root, "c.py")[0], 0)

    def test_non_edit_tool_allows(self):
        root = project(shipped=[1])
        event = {"tool_name": "Read", "cwd": root, "tool_input": {"file_path": "c.py"}}
        proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=root, universal_newlines=True)
        proc.communicate(json.dumps(event), timeout=30)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phase_hook -v`
Expected: FAIL — the script does not exist

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# scripts/phase_hook.py
"""PreToolUse fence: deny edits to files owned by a LATER phase.

This is the most dangerous code in the plugin -- a false deny stops all work.
Every uncertain path allows the edit. A fence that cannot read the plan must
never conclude "nothing is in scope".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALLOW = 0
DENY = 2


def main():
    if os.environ.get("CCA_INNER"):
        return ALLOW

    from ccalib import config, events, gitstate, phases

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return ALLOW

    if event.get("tool_name", "") not in ("Edit", "Write"):
        return ALLOW

    file_path = (event.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return ALLOW

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = config.load(root)
    if not (cfg.get("phases") or {}).get("enabled", True):
        return ALLOW

    phase_list = phases.load(root, cfg)
    if not phase_list:
        return ALLOW

    shipped = gitstate.phase_commits(root)
    if shipped is None:          # not a repo, or git unavailable
        return ALLOW

    current = phases.current(phase_list, shipped, cfg)
    if not current:              # every phase shipped
        return ALLOW

    rel = os.path.relpath(file_path, root) if os.path.isabs(file_path) else file_path
    verdict = phases.classify(rel, phase_list, current)

    if verdict == "later":
        owner = next(
            (p for p in phase_list
             if p["number"] > current["number"]
             and phases.classify(rel, [p], p) == "current"),
            None,
        )
        owner_number = owner["number"] if owner else "?"
        owner_title = owner["title"] if owner else "a later phase"
        events.append("phase_fenced",
                      {"file": rel, "current": current["number"],
                       "owner": owner_number}, root)
        sys.stderr.write(
            "CCA phase gate: %s belongs to phase %s (%s), but you are on "
            "phase %s (%s).\n\n"
            "Finish and commit phase %s first. If this file genuinely belongs to the "
            "current phase, add it to that task's Files block in the plan.\n"
            % (rel, owner_number, owner_title, current["number"], current["title"],
               current["number"])
        )
        return DENY

    if verdict == "unlisted":
        events.append("phase_fenced",
                      {"file": rel, "current": current["number"], "owner": None,
                       "allowed": True}, root)
    return ALLOW


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:   # a fence bug must never stop the user working
        sys.exit(ALLOW)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phase_hook -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 5: The gate ladder

**Files:**
- Create: `scripts/ccalib/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `feedback.open_items`, `gitstate.dirty_paths`, `gitstate.phase_commits`
- Produces: `evaluate(root, cfg, phase, shipped) -> Dict` with keys
  `{"ok": bool, "rung": str, "checks": Dict[str, bool], "reason": str}` where `rung` is one of
  `"tests" | "critics" | "commit" | "tree" | ""`;
  `commit_command(phase, plan_rel) -> str`; `tick_steps(plan_path, phase) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate.py
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import gate

PHASE = {"number": 3, "title": "Critic runner",
         "files": ["scripts/ccalib/runner.py", "tests/test_runner.py"],
         "start_line": 0, "end_line": 8}

PLAN_BODY = """### Task 3: Critic runner

**Files:**
- Create: `scripts/ccalib/runner.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Implement**

### Task 4: Next

- [ ] **Step 1: Untouched**
"""


def cfg(test_command=""):
    return {"phases": {"enabled": True, "test_command": test_command, "done": []}}


class TestLadderOrder(unittest.TestCase):
    def test_failing_tests_block_at_the_first_rung(self):
        root = tempfile.mkdtemp()
        result = gate.evaluate(root, cfg("python3 -c 'import sys; sys.exit(1)'"), PHASE, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["rung"], "tests")

    def test_open_critic_items_block_before_commit(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.ts\n- finding\n")
        result = gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {})
        self.assertEqual(result["rung"], "critics")

    def test_missing_commit_blocks_at_the_third_rung(self):
        root = tempfile.mkdtemp()
        result = gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {})
        self.assertEqual(result["rung"], "commit")

    def test_dirty_tree_blocks_after_the_commit_exists(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "stray.txt"), "w") as fh:
            fh.write("x")
        result = gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {3: "abc1234"},
                               dirty=["stray.txt"])
        self.assertEqual(result["rung"], "tree")

    def test_everything_satisfied_passes(self):
        root = tempfile.mkdtemp()
        result = gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {3: "abc1234"}, dirty=[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rung"], "")


class TestTestRung(unittest.TestCase):
    def test_absent_test_command_skips_rather_than_blocks(self):
        root = tempfile.mkdtemp()
        result = gate.evaluate(root, cfg(""), PHASE, {})
        self.assertNotEqual(result["rung"], "tests")
        self.assertIsNone(result["checks"]["tests"])

    def test_passing_tests_record_true(self):
        root = tempfile.mkdtemp()
        result = gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {})
        self.assertTrue(result["checks"]["tests"])


class TestCommitCommand(unittest.TestCase):
    def test_includes_every_phase_file_and_the_plan(self):
        command = gate.commit_command(PHASE, "docs/superpowers/plans/p.md")
        self.assertIn("scripts/ccalib/runner.py", command)
        self.assertIn("tests/test_runner.py", command)
        self.assertIn("docs/superpowers/plans/p.md", command)

    def test_message_carries_the_phase_tag_and_title(self):
        command = gate.commit_command(PHASE, "p.md")
        self.assertIn("[phase-3]", command)
        self.assertIn("Critic runner", command)

    def test_never_emits_push_or_init(self):
        command = gate.commit_command(PHASE, "p.md")
        self.assertNotIn("push", command)
        self.assertNotIn("init", command)


class TestTickSteps(unittest.TestCase):
    def test_ticks_only_the_named_phase(self):
        path = os.path.join(tempfile.mkdtemp(), "plan.md")
        with open(path, "w") as fh:
            fh.write(PLAN_BODY)
        phase = {"number": 3, "title": "Critic runner", "files": [],
                 "start_line": 0, "end_line": 7}
        ticked = gate.tick_steps(path, phase)
        with open(path) as fh:
            body = fh.read()
        self.assertEqual(ticked, 2)
        self.assertIn("- [x] **Step 1: Write the failing test**", body)
        self.assertIn("- [ ] **Step 1: Untouched**", body)

    def test_ticking_twice_is_idempotent(self):
        path = os.path.join(tempfile.mkdtemp(), "plan.md")
        with open(path, "w") as fh:
            fh.write(PLAN_BODY)
        phase = {"number": 3, "title": "x", "files": [], "start_line": 0, "end_line": 7}
        gate.tick_steps(path, phase)
        self.assertEqual(gate.tick_steps(path, phase), 0)

    def test_missing_file_returns_zero_not_raises(self):
        phase = {"number": 3, "title": "x", "files": [], "start_line": 0, "end_line": 7}
        self.assertEqual(gate.tick_steps("/nonexistent/plan.md", phase), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gate -v`
Expected: FAIL — `ImportError: cannot import name 'gate'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ccalib/gate.py
"""The Stop-time gate ladder.

Rung order is load-bearing: the operator is never told to commit work that
still fails its tests or still has open critic findings.
"""
import os
import subprocess
from typing import Any, Dict, List, Optional

from . import feedback, gitstate

_TEST_TIMEOUT = 600


def _run_tests(root, command):
    # type: (str, str) -> Any
    """True / False, or None when no command is configured."""
    if not command:
        return None
    try:
        proc = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        out, _ = proc.communicate(timeout=_TEST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None          # cannot determine -> never block on it
    return (proc.returncode == 0, out)


def commit_command(phase, plan_rel):
    # type: (Dict[str, Any], str) -> str
    paths = list(phase.get("files", []))
    if plan_rel:
        paths.append(plan_rel)
    return ('git add %s \\\n  && git commit -m "[phase-%s] %s"'
            % (" ".join(paths), phase["number"], phase["title"]))


def tick_steps(plan_path, phase):
    # type: (str, Dict[str, Any]) -> int
    """Mark this phase's step checkboxes done. Returns how many were changed."""
    try:
        with open(plan_path) as fh:
            lines = fh.read().splitlines(True)
    except (IOError, OSError):
        return 0

    start = phase.get("start_line", 0)
    end = min(phase.get("end_line", len(lines)), len(lines))
    changed = 0
    for index in range(start, end):
        if lines[index].lstrip().startswith("- [ ]"):
            lines[index] = lines[index].replace("- [ ]", "- [x]", 1)
            changed += 1
    if not changed:
        return 0
    try:
        with open(plan_path, "w") as fh:
            fh.writelines(lines)
    except (IOError, OSError):
        return 0
    return changed


def evaluate(root, cfg, phase, shipped, dirty=None):
    # type: (str, Dict[str, Any], Dict[str, Any], Dict[int, str], Optional[List[str]]) -> Dict[str, Any]
    checks = {"tests": None, "critics": None, "commit": None, "tree": None}

    # Rung 1 -- tests
    outcome = _run_tests(root, (cfg.get("phases") or {}).get("test_command", ""))
    if outcome is not None:
        passed, output = outcome
        checks["tests"] = passed
        if not passed:
            return {"ok": False, "rung": "tests", "checks": checks,
                    "reason": "Phase %s tests are failing:\n\n%s"
                              % (phase["number"], output[-3000:])}

    # Rung 2 -- critics
    open_items = feedback.open_items(root)
    checks["critics"] = not open_items
    if open_items:
        return {"ok": False, "rung": "critics", "checks": checks,
                "reason": "CF.md has %d open critic item(s); address them before shipping "
                          "phase %s:\n\n%s"
                          % (len(open_items), phase["number"], "\n".join(open_items[:20]))}

    # Rung 3 -- the commit
    has_commit = phase["number"] in (shipped or {})
    checks["commit"] = has_commit
    if not has_commit:
        return {"ok": False, "rung": "commit", "checks": checks, "reason": ""}

    # Rung 4 -- clean tree
    paths = dirty if dirty is not None else gitstate.dirty_paths(root)
    if paths is None:
        checks["tree"] = None
        return {"ok": True, "rung": "", "checks": checks, "reason": ""}
    checks["tree"] = not paths
    if paths:
        return {"ok": False, "rung": "tree", "checks": checks,
                "reason": "Phase %s is committed but the tree is not clean. These belong "
                          "either to this phase or to the next one:\n\n%s"
                          % (phase["number"], "\n".join(paths[:30]))}

    return {"ok": True, "rung": "", "checks": checks, "reason": ""}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gate -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 6: Wire the gate into Stop, and add SessionStart context

**Files:**
- Modify: `scripts/drain_hook.py`
- Create: `scripts/session_hook.py`
- Test: `tests/test_gate_hooks.py`

**Interfaces:**
- Consumes: `gate.evaluate`, `gate.commit_command`, `gate.tick_steps`, `phases`, `gitstate`
- Produces: `drain_hook.py` blocking on the phase gate after the CF.md check;
  `session_hook.py` printing `hookSpecificOutput.additionalContext` describing the current phase

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate_hooks.py
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DRAIN = os.path.join(ROOT, "scripts", "drain_hook.py")
SESSION = os.path.join(ROOT, "scripts", "session_hook.py")

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`

- [ ] **Step 1: x**
"""


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def project(shipped=(), test_command="python3 -c 'pass'"):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, "p.md"), "w") as fh:
        fh.write(PLAN)
    os.makedirs(os.path.join(root, ".cca"))
    with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
        json.dump({"phases": {"enabled": True, "test_command": test_command, "done": []}}, fh)
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write(".cca/\nCF.md\n")
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    for number in shipped:
        name = "shipped%d.txt" % number
        with open(os.path.join(root, name), "w") as fh:
            fh.write("x")
        git(root, "add", name)
        git(root, "commit", "-m", "[phase-%d] done" % number)
    return root


def run(script, event, root):
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    proc = subprocess.Popen([sys.executable, script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=120)
    return proc.returncode, out, err


class TestDrainGate(unittest.TestCase):
    def test_blocks_when_phase_has_no_commit(self):
        root = project()
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("git add", payload["reason"])
        self.assertIn("[phase-1]", payload["reason"])

    def test_ticks_the_plan_checkboxes_when_suggesting_the_commit(self):
        root = project()
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        with open(os.path.join(root, "docs", "superpowers", "plans", "p.md")) as fh:
            body = fh.read()
        self.assertIn("- [x] **Step 1: x**", body)

    def test_allows_stop_when_the_phase_is_committed_and_tree_clean(self):
        root = project(shipped=[1])
        # phase 2 has no work yet: its commit is missing, so it still blocks --
        # ship it to reach the all-clear state
        root2 = project(shipped=[1, 2])
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root2}, root2)
        self.assertEqual(out.strip(), "")

    def test_cf_items_block_before_the_phase_gate(self):
        root = project()
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.py\n- finding\n")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("CF.md", json.loads(out)["reason"])

    def test_stop_hook_active_never_loops(self):
        root = project()
        _code, out, _err = run(DRAIN, {"stop_hook_active": True, "cwd": root}, root)
        self.assertEqual(out.strip(), "")

    def test_no_plan_allows_stop(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")


class TestSessionContext(unittest.TestCase):
    def test_announces_the_current_phase(self):
        root = project(shipped=[1])
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("phase 2", context.lower())
        self.assertIn("Two", context)

    def test_lists_shipped_phases(self):
        root = project(shipped=[1])
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("One", context)

    def test_silent_when_no_plan(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gate_hooks -v`
Expected: FAIL — `session_hook.py` does not exist; drain has no gate

- [ ] **Step 3: Extend `drain_hook.py`**

Replace the body of `main()` in `scripts/drain_hook.py`, after the existing `stop_hook_active`
check and the `open_items` block, with the following. The CF.md check stays exactly where it is —
it is rung 2 of the ladder and must keep firing before the phase gate.

```python
    # --- phase gate (rungs 1, 3, 4 -- rung 2 is the CF.md check above) ---
    from ccalib import config, gate, gitstate, phases

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

    result = gate.evaluate(root, cfg, current, shipped)
    events.append("phase_gate", {"phase": current["number"], "ok": result["ok"],
                                 "rung": result["rung"], "checks": result["checks"]}, root)
    if result["ok"]:
        return 0

    if result["rung"] == "commit":
        plan_path = phases.active_plan_path(root, cfg) or ""
        plan_rel = os.path.relpath(plan_path, root) if plan_path else ""
        gate.tick_steps(plan_path, current)
        reason = (
            "Phase %s/%s -- %s is ready but has not shipped.\n\n"
            "Commit it before moving on. The plan file is included so the ticked "
            "steps ride along and the tree ends clean:\n\n  %s\n"
            % (current["number"], len(phase_list), current["title"],
               gate.commit_command(current, plan_rel))
        )
    else:
        reason = "Phase %s/%s -- %s cannot ship yet.\n\n%s" % (
            current["number"], len(phase_list), current["title"], result["reason"])

    print(json.dumps({"decision": "block", "reason": reason}))
    return 0
```

- [ ] **Step 4: Write `session_hook.py`**

```python
#!/usr/bin/env python3
# scripts/session_hook.py
"""SessionStart hook: tell the session where it is in the plan.

This is what makes phase discipline survive a session boundary -- a fresh
session learns what has shipped and what is next from git, not from memory.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import config, events, gitstate, phases

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        event = {}

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
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
    done = [p for p in phase_list if p["number"] in shipped]
    done_text = ", ".join("%s %s (%s)" % (p["number"], p["title"], shipped[p["number"]])
                          for p in done) or "none yet"

    if current:
        body = (
            "CCA phase gate is active.\n"
            "Current: phase %s of %s - %s\n"
            "Scope: %s\n"
            "Shipped: %s\n\n"
            "Work only on the current phase. Edits to files owned by a later phase are "
            "blocked. When the phase is done, you will be given a commit command to hand "
            "to the operator - never run git yourself."
            % (current["number"], len(phase_list), current["title"],
               ", ".join(current["files"]) or "(no files listed)", done_text)
        )
    else:
        body = "CCA phase gate: every phase in the plan has shipped (%s)." % done_text

    events.append("phase_context",
                  {"current": current["number"] if current else None,
                   "total": len(phase_list)}, root)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": body}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_gate_hooks -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Verify**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

### Task 7: Hook wiring, event types, and the dashboard phase strip

**Files:**
- Modify: `hooks/hooks.json`
- Modify: `scripts/ccalib/events.py` (add the three new event types)
- Modify: `scripts/dashboard.py` (add `phase_strip`)
- Modify: `dashboard/index.html`
- Test: `tests/test_phase_dashboard.py`, `tests/test_manifest.py`

**Interfaces:**
- Consumes: `.cca/events.jsonl`
- Produces: `dashboard.phase_strip(events) -> Dict` with
  `{"current": Optional[int], "total": Optional[int], "title": str, "checks": Dict, "fenced": int}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase_dashboard.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dashboard
from ccalib import events as events_mod

ROOT = os.path.join(os.path.dirname(__file__), "..")


def E(kind, payload):
    return {"type": kind, "ts": 1.0, "payload": payload}


class TestNewEventTypes(unittest.TestCase):
    def test_phase_events_are_registered(self):
        for kind in ("phase_context", "phase_fenced", "phase_gate"):
            self.assertIn(kind, events_mod.EVENT_TYPES, kind)


class TestPhaseStrip(unittest.TestCase):
    def test_empty_when_no_phase_events(self):
        self.assertIsNone(dashboard.phase_strip([])["current"])

    def test_reads_current_and_total_from_context(self):
        strip = dashboard.phase_strip([E("phase_context", {"current": 3, "total": 11})])
        self.assertEqual(strip["current"], 3)
        self.assertEqual(strip["total"], 11)

    def test_latest_gate_result_wins(self):
        strip = dashboard.phase_strip([
            E("phase_context", {"current": 3, "total": 11}),
            E("phase_gate", {"phase": 3, "ok": False, "rung": "tests",
                             "checks": {"tests": False}}),
            E("phase_gate", {"phase": 3, "ok": False, "rung": "commit",
                             "checks": {"tests": True, "critics": True, "commit": False}}),
        ])
        self.assertEqual(strip["rung"], "commit")
        self.assertTrue(strip["checks"]["tests"])

    def test_counts_fence_denials_only(self):
        strip = dashboard.phase_strip([
            E("phase_fenced", {"file": "c.py", "current": 2, "owner": 3}),
            E("phase_fenced", {"file": "z.py", "current": 2, "owner": None, "allowed": True}),
        ])
        self.assertEqual(strip["fenced"], 1)


class TestPage(unittest.TestCase):
    def test_page_renders_the_phase_strip(self):
        with open(os.path.join(ROOT, "dashboard", "index.html")) as fh:
            body = fh.read()
        self.assertIn("phase-strip", body)
        self.assertIn("phase_context", body)


if __name__ == "__main__":
    unittest.main()
```

Add to `tests/test_manifest.py`:

```python
    def test_hooks_json_wires_the_phase_fence_and_session_context(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        pre = hooks["PreToolUse"][0]
        self.assertEqual(pre["matcher"], "Edit|Write")
        self.assertIn("phase_hook.py", pre["hooks"][0]["command"])
        self.assertIn("session_hook.py", hooks["SessionStart"][0]["hooks"][0]["command"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phase_dashboard tests.test_manifest -v`
Expected: FAIL — event types missing, `phase_strip` undefined, hooks unwired

- [ ] **Step 3: Register the new event types**

In `scripts/ccalib/events.py`, extend `EVENT_TYPES`:

```python
EVENT_TYPES = frozenset([
    "edit_started", "layer0_result", "critic_dispatched", "critic_verdict",
    "vote", "queued", "blocked", "drain_blocked", "session_end",
    "phase_context", "phase_fenced", "phase_gate",
])  # type: FrozenSet[str]
```

- [ ] **Step 4: Add `phase_strip` to `scripts/dashboard.py`**

```python
def phase_strip(events):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    strip = {"current": None, "total": None, "title": "", "rung": "",
             "checks": {}, "fenced": 0}  # type: Dict[str, Any]
    for e in events:
        kind = e.get("type")
        p = e.get("payload", {})
        if kind == "phase_context":
            strip["current"] = p.get("current")
            strip["total"] = p.get("total")
        elif kind == "phase_gate":
            strip["rung"] = p.get("rung", "")
            strip["checks"] = p.get("checks", {})
            if strip["current"] is None:
                strip["current"] = p.get("phase")
        elif kind == "phase_fenced" and not p.get("allowed"):
            strip["fenced"] += 1
    return strip
```

- [ ] **Step 5: Render the strip in `dashboard/index.html`**

Insert this markup immediately after the `.cast` div:

```html
  <div class="cast" id="phase-strip" hidden></div>
```

Add this CSS inside the existing `<style>` block:

```css
  .gate { display: inline-block; margin-right: 14px; }
  .gate .on { color: var(--pass); }
  .gate .off { color: var(--dim); }
  .gate .bad { color: var(--fail); }
```

Add these two functions to the `<script>`, and call `renderPhase(events)` from `render()`:

```javascript
function light(name, value) {
  const cls = value === true ? "on" : (value === false ? "bad" : "off");
  const mark = value === true ? "✅" : (value === false ? "⬜" : "—");
  return '<span class="gate"><span class="' + cls + '">' + mark + '</span> ' + name + '</span>';
}

function renderPhase(events) {
  const el = document.getElementById("phase-strip");
  let current = null, total = null, checks = {}, fenced = 0;
  for (const e of events) {
    const p = e.payload || {};
    if (e.type === "phase_context") { current = p.current; total = p.total; }
    else if (e.type === "phase_gate") { checks = p.checks || {}; if (current === null) current = p.phase; }
    else if (e.type === "phase_fenced" && !p.allowed) fenced++;
  }
  if (current === null) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '<div>phase <b>' + current + (total ? " / " + total : "") + '</b>'
    + (fenced ? ' &nbsp;·&nbsp; <span class="meta">' + fenced + ' out-of-phase edit(s) blocked</span>' : '')
    + '</div><div style="margin-top:6px">'
    + light("tests", checks.tests) + light("critics", checks.critics)
    + light("commit", checks.commit) + light("tree", checks.tree) + '</div>';
}
```

- [ ] **Step 6: Wire the hooks**

Replace `hooks/hooks.json` with:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/session_hook.py\"",
            "timeout": 30
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/phase_hook.py\"",
            "timeout": 30
          }
        ]
      }
    ],
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
            "timeout": 600
          }
        ]
      }
    ]
  }
}
```

Note the `Stop` timeout rises to 600s because the gate now runs the project's test suite.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest tests.test_phase_dashboard tests.test_manifest -v`
Expected: PASS

- [ ] **Step 8: Verify the whole suite**

Run: `cd ~/Documents/ChatGPT/CCA && python3 -m unittest discover tests`
Expected: all tests pass

---

## After the plan

1. Update `README.md` with a Phase Gate section: the ladder, the config block, and the escape hatches.
2. Dogfood it: `git init` this repo (operator's call), point `phases.plan` at the ensemble plan, and
   see whether the fence agrees with how the work was actually done.
3. The fence does not cover `Bash` heredoc writes or `NotebookEdit`. Those bypass it entirely today —
   spec open question 1.
