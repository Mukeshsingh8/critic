#!/usr/bin/env python3
"""PostToolUse hook. Fails open: any internal error exits 0 silently."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    # The recursion firewall comes before everything else. Critics are
    # `claude -p` processes inside this project; without this they load this
    # very hook and spawn critics of their own.
    if os.environ.get("CCA_INNER"):
        return 0

    from ccalib import arbiter, change, config, events, feedback, fixes, payload, runner, vote

    started = time.time()

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

    # What the coder actually did travels with the event. Without it the board
    # can show a verdict but never the thing being judged.
    events.append("edit_started", dict(
        {"file": rel, "tool": tool_name},
        **change.summarise(tool_name, tool_input)), root)

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

    # A clean verdict on this file is the evidence that its accepted fixes
    # landed. Nothing else in the system can honestly say they were carried out.
    if decision["decision"] == "pass":
        before = {item["id"]: item.get("key", "") for item in fixes.open_fixes(root)
                  if item.get("file") == rel}
        closed = fixes.resolve_file(root, rel)
        if closed:
            events.append("fix_resolved", {"file": rel, "count": closed,
                                           "keys": list(before.values())}, root)

    action = decision["decision"]
    if action == "block":
        answer = arbiter.request(root, cfg, "block", {
            "file": rel,
            "reason": decision["reason"],
            "findings": decision["findings"],
            "session_id": event.get("session_id", ""),
            "options": ["uphold", "queue", "overrule", "fix"],
        }, elapsed=time.time() - started)
        # No answer means no arbiter, or an arbiter who said nothing. Either
        # way the critics' own decision stands -- the bridge only ever relaxes
        # a gate when a human actually acts.
        action = (answer or {}).get("choice", "uphold")

        # "fix" is the whole point of the live question: Claude is still held
        # here, so the remedies the operator accepted become the block reason
        # and get carried out in this same turn rather than at the next Stop.
        if action == "fix":
            accepted = fixes.todo_fixes(root)
            if accepted:
                fixes.mark_sent(root, [item["id"] for item in accepted])
                events.append("fix_dispatched",
                              {"count": len(accepted), "via": "block",
                               "ids": [item["id"] for item in accepted],
                               "keys": [item.get("key", "") for item in accepted]}, root)
                events.append("blocked", {"file": rel}, root)
                print(json.dumps({"decision": "block",
                                  "reason": fixes.directive(accepted)}))
                return 0
            action = "block"  # nothing was accepted, so the block simply stands

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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a critic bug must never stop the user editing
        sys.exit(0)
