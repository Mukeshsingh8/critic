import json, os, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestManifest(unittest.TestCase):
    def test_plugin_json_is_valid_and_named(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["name"], "cca")
        self.assertTrue(manifest["description"].strip())
        self.assertTrue(manifest["version"].strip())

    def test_hooks_json_wires_both_entrypoints(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        post = hooks["PostToolUse"][0]
        self.assertEqual(post["matcher"], "Edit|Write")
        self.assertIn("critic_hook.py", post["hooks"][0]["command"])
        self.assertIn("drain_hook.py", hooks["Stop"][0]["hooks"][0]["command"])

    def test_hooks_use_the_stable_command_type(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        for event in hooks.values():
            for group in event:
                for hook in group["hooks"]:
                    self.assertEqual(hook["type"], "command")

    def test_hook_commands_reference_plugin_root(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            body = fh.read()
        self.assertIn("CLAUDE_PLUGIN_ROOT", body)

    def test_referenced_scripts_exist(self):
        for script in ("critic_hook.py", "drain_hook.py"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", script)), script)


if __name__ == "__main__":
    unittest.main()


class TestPhaseHooks(unittest.TestCase):
    def test_hooks_json_wires_the_phase_fence_and_session_context(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        pre = hooks["PreToolUse"][0]
        for tool in ("Edit", "Write"):
            self.assertIn(tool, pre["matcher"])
        self.assertIn("phase_hook.py", pre["hooks"][0]["command"])
        self.assertIn("session_hook.py", hooks["SessionStart"][0]["hooks"][0]["command"])

    def test_fence_matcher_includes_bash(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        self.assertIn("Bash", hooks["PreToolUse"][0]["matcher"])

    def test_critic_loop_does_not_run_on_bash(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        self.assertNotIn("Bash", hooks["PostToolUse"][0]["matcher"])

    def test_fence_matcher_includes_notebook_edit(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
            hooks = json.load(fh)["hooks"]
        self.assertIn("NotebookEdit", hooks["PreToolUse"][0]["matcher"])
