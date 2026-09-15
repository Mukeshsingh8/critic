import json, os, stat, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import runner

CANNED = json.dumps({"verdict": "fail", "findings": [{
    "severity": "major", "kind": "correctness", "file": "a.ts", "line": 2,
    "issue": "Off-by-one in the loop bound", "evidence": "", "suggestion": "Use <=",
}]})


def fake_claude(tmpdir, body):
    path = os.path.join(tmpdir, "claude")
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    os.environ["PATH"] = tmpdir + os.pathsep + os.environ["PATH"]
    return path


class TestBuildCommand(unittest.TestCase):
    def test_command_pins_model_and_readonly_tools(self):
        cmd = runner.build_command("claude-opus-5", "/briefs/design.md", ["Read", "Grep", "Glob"])
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5")
        self.assertIn("--allowedTools", cmd)
        self.assertNotIn("Edit", cmd)
        self.assertNotIn("Write", cmd)

    def test_command_uses_dontask_permission_mode(self):
        cmd = runner.build_command("claude-opus-5", "/b.md", ["Read"])
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")

    def test_command_passes_brief_as_system_prompt_file(self):
        cmd = runner.build_command("claude-opus-5", "/b.md", ["Read"])
        self.assertEqual(cmd[cmd.index("--system-prompt-file") + 1], "/b.md")


class TestRunOne(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.brief = os.path.join(self.tmp, "brief.md")
        with open(self.brief, "w") as fh:
            fh.write("be a critic")
        self._path = os.environ["PATH"]

    def tearDown(self):
        os.environ["PATH"] = self._path

    def test_parses_a_successful_critic_run(self):
        fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + CANNED + "'")
        r = runner.run_one("claude-opus-5", self.brief, "change", 30, "opus", self.tmp)
        self.assertEqual(r["verdict"], "fail")
        self.assertFalse(r["degraded"])

    def test_sets_recursion_firewall_env(self):
        fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
        r = runner.run_one("claude-opus-5", self.brief, "change", 30, "opus", self.tmp)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(os.environ.get("CCA_INNER"), None,
                         "runner must not leak CCA_INNER into the parent env")

    def test_timeout_is_warn_never_fail(self):
        fake_claude(self.tmp, "sleep 5")
        r = runner.run_one("claude-opus-5", self.brief, "change", 1, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_nonzero_exit_is_warn_never_fail(self):
        fake_claude(self.tmp, "exit 3")
        r = runner.run_one("claude-opus-5", self.brief, "change", 10, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])

    def test_missing_claude_binary_is_warn_never_fail(self):
        os.environ["PATH"] = "/nonexistent"
        r = runner.run_one("claude-opus-5", self.brief, "change", 10, "opus", self.tmp)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(r["degraded"])


class TestRunAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.brief = os.path.join(self.tmp, "brief.md")
        with open(self.brief, "w") as fh:
            fh.write("critic")
        self._path = os.environ["PATH"]

    def tearDown(self):
        os.environ["PATH"] = self._path

    def test_runs_every_critic_and_labels_results(self):
        fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
        critics = [
            {"name": "design", "model": "claude-opus-5", "brief": self.brief, "timeout": 30},
            {"name": "correctness", "model": "claude-fable-5-1", "brief": self.brief, "timeout": 30},
        ]
        results = runner.run_all(critics, "change", self.tmp)
        self.assertEqual({r["critic"] for r in results}, {"design", "correctness"})


if __name__ == "__main__":
    unittest.main()
