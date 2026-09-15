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
