#!/usr/bin/env python3
"""Stop hook. Refuses to finish while CF.md has open items."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import events, feedback, fixes

    started = time.time()

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return 0

    if event.get("stop_hook_active"):
        return 0  # already forced one continuation -- do not loop

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    # Rung 0 -- fixes the operator explicitly accepted. Above CF.md because
    # CF.md is a queue of things to arbitrate; this is a decision already made.
    accepted = fixes.todo_fixes(root)
    if accepted:
        fixes.mark_sent(root, [item["id"] for item in accepted])
        events.append("fix_dispatched",
                      {"count": len(accepted), "via": "stop",
                       "ids": [item["id"] for item in accepted],
                       "keys": [item.get("key", "") for item in accepted]}, root)
        print(json.dumps({"decision": "block", "reason": fixes.directive(accepted)}))
        return 0

    items = feedback.open_items(root)
    if not items:
        return _phase_gate(root, event, started)

    events.append("drain_blocked", {"count": len(items)}, root)
    listing = "\n".join(items[:20])
    print(json.dumps({
        "decision": "block",
        "reason": ("CF.md still has %d open critic item(s). Address each one, then "
                   "delete its heading from CF.md -- or insert a [wontfix] tag into the "
                   "heading with a one-line justification for the arbiter.\n\n%s"
                   % (len(items), listing)),
    }))
    return 0


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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
