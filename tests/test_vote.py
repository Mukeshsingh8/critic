import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import vote


def V(critic, verdict_value, findings=None):
    return {"critic": critic, "stated": verdict_value, "verdict": verdict_value,
            "findings": findings or [], "degraded": False}


def F(severity="major", kind="correctness"):
    return {"severity": severity, "kind": kind, "file": "a.ts", "line": 1,
            "issue": "x", "evidence": "e", "suggestion": ""}


class TestVote(unittest.TestCase):
    def test_all_pass_is_pass(self):
        self.assertEqual(vote.decide([V("opus", "pass"), V("fable", "pass")], [])["decision"], "pass")

    def test_both_fail_blocks(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "fail", [F()])], [])
        self.assertEqual(r["decision"], "block")

    def test_one_fail_queues(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "queue")

    def test_any_critical_blocks_even_alone(self):
        r = vote.decide([V("opus", "fail", [F("critical")]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "block")

    def test_any_warn_queues(self):
        r = vote.decide([V("opus", "warn", [F("minor")]), V("fable", "pass")], [])
        self.assertEqual(r["decision"], "queue")

    def test_layer0_error_blocks_regardless(self):
        layer0 = [{"severity": "critical", "kind": "convention", "file": "a.ts",
                   "line": 3, "issue": "TS2345 type error", "evidence": "tsc",
                   "suggestion": "", "blocking": True}]
        r = vote.decide([V("opus", "pass"), V("fable", "pass")], layer0)
        self.assertEqual(r["decision"], "block")

    def test_layer0_non_blocking_only_queues(self):
        layer0 = [{"severity": "minor", "kind": "reuse", "file": "a.ts", "line": 3,
                   "issue": "duplicate block", "evidence": "jscpd",
                   "suggestion": "", "blocking": False}]
        r = vote.decide([V("opus", "pass"), V("fable", "pass")], layer0)
        self.assertEqual(r["decision"], "queue")

    def test_two_degraded_critics_never_block(self):
        a = {"critic": "opus", "stated": "", "verdict": "warn", "findings": [F("minor")], "degraded": True}
        b = {"critic": "fable", "stated": "", "verdict": "warn", "findings": [F("minor")], "degraded": True}
        self.assertEqual(vote.decide([a, b], [])["decision"], "queue")

    def test_findings_are_aggregated_across_sources(self):
        r = vote.decide([V("opus", "warn", [F("minor")]), V("fable", "warn", [F("minor")])],
                        [{"severity": "minor", "kind": "reuse", "file": "b.ts", "line": 1,
                          "issue": "dup", "evidence": "jscpd", "suggestion": "", "blocking": False}])
        self.assertEqual(len(r["findings"]), 3)

    def test_reason_is_non_empty_when_not_pass(self):
        r = vote.decide([V("opus", "fail", [F()]), V("fable", "fail", [F()])], [])
        self.assertTrue(r["reason"].strip())


if __name__ == "__main__":
    unittest.main()
