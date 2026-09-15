#!/usr/bin/env python3
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
    with open(os.path.join(PROJECT, case["file"])) as fh:
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
