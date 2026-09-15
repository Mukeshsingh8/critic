#!/usr/bin/env python3
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
