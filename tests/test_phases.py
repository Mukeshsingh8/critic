import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import config, phases

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`
- Test: `tests/test_b.py`

- [ ] **Step 1: x**

### Task 3: Three

**Files:**
- Create: `c.py`

- [ ] **Step 1: x**
"""


def project_with_plan(body=PLAN, name="2026-09-14-p.md"):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, name), "w") as fh:
        fh.write(body)
    return root


class TestActivePlan(unittest.TestCase):
    def test_none_when_no_plans_directory(self):
        cfg = config.load(tempfile.mkdtemp())
        self.assertIsNone(phases.active_plan_path(tempfile.mkdtemp(), cfg))

    def test_finds_the_only_plan(self):
        root = project_with_plan()
        self.assertTrue(phases.active_plan_path(root, config.load(root)).endswith("p.md"))

    def test_config_pin_wins(self):
        root = project_with_plan()
        os.makedirs(os.path.join(root, ".cca"))
        pinned = "docs/superpowers/plans/2026-09-14-p.md"
        with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"enabled": True, "plan": pinned}}, fh)
        cfg = config.load(root)
        self.assertTrue(phases.active_plan_path(root, cfg).endswith("p.md"))


class TestCurrent(unittest.TestCase):
    def setUp(self):
        self.phases = phases.load(project_with_plan(), config.load(tempfile.mkdtemp()))

    def test_first_phase_when_nothing_shipped(self):
        self.assertEqual(phases.current(self.phases, {}, {})["number"], 1)

    def test_lowest_unshipped_phase(self):
        self.assertEqual(phases.current(self.phases, {1: "aaa"}, {})["number"], 2)

    def test_gap_in_shipped_phases_picks_the_lowest_missing(self):
        self.assertEqual(phases.current(self.phases, {1: "a", 3: "c"}, {})["number"], 2)

    def test_none_when_all_shipped(self):
        self.assertIsNone(phases.current(self.phases, {1: "a", 2: "b", 3: "c"}, {}))

    def test_config_done_override_is_honoured(self):
        cfg = {"phases": {"done": [1, 2]}}
        self.assertEqual(phases.current(self.phases, {}, cfg)["number"], 3)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.phases = phases.load(project_with_plan(), config.load(tempfile.mkdtemp()))
        self.current = phases.current(self.phases, {1: "aaa"}, {})  # phase 2

    def test_current_phase_file(self):
        self.assertEqual(phases.classify("b.py", self.phases, self.current), "current")

    def test_later_phase_file(self):
        self.assertEqual(phases.classify("c.py", self.phases, self.current), "later")

    def test_earlier_phase_file(self):
        self.assertEqual(phases.classify("a.py", self.phases, self.current), "earlier")

    def test_unlisted_file(self):
        self.assertEqual(phases.classify("zzz.py", self.phases, self.current), "unlisted")

    def test_everything_unlisted_when_there_is_no_current_phase(self):
        self.assertEqual(phases.classify("c.py", self.phases, None), "unlisted")

    def test_path_matching_is_suffix_tolerant(self):
        self.assertEqual(phases.classify("./b.py", self.phases, self.current), "current")


if __name__ == "__main__":
    unittest.main()
