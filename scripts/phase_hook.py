#!/usr/bin/env python3
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

    from ccalib import bashwrite, config, events, gitstate, phases

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return ALLOW

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}

    if tool_name in ("Edit", "Write"):
        candidates = [tool_input.get("file_path", "")]
    elif tool_name == "NotebookEdit":
        # NotebookEdit uses `notebook_path`, not `file_path`, and the schema
        # requires it to be absolute.
        candidates = [tool_input.get("notebook_path", "")]
    elif tool_name == "Bash":
        # `cat > file <<EOF` is a normal way to write a file and used to walk
        # straight through this fence. Only write TARGETS count -- reading a
        # later-phase file must stay allowed.
        candidates = bashwrite.write_targets(tool_input.get("command", ""))
    else:
        return ALLOW

    candidates = [c for c in candidates if c]
    if not candidates:
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

    rel = ""
    verdict = "unlisted"
    for candidate in candidates:
        as_rel = (os.path.relpath(candidate, root)
                  if os.path.isabs(candidate) else candidate)
        outcome = phases.classify(as_rel, phase_list, current)
        if outcome == "later":          # first out-of-phase write wins
            rel, verdict = as_rel, outcome
            break
        if outcome != "unlisted":
            rel, verdict = as_rel, outcome
    if not rel:
        rel = candidates[0]

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
