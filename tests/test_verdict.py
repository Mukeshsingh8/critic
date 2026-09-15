import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import verdict


def payload(findings, stated="fail"):
    return json.dumps({"verdict": stated, "findings": findings})


GOOD_REUSE = {
    "severity": "major", "kind": "reuse", "file": "src/a.ts", "line": 4,
    "issue": "Reimplements currency formatting",
    "evidence": "lib/format.ts:12 exports formatCurrency() with the same signature",
    "suggestion": "Import formatCurrency",
}


class TestExtraction(unittest.TestCase):
    def test_extracts_json_embedded_in_prose(self):
        raw = "Sure! Here is my review:\n" + payload([GOOD_REUSE]) + "\nHope that helps."
        self.assertFalse(verdict.parse(raw, "opus")["degraded"])

    def test_unparseable_output_is_warn_and_degraded(self):
        r = verdict.parse("I could not complete the review.", "opus")
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_empty_output_is_warn_and_degraded(self):
        self.assertTrue(verdict.parse("", "opus")["degraded"])


class TestEvidenceBar(unittest.TestCase):
    def test_reuse_without_evidence_cannot_block(self):
        f = dict(GOOD_REUSE); f["evidence"] = ""
        r = verdict.parse(payload([f]), "opus")
        self.assertEqual(r["findings"][0]["severity"], "minor")
        self.assertEqual(r["verdict"], "warn")

    def test_reuse_with_evidence_can_block(self):
        r = verdict.parse(payload([GOOD_REUSE]), "opus")
        self.assertEqual(r["findings"][0]["severity"], "major")
        self.assertEqual(r["verdict"], "fail")

    def test_complexity_without_evidence_cannot_block(self):
        f = {"severity": "critical", "kind": "complexity", "file": "a.ts",
             "line": 1, "issue": "slow"}
        r = verdict.parse(payload([f]), "fable")
        self.assertEqual(r["findings"][0]["severity"], "minor")
        self.assertEqual(r["verdict"], "warn")

    def test_correctness_does_not_require_evidence(self):
        f = {"severity": "critical", "kind": "correctness", "file": "a.ts",
             "line": 9, "issue": "Null deref when supplier list is empty"}
        r = verdict.parse(payload([f]), "fable")
        self.assertEqual(r["verdict"], "fail")


class TestDropRules(unittest.TestCase):
    def test_hedging_findings_are_dropped(self):
        for hedge in ["Consider extracting this", "You might want to rename it",
                      "It would be cleaner to split this"]:
            f = dict(GOOD_REUSE); f["issue"] = hedge
            r = verdict.parse(payload([f]), "opus")
            self.assertEqual(r["findings"], [], hedge)

    def test_style_findings_are_dropped(self):
        f = {"severity": "major", "kind": "style", "file": "a.ts", "line": 1,
             "issue": "Use single quotes"}
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])

    def test_finding_without_file_is_dropped(self):
        f = dict(GOOD_REUSE); f.pop("file")
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])

    def test_unknown_kind_is_dropped(self):
        f = dict(GOOD_REUSE); f["kind"] = "vibes"
        self.assertEqual(verdict.parse(payload([f]), "opus")["findings"], [])


class TestDerivedVerdict(unittest.TestCase):
    def test_verdict_is_derived_not_trusted(self):
        f = {"severity": "critical", "kind": "correctness", "file": "a.ts",
             "line": 2, "issue": "Deletes rows without a where clause"}
        r = verdict.parse(payload([f], stated="pass"), "fable")
        self.assertEqual(r["stated"], "pass")
        self.assertEqual(r["verdict"], "fail")

    def test_fail_with_no_surviving_findings_becomes_pass(self):
        f = {"severity": "major", "kind": "style", "file": "a.ts", "line": 1,
             "issue": "Use tabs"}
        r = verdict.parse(payload([f], stated="fail"), "opus")
        self.assertEqual(r["verdict"], "pass")

    def test_only_minor_findings_yield_warn(self):
        f = {"severity": "minor", "kind": "decomposition", "file": "a.ts",
             "line": 1, "issue": "Function handles parsing and rendering"}
        self.assertEqual(verdict.parse(payload([f]), "opus")["verdict"], "warn")

    def test_no_findings_yields_pass(self):
        self.assertEqual(verdict.parse(payload([]), "opus")["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
