import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import config


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_config_returns_defaults(self):
        cfg = config.load(self.tmp)
        self.assertEqual(cfg["critics"][0]["model"], "claude-opus-5")
        # the shipped default is deliberately the affordable roster
        self.assertEqual(cfg["critics"][1]["model"], "claude-haiku-4-5")

    def test_user_config_overrides_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"critics": [{"name": "solo", "model": "claude-haiku-4-5",
                                    "brief": "correctness", "timeout": 30}]}, fh)
        cfg = config.load(self.tmp)
        self.assertEqual(len(cfg["critics"]), 1)
        self.assertEqual(cfg["critics"][0]["model"], "claude-haiku-4-5")

    def test_corrupt_config_falls_back_to_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            fh.write("{ not json")
        self.assertEqual(len(config.load(self.tmp)["critics"]), 2)

    def test_partial_nested_block_keeps_sibling_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 60}}, fh)
        arbiter = config.load(self.tmp)["arbiter"]
        self.assertEqual(arbiter["block_wait"], 60)
        self.assertEqual(arbiter["stale_after"], 15)
        self.assertEqual(arbiter["commit_wait"], 300)
        self.assertTrue(arbiter["enabled"])

    def test_partial_phases_block_keeps_sibling_defaults(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"phases": {"test_command": "make test"}}, fh)
        phases = config.load(self.tmp)["phases"]
        self.assertEqual(phases["test_command"], "make test")
        self.assertTrue(phases["enabled"])
        self.assertEqual(phases["done"], [])

    def test_lists_are_replaced_wholesale_not_merged(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"critics": [{"name": "solo", "model": "claude-haiku-4-5",
                                    "brief": "correctness", "timeout": 30}]}, fh)
        self.assertEqual(len(config.load(self.tmp)["critics"]), 1)

    def test_arbiter_defaults(self):
        arbiter = config.load(self.tmp)["arbiter"]
        self.assertTrue(arbiter["enabled"])
        self.assertEqual(arbiter["block_wait"], 180)
        self.assertEqual(arbiter["commit_wait"], 300)
        self.assertEqual(arbiter["stale_after"], 15)

    def test_defaults_are_not_mutated_between_loads(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 1}}, fh)
        config.load(self.tmp)
        self.assertEqual(config.load(tempfile.mkdtemp())["arbiter"]["block_wait"], 180)


class TestShouldReview(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_source_files_are_reviewed(self):
        self.assertTrue(config.should_review("src/app.ts", self.cfg))

    def test_ignored_globs_are_skipped(self):
        for path in ["node_modules/x/index.js", ".cca/events.jsonl",
                     "CF.md", "dist/bundle.js", "package-lock.json"]:
            self.assertFalse(config.should_review(path, self.cfg), path)


class TestCriticsFor(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_normal_path_gets_the_two_standing_critics(self):
        names = [c["name"] for c in config.critics_for("src/a.ts", self.cfg, "/plugin")]
        self.assertEqual(names, ["design", "correctness"])

    def test_migration_path_adds_the_schema_critic(self):
        names = [c["name"] for c in
                 config.critics_for("src/migrations/1712-add-col.ts", self.cfg, "/plugin")]
        self.assertIn("schema", names)

    def test_brief_paths_are_absolute_under_plugin_root(self):
        critics = config.critics_for("src/a.ts", self.cfg, "/plugin")
        self.assertEqual(critics[0]["brief"], "/plugin/critics/design.md")


if __name__ == "__main__":
    unittest.main()
