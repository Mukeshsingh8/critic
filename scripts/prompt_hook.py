#!/usr/bin/env python3
"""UserPromptSubmit hook.

The Stop gate cannot reach a session that is sitting idle at the prompt, so an
accepted fix would wait until the next time Claude tried to finish. This hands
the queue over the moment the operator types anything.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import events, fixes

    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return 0

    root = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    pending = fixes.todo_fixes(root)
    if not pending:
        return 0

    fixes.mark_sent(root, [item["id"] for item in pending])
    events.append("fix_dispatched",
                  {"count": len(pending), "via": "prompt",
                   "ids": [item["id"] for item in pending],
                   "keys": [item.get("key", "") for item in pending]}, root)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": fixes.directive(pending),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a queue bug must never block the operator typing
        sys.exit(0)
