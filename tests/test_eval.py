import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cca_eval as evalmod

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestCases(unittest.TestCase):
    def test_every_case_file_exists(self):
        for case in evalmod.CASES:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "test-project", case["file"])),
                            case["file"])

    def test_there_is_a_negative_control(self):
        self.assertEqual(len([c for c in evalmod.CASES if c["expect"] == "clean"]), 1)

    def test_every_defect_names_its_expected_catcher(self):
        for case in evalmod.CASES:
            if case["expect"] == "defect":
                self.assertIn(case["critic"], ("design", "correctness", "schema"), case["id"])
                self.assertTrue(case["kind"])

    def test_all_five_planted_defect_classes_are_present(self):
        kinds = {c["kind"] for c in evalmod.CASES if c["expect"] == "defect"}
        self.assertEqual(kinds, {"reuse", "complexity", "schema", "convention", "decomposition"})


class TestSummarise(unittest.TestCase):
    def test_counts_catches_and_false_positives(self):
        results = [
            {"id": "a", "expect": "defect", "caught": True},
            {"id": "b", "expect": "defect", "caught": False},
            {"id": "c", "expect": "clean", "caught": True},
        ]
        s = evalmod.summarise(results)
        self.assertEqual(s["caught"], 1)
        self.assertEqual(s["missed"], 1)
        self.assertEqual(s["false_positives"], 1)


if __name__ == "__main__":
    unittest.main()
