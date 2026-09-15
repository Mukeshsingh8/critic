import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import change


class TestSummarise(unittest.TestCase):
    def test_edit_counts_added_and_removed_lines(self):
        out = change.summarise("Edit", {
            "old_string": "a = 1\nb = 2\n",
            "new_string": "a = 1\nb = 3\nc = 4\n",
        })
        self.assertEqual(out["added"], 2)
        self.assertEqual(out["removed"], 1)

    def test_edit_excerpt_marks_each_line(self):
        out = change.summarise("Edit", {"old_string": "old\n", "new_string": "new\n"})
        signs = {row["sign"] for row in out["excerpt"]}
        self.assertIn("+", signs)
        self.assertIn("-", signs)
        texts = [row["text"] for row in out["excerpt"]]
        self.assertIn("new", texts)
        self.assertIn("old", texts)

    def test_write_counts_every_line_as_added(self):
        out = change.summarise("Write", {"content": "one\ntwo\nthree\n"})
        self.assertEqual(out["added"], 3)
        self.assertEqual(out["removed"], 0)

    def test_notebook_edit_reads_new_source(self):
        out = change.summarise("NotebookEdit", {"new_source": "import pandas\n"})
        self.assertEqual(out["added"], 1)
        self.assertIn("import pandas", [row["text"] for row in self._content(out)])

    def _content(self, out):
        """Excerpt rows carry `@@` hunk markers too; those are context, not code."""
        return [row for row in out["excerpt"] if row["sign"] != "@"]

    def test_hunk_markers_are_kept_so_multi_hunk_edits_are_not_misread(self):
        out = change.summarise("Edit", {
            "old_string": "\n".join("line %d" % i for i in range(40)),
            "new_string": "\n".join(("CHANGED" if i in (2, 30) else "line %d" % i)
                                     for i in range(40)),
        })
        self.assertTrue(any(row["sign"] == "@" for row in out["excerpt"]))

    def test_a_long_line_is_clipped_not_dropped(self):
        out = change.summarise("Write", {"content": "x" * 5000})
        line = self._content(out)[0]["text"]
        self.assertLessEqual(len(line), change.MAX_CHARS)
        self.assertTrue(line.startswith("xxx"))

    def test_a_huge_change_is_truncated_and_says_so(self):
        out = change.summarise("Write", {"content": "\n".join("line %d" % i for i in range(500))})
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(out["excerpt"]), change.MAX_LINES)
        self.assertEqual(out["added"], 500)  # the count is of the whole change

    def test_an_empty_change_is_not_an_error(self):
        out = change.summarise("Edit", {"old_string": "", "new_string": ""})
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["removed"], 0)
        self.assertEqual(out["excerpt"], [])

    def test_tabs_become_spaces_so_the_board_can_render_them(self):
        out = change.summarise("Write", {"content": "\tindented\n"})
        self.assertEqual(self._content(out)[0]["text"], "    indented")

    def test_missing_fields_do_not_raise(self):
        self.assertEqual(change.summarise("Edit", {})["added"], 0)
        self.assertEqual(change.summarise("Write", {})["added"], 0)


if __name__ == "__main__":
    unittest.main()
