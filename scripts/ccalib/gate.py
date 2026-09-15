"""The Stop-time gate ladder.

Rung order is load-bearing: the operator is never told to commit work that
still fails its tests or still has open critic findings.
"""
import os
import subprocess
from typing import Any, Dict, List, Optional

from . import feedback, gitstate, phases as phases_mod

_TEST_TIMEOUT = 600


def _run_tests(root, command):
    # type: (str, str) -> Any
    """(passed, output), or None when no command is configured or it cannot run."""
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


def _later_phase_paths(paths, phase, phase_list):
    # type: (Optional[List[str]], Dict[str, Any], Optional[List[Dict[str, Any]]]) -> List[Any]
    """Dirty paths owned by a phase AFTER the current one."""
    if not paths or not phase_list:
        return []
    found = []  # type: List[Any]
    for path in paths:
        if phases_mod.classify(path, phase_list, phase) != "later":
            continue
        owner = next((p for p in phase_list
                      if p["number"] > phase["number"]
                      and phases_mod.classify(path, [p], p) == "current"), None)
        found.append((path, owner["number"] if owner else "?",
                      owner["title"] if owner else "later"))
    return found


def evaluate(root, cfg, phase, shipped, dirty=None, phase_list=None, skip_tests=False):
    # type: (str, Dict[str, Any], Dict[str, Any], Dict[int, str], Optional[List[str]], Optional[List[Dict[str, Any]]], bool) -> Dict[str, Any]
    checks = {"tests": None, "critics": None, "commit": None, "tree": None}

    # Rung 1 -- tests. Skipped only when they have already passed in this same
    # Stop: re-running a 600s command after the commit would run the hook past
    # its budget and throw away the decision the human just made.
    outcome = None if skip_tests else _run_tests(
        root, (cfg.get("phases") or {}).get("test_command", ""))
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

    paths = dirty if dirty is not None else gitstate.dirty_paths(root)

    # Rung 3 -- the commit
    has_commit = phase["number"] in (shipped or {})
    checks["commit"] = has_commit
    if not has_commit:
        # A clean tree means this phase has not been started, so there is nothing
        # to ship and nothing to block. Finishing the previous phase and stopping
        # must not be blocked by the next phase's mere existence.
        if paths == []:
            checks["tree"] = True
            return {"ok": True, "rung": "", "checks": checks, "reason": ""}

        # Later-phase work sitting uncommitted IS the lump this gate exists to
        # prevent. It has to be caught HERE, before the commit: once the phase
        # is committed it stops being `current`, so a post-commit tree check is
        # unreachable. (Found by dogfooding -- the old rung 4 was dead code.)
        ahead = _later_phase_paths(paths, phase, phase_list)
        if ahead:
            checks["tree"] = False
            listing = "\n".join("  %s  (phase %s -- %s)" % (path, number, title)
                                for path, number, title in ahead[:20])
            return {"ok": False, "rung": "tree", "checks": checks,
                    "reason": "Uncommitted work belonging to a later phase is in the tree "
                              "while you are on phase %s. Ship one phase at a time -- revert "
                              "or stash this, finish phase %s, and come back to it:\n\n%s"
                              % (phase["number"], phase["number"], listing)}

        checks["tree"] = True
        return {"ok": False, "rung": "commit", "checks": checks, "reason": ""}

    # Defensive: only reachable if a caller passes an inconsistent `shipped` map.
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
