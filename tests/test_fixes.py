import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import fixes

FINDING = {
    "file": "src/pricing.ts", "line": 42, "severity": "critical", "kind": "reuse",
    "critic": "design", "issue": "Reimplements applyDiscount()",
    "suggestion": "Call applyDiscount() from lib/pricing.ts", "evidence": "lib/pricing.ts:88",
}


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_file_is_an_empty_queue(self):
        self.assertEqual(fixes.load(self.tmp), [])
        self.assertEqual(fixes.open_fixes(self.tmp), [])

    def test_corrupt_file_never_raises(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "fixes.json"), "w") as fh:
            fh.write("{ not json")
        self.assertEqual(fixes.load(self.tmp), [])

    def test_accepting_queues_it_as_todo(self):
        item = fixes.accept(self.tmp, FINDING)
        self.assertEqual(item["status"], fixes.TODO)
        self.assertEqual(item["suggestion"], FINDING["suggestion"])
        self.assertEqual(len(fixes.todo_fixes(self.tmp)), 1)

    def test_accepting_the_same_remedy_twice_is_one_item(self):
        fixes.accept(self.tmp, FINDING)
        fixes.accept(self.tmp, dict(FINDING))
        self.assertEqual(len(fixes.load(self.tmp)), 1)

    def test_two_remedies_on_one_line_are_separate_items(self):
        fixes.accept(self.tmp, FINDING)
        other = dict(FINDING, suggestion="Sum in minor units, round once")
        fixes.accept(self.tmp, other)
        self.assertEqual(len(fixes.load(self.tmp)), 2)

    def test_written_atomically_leaving_no_temp_file(self):
        fixes.accept(self.tmp, FINDING)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, ".cca"))), ["fixes.json"])

    def test_mark_sent_moves_it_out_of_todo_but_stays_open(self):
        item = fixes.accept(self.tmp, FINDING)
        self.assertEqual(fixes.mark_sent(self.tmp, [item["id"]]), 1)
        self.assertEqual(fixes.todo_fixes(self.tmp), [])
        self.assertEqual(len(fixes.open_fixes(self.tmp)), 1)
        self.assertIsNotNone(fixes.load(self.tmp)[0]["sent_at"])

    def test_a_clean_verdict_on_the_file_resolves_its_fixes(self):
        item = fixes.accept(self.tmp, FINDING)
        fixes.mark_sent(self.tmp, [item["id"]])
        self.assertEqual(fixes.resolve_file(self.tmp, "src/pricing.ts"), 1)
        self.assertEqual(fixes.open_fixes(self.tmp), [])
        self.assertEqual(fixes.load(self.tmp)[0]["status"], fixes.DONE)

    def test_a_clean_verdict_on_another_file_resolves_nothing(self):
        fixes.accept(self.tmp, FINDING)
        self.assertEqual(fixes.resolve_file(self.tmp, "src/other.ts"), 0)
        self.assertEqual(len(fixes.open_fixes(self.tmp)), 1)

    def test_dismiss_closes_it_without_claiming_it_was_done(self):
        item = fixes.accept(self.tmp, FINDING)
        fixes.dismiss(self.tmp, item["id"])
        self.assertEqual(fixes.load(self.tmp)[0]["status"], fixes.DISMISSED)
        self.assertEqual(fixes.open_fixes(self.tmp), [])

    def test_re_accepting_a_done_fix_reopens_it(self):
        item = fixes.accept(self.tmp, FINDING)
        fixes.resolve_file(self.tmp, "src/pricing.ts")
        again = fixes.accept(self.tmp, FINDING)
        self.assertEqual(again["status"], fixes.TODO)
        self.assertEqual(len(fixes.load(self.tmp)), 1)

    def test_the_queue_is_bounded(self):
        for index in range(fixes.MAX_FIXES + 25):
            fixes.accept(self.tmp, dict(FINDING, suggestion="fix %d" % index))
        self.assertEqual(len(fixes.load(self.tmp)), fixes.MAX_FIXES)


class TestDirective(unittest.TestCase):
    def test_empty_queue_has_no_directive(self):
        self.assertEqual(fixes.directive([]), "")

    def test_directive_is_imperative_and_carries_the_detail(self):
        text = fixes.directive([dict(FINDING, id="a")])
        self.assertIn("Call applyDiscount() from lib/pricing.ts", text)
        self.assertIn("src/pricing.ts:42", text)
        self.assertIn("Reimplements applyDiscount()", text)
        self.assertIn("lib/pricing.ts:88", text)
        self.assertIn("Do not ask whether to proceed", text)

    def test_a_fix_with_no_suggestion_still_reads(self):
        text = fixes.directive([{"file": "a.ts", "line": 1, "suggestion": ""}])
        self.assertIn("no suggestion recorded", text)


if __name__ == "__main__":
    unittest.main()
