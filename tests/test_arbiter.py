import json, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter, config


def beat(root, age=0.0, **extra):
    """Write a heartbeat file aged `age` seconds into the past."""
    os.makedirs(os.path.join(root, ".cca"), exist_ok=True)
    data = {"pid": os.getpid(), "ts": time.time() - age, "host": "test", "version": "0.1.0"}
    data.update(extra)
    with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
        json.dump(data, fh)


class TestPrimitives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)

    def test_id_is_unique_and_filename_safe(self):
        made = {arbiter.new_id("sess/../../etc") for _ in range(50)}
        self.assertEqual(len(made), 50)
        for ident in made:
            self.assertNotIn("/", ident)
            self.assertNotIn("..", ident)

    def test_id_survives_an_empty_session(self):
        self.assertTrue(arbiter.new_id(""))

    def test_write_json_is_atomic_and_leaves_no_temp_file(self):
        target = os.path.join(self.tmp, ".cca", "pending", "a.json")
        arbiter.write_json(target, {"hello": "world"})
        with open(target) as fh:
            self.assertEqual(json.load(fh)["hello"], "world")
        self.assertEqual(os.listdir(os.path.dirname(target)), ["a.json"])

    def test_read_json_returns_none_for_missing_corrupt_and_non_object(self):
        missing = os.path.join(self.tmp, "nope.json")
        self.assertIsNone(arbiter.read_json(missing))
        corrupt = os.path.join(self.tmp, "bad.json")
        with open(corrupt, "w") as fh:
            fh.write("{ not json")
        self.assertIsNone(arbiter.read_json(corrupt))
        listy = os.path.join(self.tmp, "list.json")
        with open(listy, "w") as fh:
            fh.write("[1, 2]")
        self.assertIsNone(arbiter.read_json(listy))


class TestPresent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)

    def test_absent_without_a_heartbeat(self):
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_present_with_a_fresh_heartbeat(self):
        beat(self.tmp)
        self.assertTrue(arbiter.present(self.tmp, self.cfg))

    def test_absent_with_a_stale_heartbeat(self):
        beat(self.tmp, age=60)
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_disabled_in_config(self):
        beat(self.tmp)
        self.cfg["arbiter"]["enabled"] = False
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_heartbeat_is_corrupt(self):
        os.makedirs(os.path.join(self.tmp, ".cca"), exist_ok=True)
        with open(os.path.join(self.tmp, ".cca", "arbiter.json"), "w") as fh:
            fh.write("{ not json")
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_timestamp_is_not_a_number(self):
        beat(self.tmp, ts="soon")
        self.assertFalse(arbiter.present(self.tmp, self.cfg))

    def test_absent_when_the_heartbeat_is_from_the_future(self):
        """A clock skew that reads as -3600s must not read as 'fresh'."""
        beat(self.tmp, age=-3600)
        self.assertFalse(arbiter.present(self.tmp, self.cfg))


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _pending(self, name, deadline):
        arbiter.write_json(os.path.join(self.tmp, ".cca", "pending", name),
                           {"id": name[:-5], "deadline": deadline})

    def _decision(self, name):
        arbiter.write_json(os.path.join(self.tmp, ".cca", "decisions", name),
                           {"id": name[:-5], "choice": "uphold"})

    def test_expired_pending_is_removed(self):
        self._pending("old.json", deadline=time.time() - 3600)
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_live_pending_survives(self):
        self._pending("live.json", deadline=time.time() + 3600)
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), ["live.json"])

    def test_corrupt_pending_is_removed(self):
        os.makedirs(os.path.join(self.tmp, ".cca", "pending"), exist_ok=True)
        with open(os.path.join(self.tmp, ".cca", "pending", "bad.json"), "w") as fh:
            fh.write("{ not json")
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_orphan_decision_is_removed_once_stale(self):
        self._decision("orphan.json")
        arbiter.sweep(self.tmp, now=time.time() + 3600)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), [])

    def test_a_decision_for_a_live_pending_is_kept(self):
        self._pending("live.json", deadline=time.time() + 3600)
        self._decision("live.json")
        arbiter.sweep(self.tmp, now=time.time() + 3600)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), ["live.json"])

    def test_a_fresh_orphan_decision_is_kept(self):
        """A decision written microseconds before its pending file appears must
        not be swept out from under the hook."""
        self._decision("racing.json")
        arbiter.sweep(self.tmp)
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), ["racing.json"])

    def test_sweep_on_a_project_with_no_cca_dir_is_silent(self):
        arbiter.sweep(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()


import threading


def answer_after(root, delay, choice, **extra):
    """A fake arbiter: wait for the request to appear, then answer it.
    Returns the thread so a test can join it."""
    def run():
        deadline = time.time() + 10
        pending_dir = os.path.join(root, ".cca", "pending")
        ident = None
        while time.time() < deadline and ident is None:
            names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
            names = [n for n in names if n.endswith(".json")]
            if names:
                ident = names[0]
            else:
                time.sleep(0.01)
        if ident is None:
            return
        time.sleep(delay)
        data = {"id": ident[:-5], "choice": choice, "ts": time.time()}
        data.update(extra)
        arbiter.write_json(os.path.join(root, ".cca", "decisions", ident), data)

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
    return thread


BLOCK = {"options": ["uphold", "queue", "overrule"], "file": "src/a.ts",
         "reason": "critics rejected", "findings": [], "session_id": "abc123"}


class TestWaitFor(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(tempfile.mkdtemp())

    def test_uses_the_configured_wait_when_the_budget_allows(self):
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=0.0), 180.0)
        self.assertEqual(arbiter.wait_for(self.cfg, "commit", elapsed=0.0), 300.0)

    def test_clamps_to_what_is_left_of_the_hook_budget(self):
        # block budget 600, 500s already spent, 10s margin -> 90s left
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=500.0), 90.0)

    def test_never_returns_a_negative_wait(self):
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=10000.0), 0.0)

    def test_a_non_numeric_wait_falls_back_to_the_default(self):
        self.cfg["arbiter"]["block_wait"] = "soon"
        self.assertEqual(arbiter.wait_for(self.cfg, "block", elapsed=0.0), 180.0)


class TestRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 3

    def _events(self, kind):
        from ccalib import events as ev
        return [e["payload"] for e in ev.read_all(self.tmp) if e["type"] == kind]

    def test_no_heartbeat_means_no_request_and_no_wait(self):
        started = time.time()
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertLess(time.time() - started, 1.0)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))
        self.assertFalse(self._events("arbitration")[0]["present"])

    def test_stale_heartbeat_means_no_request(self):
        beat(self.tmp, age=60)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertFalse(self._events("arbitration")[0]["present"])

    def test_disabled_means_no_request(self):
        beat(self.tmp)
        self.cfg["arbiter"]["enabled"] = False
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))

    def test_a_decision_is_returned_and_both_files_are_removed(self):
        beat(self.tmp)
        answer_after(self.tmp, 0.1, "overrule", note="test-only helper")
        decision = arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(decision["choice"], "overrule")
        self.assertEqual(decision["note"], "test-only helper")
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "decisions")), [])

    def test_the_request_carries_the_payload_and_a_deadline(self):
        beat(self.tmp)
        seen = {}

        def capture():
            pending_dir = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 5
            while time.time() < deadline:
                names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
                if names:
                    seen.update(arbiter.read_json(os.path.join(pending_dir, names[0])) or {})
                    arbiter.write_json(os.path.join(self.tmp, ".cca", "decisions", names[0]),
                                       {"id": seen.get("id"), "choice": "uphold"})
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=capture)
        thread.daemon = True
        thread.start()
        arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        thread.join(timeout=5)
        self.assertEqual(seen["kind"], "block")
        self.assertEqual(seen["file"], "src/a.ts")
        self.assertEqual(seen["options"], ["uphold", "queue", "overrule"])
        self.assertGreater(seen["deadline"], seen["created"])

    def test_timeout_returns_none_and_cleans_up(self):
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 0.5
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])
        record = self._events("arbitration")[0]
        self.assertTrue(record["timed_out"])
        self.assertTrue(record["present"])

    def test_a_choice_outside_options_is_ignored(self):
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 1
        answer_after(self.tmp, 0.05, "delete-the-repo")
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK)))

    def test_a_malformed_decision_does_not_end_the_wait(self):
        """Garbage must be ignored, and a good answer arriving afterwards
        must still be honoured."""
        beat(self.tmp)
        self.cfg["arbiter"]["block_wait"] = 4

        def scribble_then_answer():
            pending_dir = os.path.join(self.tmp, ".cca", "pending")
            deadline = time.time() + 5
            while time.time() < deadline:
                names = [n for n in os.listdir(pending_dir)] if os.path.isdir(pending_dir) else []
                if names:
                    target = os.path.join(self.tmp, ".cca", "decisions", names[0])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "w") as fh:
                        fh.write("{ not json")
                    time.sleep(0.5)
                    arbiter.write_json(target, {"id": names[0][:-5], "choice": "queue"})
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=scribble_then_answer)
        thread.daemon = True
        thread.start()
        decision = arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(decision["choice"], "queue")

    def test_leftovers_from_a_crash_are_swept_before_asking(self):
        beat(self.tmp)
        arbiter.write_json(os.path.join(self.tmp, ".cca", "pending", "ancient.json"),
                           {"id": "ancient", "deadline": time.time() - 3600})
        self.cfg["arbiter"]["block_wait"] = 0.5
        arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK))
        self.assertEqual(os.listdir(os.path.join(self.tmp, ".cca", "pending")), [])

    def test_zero_budget_skips_the_request_entirely(self):
        beat(self.tmp)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", dict(BLOCK),
                                          elapsed=10000.0))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".cca", "pending")))

    def test_an_internal_error_returns_none_rather_than_raising(self):
        beat(self.tmp)
        self.assertIsNone(arbiter.request(self.tmp, self.cfg, "block", {"options": "not a list"}))

    def test_commit_decisions_carry_ok_and_error_through(self):
        beat(self.tmp)
        self.cfg["arbiter"]["commit_wait"] = 3
        payload = {"options": ["commit", "edit", "later"], "session_id": "s"}
        answer_after(self.tmp, 0.1, "commit", ok=False, error="nothing to commit")
        decision = arbiter.request(self.tmp, self.cfg, "commit", payload)
        self.assertEqual(decision["choice"], "commit")
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["error"], "nothing to commit")
