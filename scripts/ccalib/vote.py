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
            (" -> " + f["suggestion"]) if f.get("suggestion") else "",
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
        return {"decision": "queue", "findings": all_findings, "reason": _format(all_findings)}

    return {"decision": "pass", "findings": [], "reason": ""}
