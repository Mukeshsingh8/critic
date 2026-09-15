import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import feedback


def F(issue="something wrong"):
    return {"severity": "minor", "kind": "reuse", "file": "a.ts", "line": 1,
            "issue": issue, "evidence": "lib/x.ts:1", "suggestion": "reuse it"}


class TestFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_append_creates_cf_md(self):
        feedback.append([F()], "a.ts", self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "CF.md")))

    def test_each_entry_is_a_heading_plus_findings(self):
        feedback.append([F("first")], "a.ts", self.tmp)
        with open(os.path.join(self.tmp, "CF.md")) as fh:
            body = fh.read()
        self.assertIn("## ", body)
        self.assertIn("first", body)

    def test_open_items_counts_headings(self):
        feedback.append([F()], "a.ts", self.tmp)
        feedback.append([F()], "b.ts", self.tmp)
        self.assertEqual(len(feedback.open_items(self.tmp)), 2)

    def test_wontfix_items_are_not_open(self):
        feedback.append([F()], "a.ts", self.tmp)
        path = os.path.join(self.tmp, "CF.md")
        with open(path) as fh:
            body = fh.read()
        with open(path, "w") as fh:
            fh.write(body.replace("## ", "## [wontfix] ", 1))
        self.assertEqual(feedback.open_items(self.tmp), [])

    def test_missing_file_has_no_open_items(self):
        self.assertEqual(feedback.open_items(self.tmp), [])

    def test_appending_empty_findings_writes_nothing(self):
        feedback.append([], "a.ts", self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CF.md")))


if __name__ == "__main__":
    unittest.main()
