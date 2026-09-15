import os, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
BRIEFS = ("design", "correctness", "schema")


class TestBriefs(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(ROOT, "critics", "%s.md" % name)) as fh:
            return fh.read()

    def test_all_briefs_exist(self):
        for name in BRIEFS:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "critics", "%s.md" % name)), name)

    def test_every_brief_demands_json_only(self):
        for name in BRIEFS:
            self.assertIn("JSON", self._read(name), name)

    def test_every_brief_forbids_writing_files(self):
        for name in BRIEFS:
            self.assertIn("never", self._read(name).lower(), name)

    def test_every_brief_states_the_evidence_requirement(self):
        for name in BRIEFS:
            self.assertIn("evidence", self._read(name).lower(), name)

    def test_briefs_forbid_hedging(self):
        for name in BRIEFS:
            self.assertIn("consider", self._read(name).lower(), name)

    def test_design_brief_requires_grep_before_claiming_reuse(self):
        self.assertIn("Grep", self._read("design"))

    def test_correctness_brief_calibrates_complexity_to_data_size(self):
        self.assertIn("data size", self._read("correctness").lower())

    def test_schema_brief_covers_production_drift(self):
        body = self._read("schema").lower()
        for term in ("existing rows", "rollback", "cascade"):
            self.assertIn(term, body, term)


if __name__ == "__main__":
    unittest.main()
