import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import payload


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_edit_payload_contains_before_and_after(self):
        text = payload.build("Edit", {"file_path": "a.ts", "old_string": "OLD",
                                      "new_string": "NEW"}, self.tmp)
        self.assertIn("OLD", text)
        self.assertIn("NEW", text)
        self.assertIn("a.ts", text)

    def test_write_payload_contains_content(self):
        text = payload.build("Write", {"file_path": "b.ts", "content": "BODY"}, self.tmp)
        self.assertIn("BODY", text)

    def test_payload_includes_project_conventions_when_present(self):
        with open(os.path.join(self.tmp, "CLAUDE.md"), "w") as fh:
            fh.write("never use any")
        text = payload.build("Write", {"file_path": "b.ts", "content": "x"}, self.tmp)
        self.assertIn("never use any", text)

    def test_payload_includes_task_spec_when_present(self):
        with open(os.path.join(self.tmp, "PROMPT.md"), "w") as fh:
            fh.write("build the widget")
        text = payload.build("Write", {"file_path": "b.ts", "content": "x"}, self.tmp)
        self.assertIn("build the widget", text)

    def test_missing_context_files_do_not_raise(self):
        self.assertIn("b.ts", payload.build("Write", {"file_path": "b.ts", "content": "x"}, self.tmp))

    def test_huge_content_is_truncated(self):
        text = payload.build("Write", {"file_path": "b.ts", "content": "x" * 200000}, self.tmp)
        self.assertLess(len(text), 100000)


if __name__ == "__main__":
    unittest.main()
