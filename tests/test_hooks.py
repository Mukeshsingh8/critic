import json, os, stat, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CRITIC = os.path.join(ROOT, "scripts", "critic_hook.py")
DRAIN = os.path.join(ROOT, "scripts", "drain_hook.py")


def fake_claude(tmpdir, body):
    path = os.path.join(tmpdir, "claude")
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return tmpdir


def run(script, event, cwd, extra_env=None):
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = cwd
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([sys.executable, script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=60)
    return proc.returncode, out, err


class TestCriticHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _event(self, path="src/a.ts"):
        return {"tool_name": "Edit", "cwd": self.tmp,
                "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}

    def test_recursion_firewall_exits_immediately(self):
        code, out, _ = run(CRITIC, self._event(), self.tmp, {"CCA_INNER": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".cca", "events.jsonl")))

    def test_ignored_path_is_skipped(self):
        code, out, _ = run(CRITIC, self._event("node_modules/x/i.js"), self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_all_pass_is_silent_and_exit_zero(self):
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        code, out, _ = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_consensus_fail_emits_block_decision(self):
        canned = ('{"verdict":"fail","findings":[{"severity":"major",'
                  '"kind":"correctness","file":"src/a.ts","line":1,'
                  '"issue":"Null deref on empty list","evidence":"","suggestion":"guard"}]}')
        env = {"PATH": fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + canned + "'")
                       + os.pathsep + os.environ["PATH"]}
        code, out, _ = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_malformed_stdin_never_blocks_the_coder(self):
        proc = subprocess.Popen([sys.executable, CRITIC], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=self.tmp, universal_newlines=True)
        out, _ = proc.communicate("this is not json", timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out.strip(), "")

    def test_events_are_written(self):
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        run(CRITIC, self._event(), self.tmp, env)
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            kinds = [json.loads(l)["type"] for l in fh if l.strip()]
        self.assertIn("edit_started", kinds)
        self.assertIn("critic_verdict", kinds)
        self.assertIn("vote", kinds)


class TestDrainHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_no_cf_file_allows_stop(self):
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_open_items_block_stop(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("# Critic Feedback\n\n## 2026-09-14 10:00:00 -- a.ts\n- x\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_hook_active_never_loops(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 10:00:00 -- a.ts\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": True}, self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_wontfix_items_allow_stop(self):
        with open(os.path.join(self.tmp, "CF.md"), "w") as fh:
            fh.write("## [wontfix] 2026-09-14 -- a.ts\nreason: intentional\n")
        code, out, _ = run(DRAIN, {"stop_hook_active": False}, self.tmp)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()


import threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter as arbiter_mod

FAIL_JSON = ('{"verdict":"fail","findings":[{"severity":"major",'
             '"kind":"correctness","file":"src/a.ts","line":1,'
             '"issue":"Null deref on empty list","evidence":"","suggestion":"guard"}]}')


class TestCriticArbitration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 5}}, fh)

    def _beat(self, age=0.0):
        with open(os.path.join(self.tmp, ".cca", "arbiter.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "ts": time.time() - age, "host": "test"}, fh)

    def _env(self):
        return {"PATH": fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + FAIL_JSON + "'")
                        + os.pathsep + os.environ["PATH"]}

    def _event(self):
        return {"tool_name": "Edit", "cwd": self.tmp, "session_id": "sess0001",
                "tool_input": {"file_path": "src/a.ts", "old_string": "a", "new_string": "b"}}

    def _answer(self, choice, **extra):
        def run():
            pending = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 20
            while time.time() < deadline:
                names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
                names = [n for n in names if n.endswith(".json")]
                if names:
                    data = {"id": names[0][:-5], "choice": choice}
                    data.update(extra)
                    arbiter_mod.write_json(
                        os.path.join(self.tmp, ".cca", "decisions", names[0]), data)
                    return
                time.sleep(0.01)
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return thread

    def test_no_arbiter_blocks_exactly_as_today(self):
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_uphold_blocks(self):
        self._beat()
        self._answer("uphold")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_overrule_lets_the_edit_stand_silently(self):
        self._beat()
        self._answer("overrule", note="test-only helper")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CF.md")))

    def test_overrule_records_the_note(self):
        self._beat()
        self._answer("overrule", note="test-only helper")
        run(CRITIC, self._event(), self.tmp, self._env())
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            records = [json.loads(ln) for ln in fh if ln.strip()]
        notes = [r["payload"]["note"] for r in records if r["type"] == "arbitration"]
        self.assertIn("test-only helper", notes)

    def test_queue_writes_cf_md_and_does_not_block(self):
        self._beat()
        self._answer("queue")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        # "non-blocking" contains "block", so check the decision, not the text
        self.assertNotIn("decision", json.loads(out))
        self.assertIn("hookSpecificOutput", json.loads(out))
        with open(os.path.join(self.tmp, "CF.md")) as fh:
            self.assertIn("Null deref", fh.read())

    def test_stale_heartbeat_blocks_without_waiting(self):
        self._beat(age=120)
        started = time.time()
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertLess(time.time() - started, 20)

    def test_arbiter_disabled_blocks_without_asking(self):
        self._beat()
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"enabled": False}}, fh)
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))

    def test_a_passing_review_never_asks_the_human(self):
        self._beat()
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        _code, out, _err = run(CRITIC, self._event(), self.tmp, env)
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))


class TestEditIsRecorded(unittest.TestCase):
    """The board has to be able to show what the coder did, not just that it
    did something. That means the change rides in the event."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _run(self, tool_input, tool="Edit"):
        env = {"PATH": fake_claude(self.tmp, 'cat >/dev/null; printf \'{"verdict":"pass","findings":[]}\'')
                       + os.pathsep + os.environ["PATH"]}
        run(CRITIC, {"tool_name": tool, "cwd": self.tmp, "tool_input": tool_input}, self.tmp, env)
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            for line in fh:
                event = json.loads(line)
                if event["type"] == "edit_started":
                    return event["payload"]
        raise AssertionError("no edit_started event")

    def test_edit_records_the_diff_and_the_counts(self):
        payload = self._run({"file_path": "src/a.ts",
                             "old_string": "const x = 1;",
                             "new_string": "const x = 2;\nconst y = 3;"})
        self.assertEqual(payload["file"], "src/a.ts")
        self.assertEqual(payload["added"], 2)
        self.assertEqual(payload["removed"], 1)
        texts = [row["text"] for row in payload["excerpt"]]
        self.assertIn("const y = 3;", texts)

    def test_write_records_the_new_content(self):
        payload = self._run({"file_path": "src/b.ts", "content": "export const b = 1;\n"},
                            tool="Write")
        self.assertEqual(payload["added"], 1)
        self.assertFalse(payload["truncated"])

    def test_a_huge_write_does_not_bloat_the_log(self):
        payload = self._run({"file_path": "src/c.ts",
                             "content": "\n".join("line %d" % i for i in range(2000))},
                            tool="Write")
        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(json.dumps(payload)), 4000)


class TestLiveFixAndSkip(unittest.TestCase):
    """The operator expected clicking 'fix this' to stop the session and get it
    done before anything else. That only works while the block request is still
    open -- which is exactly when the PostToolUse hook is holding Claude."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "config.json"), "w") as fh:
            json.dump({"arbiter": {"block_wait": 8}}, fh)
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from ccalib import fixes
        self.fixes = fixes

    def _beat(self):
        with open(os.path.join(self.tmp, ".cca", "arbiter.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "ts": time.time(), "host": "test"}, fh)

    def _env(self):
        return {"PATH": fake_claude(self.tmp, "cat >/dev/null; printf '%s' '" + FAIL_JSON + "'")
                        + os.pathsep + os.environ["PATH"]}

    def _event(self):
        return {"tool_name": "Edit", "cwd": self.tmp, "session_id": "live",
                "tool_input": {"file_path": "src/a.ts", "old_string": "a", "new_string": "b"}}

    def _answer(self, choice, accept=None):
        def run_it():
            pending = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 20
            while time.time() < deadline:
                names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
                names = [n for n in names if n.endswith(".json")]
                if names:
                    request = arbiter_mod.read_json(os.path.join(pending, names[0])) or {}
                    if accept:
                        self.fixes.accept(self.tmp, accept)
                    arbiter_mod.write_json(
                        os.path.join(self.tmp, ".cca", "decisions", names[0]),
                        {"id": request.get("id"), "choice": choice})
                    return
                time.sleep(0.01)
        thread = threading.Thread(target=run_it)
        thread.daemon = True
        thread.start()

    FIX = {"file": "src/a.ts", "line": 1, "severity": "major", "kind": "correctness",
           "critic": "correctness", "issue": "Null deref on empty list",
           "suggestion": "Guard the empty case before indexing", "evidence": "line 1"}

    def test_the_block_offers_fix_as_an_option(self):
        self._beat()
        seen = {}

        def capture():
            pending = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 20
            while time.time() < deadline:
                names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
                names = [n for n in names if n.endswith(".json")]
                if names:
                    seen.update(arbiter_mod.read_json(os.path.join(pending, names[0])) or {})
                    arbiter_mod.write_json(
                        os.path.join(self.tmp, ".cca", "decisions", names[0]),
                        {"id": seen.get("id"), "choice": "uphold"})
                    return
                time.sleep(0.01)
        thread = threading.Thread(target=capture)
        thread.daemon = True
        thread.start()
        run(CRITIC, self._event(), self.tmp, self._env())
        self.assertIn("fix", seen["options"])

    def test_fix_hands_the_instruction_over_in_the_same_turn(self):
        self._beat()
        self._answer("fix", accept=self.FIX)
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Guard the empty case before indexing", payload["reason"])
        self.assertIn("Implement each one now", payload["reason"])

    def test_a_dispatched_fix_is_marked_sent_not_left_queued(self):
        self._beat()
        self._answer("fix", accept=self.FIX)
        run(CRITIC, self._event(), self.tmp, self._env())
        self.assertEqual(self.fixes.todo_fixes(self.tmp), [])
        self.assertEqual(self.fixes.load(self.tmp)[0]["status"], "sent")

    def test_fix_with_nothing_accepted_falls_back_to_the_plain_block(self):
        self._beat()
        self._answer("fix")
        _code, out, _err = run(CRITIC, self._event(), self.tmp, self._env())
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Critics rejected", payload["reason"])

    def test_a_skipped_finding_never_reaches_claude(self):
        self.fixes.skip(self.tmp, self.FIX)
        self.assertEqual(self.fixes.todo_fixes(self.tmp), [])
        self.assertEqual(self.fixes.load(self.tmp)[0]["status"], "dismissed")

    def test_skipping_does_not_claim_the_work_was_done(self):
        """A skipped finding and a fixed one must never be confused later."""
        self.fixes.skip(self.tmp, self.FIX)
        self.assertNotEqual(self.fixes.load(self.tmp)[0]["status"], "done")
