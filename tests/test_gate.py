import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import gate

PHASE = {"number": 3, "title": "Critic runner",
         "files": ["scripts/ccalib/runner.py", "tests/test_runner.py"],
         "start_line": 0, "end_line": 8}

PLAN_BODY = """### Task 3: Critic runner

**Files:**
- Create: `scripts/ccalib/runner.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Implement**

### Task 4: Next

- [ ] **Step 1: Untouched**
"""


def cfg(test_command=""):
    return {"phases": {"enabled": True, "test_command": test_command, "done": []}}


class TestLadderOrder(unittest.TestCase):
    def test_failing_tests_block_at_the_first_rung(self):
        result = gate.evaluate(tempfile.mkdtemp(),
                               cfg("python3 -c 'import sys; sys.exit(1)'"), PHASE, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["rung"], "tests")

    def test_open_critic_items_block_before_commit(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.ts\n- finding\n")
        self.assertEqual(gate.evaluate(root, cfg("python3 -c 'pass'"), PHASE, {})["rung"],
                         "critics")

    def test_missing_commit_blocks_at_the_third_rung(self):
        self.assertEqual(
            gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE, {})["rung"],
            "commit")

    def test_dirty_tree_blocks_after_the_commit_exists(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE,
                               {3: "abc1234"}, dirty=["stray.txt"])
        self.assertEqual(result["rung"], "tree")

    def test_everything_satisfied_passes(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE,
                               {3: "abc1234"}, dirty=[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rung"], "")


class TestTestRung(unittest.TestCase):
    def test_absent_test_command_skips_rather_than_blocks(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg(""), PHASE, {})
        self.assertNotEqual(result["rung"], "tests")
        self.assertIsNone(result["checks"]["tests"])

    def test_passing_tests_record_true(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE, {})
        self.assertTrue(result["checks"]["tests"])


class TestCommitCommand(unittest.TestCase):
    def test_includes_every_phase_file_and_the_plan(self):
        command = gate.commit_command(PHASE, "docs/superpowers/plans/p.md")
        self.assertIn("scripts/ccalib/runner.py", command)
        self.assertIn("tests/test_runner.py", command)
        self.assertIn("docs/superpowers/plans/p.md", command)

    def test_message_carries_the_phase_tag_and_title(self):
        command = gate.commit_command(PHASE, "p.md")
        self.assertIn("[phase-3]", command)
        self.assertIn("Critic runner", command)

    def test_never_emits_push_or_init(self):
        command = gate.commit_command(PHASE, "p.md")
        self.assertNotIn("push", command)
        self.assertNotIn("init", command)


class TestTickSteps(unittest.TestCase):
    def _plan(self):
        path = os.path.join(tempfile.mkdtemp(), "plan.md")
        with open(path, "w") as fh:
            fh.write(PLAN_BODY)
        return path

    def test_ticks_only_the_named_phase(self):
        path = self._plan()
        phase = {"number": 3, "title": "Critic runner", "files": [],
                 "start_line": 0, "end_line": 7}
        ticked = gate.tick_steps(path, phase)
        with open(path) as fh:
            body = fh.read()
        self.assertEqual(ticked, 2)
        self.assertIn("- [x] **Step 1: Write the failing test**", body)
        self.assertIn("- [ ] **Step 1: Untouched**", body)

    def test_ticking_twice_is_idempotent(self):
        path = self._plan()
        phase = {"number": 3, "title": "x", "files": [], "start_line": 0, "end_line": 7}
        gate.tick_steps(path, phase)
        self.assertEqual(gate.tick_steps(path, phase), 0)

    def test_missing_file_returns_zero_not_raises(self):
        phase = {"number": 3, "title": "x", "files": [], "start_line": 0, "end_line": 7}
        self.assertEqual(gate.tick_steps("/nonexistent/plan.md", phase), 0)


if __name__ == "__main__":
    unittest.main()


class TestNothingToShip(unittest.TestCase):
    """Finishing a phase and stopping must not be blocked by the NEXT phase,
    which has not been started. A clean tree means there is nothing to ship."""

    def test_clean_tree_and_no_commit_means_phase_not_started(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE,
                               {}, dirty=[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["rung"], "")

    def test_dirty_tree_and_no_commit_still_blocks(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE,
                               {}, dirty=["parser.py"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["rung"], "commit")

    def test_undeterminable_tree_falls_back_to_blocking_on_the_commit(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), PHASE,
                               {}, dirty=None)
        self.assertEqual(result["rung"], "commit")


PHASE_LIST = [
    {"number": 2, "title": "Two", "files": ["b.py"], "start_line": 0, "end_line": 5},
    {"number": 3, "title": "Critic runner",
     "files": ["scripts/ccalib/runner.py", "tests/test_runner.py"],
     "start_line": 5, "end_line": 10},
    {"number": 4, "title": "Four", "files": ["d.py"], "start_line": 10, "end_line": 15},
    {"number": 5, "title": "Five", "files": ["e.py"], "start_line": 15, "end_line": 20},
]


class TestLaterPhaseWorkInTree(unittest.TestCase):
    """Dogfooding found rung 4 was unreachable: `current` is by definition the
    lowest UNSHIPPED phase, so has_commit is always False and evaluate() always
    returned at rung 3. The clean-tree check must run BEFORE the commit, where
    it can actually see later-phase work sitting in the tree."""

    def _phase3(self):
        return PHASE_LIST[1]

    def test_uncommitted_later_phase_work_blocks(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["scripts/ccalib/runner.py", "d.py"],
                               phase_list=PHASE_LIST)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rung"], "tree")
        self.assertIn("d.py", result["reason"])

    def test_message_names_the_owning_phase(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["e.py"], phase_list=PHASE_LIST)
        self.assertIn("Five", result["reason"])

    def test_only_current_phase_work_reaches_the_commit_rung(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["scripts/ccalib/runner.py"], phase_list=PHASE_LIST)
        self.assertEqual(result["rung"], "commit")

    def test_unlisted_files_do_not_block(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["scratch.log", "README.md"], phase_list=PHASE_LIST)
        self.assertEqual(result["rung"], "commit")

    def test_earlier_phase_fixes_do_not_block(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["b.py"], phase_list=PHASE_LIST)
        self.assertEqual(result["rung"], "commit")

    def test_without_a_phase_list_behaviour_is_unchanged(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=["d.py"])
        self.assertEqual(result["rung"], "commit")

    def test_clean_tree_still_means_not_started(self):
        result = gate.evaluate(tempfile.mkdtemp(), cfg("python3 -c 'pass'"), self._phase3(),
                               {}, dirty=[], phase_list=PHASE_LIST)
        self.assertTrue(result["ok"])


class TestSkipTests(unittest.TestCase):
    def test_skip_tests_does_not_run_the_command(self):
        import tempfile as tf
        counter = os.path.join(tf.mkdtemp(), "runs")
        root = tf.mkdtemp()
        cfg = {"phases": {"test_command":
                          "python3 -c \"open(r'%s','a').write('x')\"" % counter}}
        phase = {"number": 1, "title": "One", "files": ["a.py"],
                 "start_line": 0, "end_line": 1}
        gate.evaluate(root, cfg, phase, {}, dirty=[], skip_tests=True)
        self.assertFalse(os.path.exists(counter))

    def test_without_the_flag_the_command_runs(self):
        import tempfile as tf
        counter = os.path.join(tf.mkdtemp(), "runs")
        root = tf.mkdtemp()
        cfg = {"phases": {"test_command":
                          "python3 -c \"open(r'%s','a').write('x')\"" % counter}}
        phase = {"number": 1, "title": "One", "files": ["a.py"],
                 "start_line": 0, "end_line": 1}
        gate.evaluate(root, cfg, phase, {}, dirty=[])
        self.assertTrue(os.path.exists(counter))
