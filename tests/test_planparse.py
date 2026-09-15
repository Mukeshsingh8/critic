import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import planparse

PLAN = """# Some Plan

Preamble that mentions Task 99 but is not a heading.

### Task 1: Event log

**Files:**
- Create: `scripts/ccalib/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Implement**

### Task 2: Verdict parsing

**Files:**
- Create: `scripts/ccalib/verdict.py`
- Modify: `scripts/critic_hook.py:12-40`
- Test: `tests/test_verdict.py`

- [ ] **Step 1: Something**

### Task 3: No files block

Just prose, no Files section.

- [ ] **Step 1: Something**
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.phases = planparse.parse(PLAN)

    def test_finds_every_task_heading(self):
        self.assertEqual([p["number"] for p in self.phases], [1, 2, 3])

    def test_captures_titles(self):
        self.assertEqual(self.phases[0]["title"], "Event log")

    def test_prose_mentioning_task_is_not_a_phase(self):
        self.assertNotIn(99, [p["number"] for p in self.phases])

    def test_collects_create_modify_and_test_paths(self):
        self.assertEqual(self.phases[1]["files"],
                         ["scripts/ccalib/verdict.py", "scripts/critic_hook.py",
                          "tests/test_verdict.py"])

    def test_strips_line_range_suffix(self):
        self.assertIn("scripts/critic_hook.py", self.phases[1]["files"])
        self.assertNotIn("scripts/critic_hook.py:12-40", self.phases[1]["files"])

    def test_task_without_files_block_has_empty_scope(self):
        self.assertEqual(self.phases[2]["files"], [])

    def test_line_ranges_bound_each_task(self):
        first, second = self.phases[0], self.phases[1]
        self.assertLess(first["start_line"], first["end_line"])
        self.assertLessEqual(first["end_line"], second["start_line"])

    def test_last_task_end_line_reaches_end_of_document(self):
        self.assertEqual(self.phases[-1]["end_line"], len(PLAN.splitlines()))

    def test_garbage_input_returns_empty_not_raises(self):
        self.assertEqual(planparse.parse("no headings here at all"), [])

    def test_empty_input_returns_empty(self):
        self.assertEqual(planparse.parse(""), [])


if __name__ == "__main__":
    unittest.main()
