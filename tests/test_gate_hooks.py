import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DRAIN = os.path.join(ROOT, "scripts", "drain_hook.py")
SESSION = os.path.join(ROOT, "scripts", "session_hook.py")

PLAN = """### Task 1: One

**Files:**
- Create: `a.py`

- [ ] **Step 1: x**

### Task 2: Two

**Files:**
- Create: `b.py`

- [ ] **Step 1: x**
"""


def git(root, *args):
    subprocess.check_call(["git"] + list(args), cwd=root,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def project(shipped=(), test_command="python3 -c 'pass'"):
    root = tempfile.mkdtemp()
    plans = os.path.join(root, "docs", "superpowers", "plans")
    os.makedirs(plans)
    with open(os.path.join(plans, "p.md"), "w") as fh:
        fh.write(PLAN)
    os.makedirs(os.path.join(root, ".cca"))
    with open(os.path.join(root, ".cca", "config.json"), "w") as fh:
        json.dump({"phases": {"enabled": True, "test_command": test_command, "done": []}}, fh)
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write(".cca/\nCF.md\n")
    git(root, "init")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    for number in shipped:
        name = "shipped%d.txt" % number
        with open(os.path.join(root, name), "w") as fh:
            fh.write("x")
        git(root, "add", name)
        git(root, "commit", "-m", "[phase-%d] done" % number)
    return root


def run(script, event, root):
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = root
    proc = subprocess.Popen([sys.executable, script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=root, universal_newlines=True)
    out, err = proc.communicate(json.dumps(event), timeout=120)
    return proc.returncode, out, err


class TestDrainGate(unittest.TestCase):
    def test_blocks_when_phase_has_no_commit(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work, not yet committed")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("git add", payload["reason"])
        self.assertIn("[phase-1]", payload["reason"])

    def test_ticks_the_plan_checkboxes_when_suggesting_the_commit(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work, not yet committed")
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        with open(os.path.join(root, "docs", "superpowers", "plans", "p.md")) as fh:
            body = fh.read()
        self.assertIn("- [x] **Step 1: x**", body)

    def test_allows_stop_when_every_phase_has_shipped(self):
        root = project(shipped=[1, 2])
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")

    def test_dirty_tree_blocks_after_the_commit_exists(self):
        root = project(shipped=[1])
        with open(os.path.join(root, "b.py"), "w") as fh:
            fh.write("uncommitted work for phase 2")
        # phase 2 has no commit yet, so this blocks at the commit rung first
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_cf_items_block_before_the_phase_gate(self):
        root = project()
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.py\n- finding\n")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("CF.md", json.loads(out)["reason"])

    def test_failing_tests_block_before_the_commit_rung(self):
        root = project(test_command="python3 -c 'import sys; sys.exit(1)'")
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("tests are failing", json.loads(out)["reason"])

    def test_stop_hook_active_never_loops(self):
        root = project()
        _code, out, _err = run(DRAIN, {"stop_hook_active": True, "cwd": root}, root)
        self.assertEqual(out.strip(), "")

    def test_no_plan_allows_stop(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")


    def test_clean_tree_with_no_phase_commit_allows_stop(self):
        """Finishing a phase and stopping must not be blocked by the next phase,
        which has not been started yet."""
        root = project(shipped=[1])
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")


class TestSessionContext(unittest.TestCase):
    def test_announces_the_current_phase(self):
        root = project(shipped=[1])
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("phase 2", context.lower())
        self.assertIn("Two", context)

    def test_lists_shipped_phases(self):
        root = project(shipped=[1])
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("One", context)

    def test_silent_when_no_plan(self):
        root = tempfile.mkdtemp()
        git(root, "init")
        _code, out, _err = run(SESSION, {"cwd": root}, root)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()


class TestLumpDetection(unittest.TestCase):
    """The end-to-end version of the defect dogfooding found: later-phase work
    sitting uncommitted must block, not be quietly carved into tidy commits."""

    def test_later_phase_work_blocks_at_the_tree_rung(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        with open(os.path.join(root, "b.py"), "w") as fh:
            fh.write("phase 2 work, written ahead of time")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        reason = json.loads(out)["reason"]
        self.assertIn("later phase", reason.lower())
        self.assertIn("b.py", reason)

    def test_current_phase_work_alone_still_offers_the_commit(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work only")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("git add", json.loads(out)["reason"])


import threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import arbiter as arbiter_mod


def beat(root, age=0.0):
    os.makedirs(os.path.join(root, ".cca"), exist_ok=True)
    with open(os.path.join(root, ".cca", "arbiter.json"), "w") as fh:
        json.dump({"pid": os.getpid(), "ts": time.time() - age, "host": "test"}, fh)


def enable_arbiter(root, **overrides):
    path = os.path.join(root, ".cca", "config.json")
    with open(path) as fh:
        cfg = json.load(fh)
    arbiter_cfg = {"commit_wait": 20}
    arbiter_cfg.update(overrides)
    cfg["arbiter"] = arbiter_cfg
    with open(path, "w") as fh:
        json.dump(cfg, fh)


def answer_commit(root, choice="commit", do_git=True, ok=True, error=""):
    """Stand in for the extension: read the request, optionally really run git,
    then write the decision."""
    def run():
        pending = os.path.join(root, ".cca", "pending")
        deadline = time.time() + 30
        while time.time() < deadline:
            names = [n for n in os.listdir(pending)] if os.path.isdir(pending) else []
            names = [n for n in names if n.endswith(".json")]
            if names:
                request = arbiter_mod.read_json(os.path.join(pending, names[0])) or {}
                committed = ok
                if do_git and ok:
                    files = [f for f in request.get("phase", {}).get("files", [])
                             if os.path.exists(os.path.join(root, f))]
                    if request.get("plan_rel"):
                        files.append(request["plan_rel"])
                    try:
                        git(root, "add", "--", *files)
                        git(root, "commit", "-m", request.get("message", "x"))
                    except subprocess.CalledProcessError:
                        committed = False
                arbiter_mod.write_json(
                    os.path.join(root, ".cca", "decisions", names[0]),
                    {"id": request.get("id"), "choice": choice,
                     "ok": committed, "error": error})
                return
            time.sleep(0.01)
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
    return thread


def events_of(root, kind):
    path = os.path.join(root, ".cca", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(ln)["payload"] for ln in fh
                if ln.strip() and json.loads(ln)["type"] == kind]


class TestCommitArbitration(unittest.TestCase):
    def _started(self):
        root = project()
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        return root

    def test_no_arbiter_blocks_with_the_command_as_today(self):
        root = self._started()
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("git add", json.loads(out)["reason"])

    def test_later_blocks_with_the_command(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root, choice="later", do_git=False)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("git add", json.loads(out)["reason"])
        self.assertIn("[phase-1]", json.loads(out)["reason"])

    def test_commit_creates_the_commit_and_allows_the_stop(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(out.strip(), "")
        log = subprocess.check_output(["git", "log", "--format=%s"], cwd=root,
                                      universal_newlines=True)
        self.assertIn("[phase-1] One", log)

    def test_the_committed_plan_carries_the_ticked_checkbox(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        show = subprocess.check_output(
            ["git", "show", "HEAD:docs/superpowers/plans/p.md"], cwd=root,
            universal_newlines=True)
        self.assertIn("- [x] **Step 1: x**", show)

    def test_a_failed_commit_blocks_with_the_git_error(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        answer_commit(root, do_git=False, ok=False, error="nothing to commit, working tree clean")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        reason = json.loads(out)["reason"]
        self.assertIn("nothing to commit", reason)
        self.assertIn("git add", reason)

    # NOTE: the spec's "commit ok, later-phase file dirty" row has no test here
    # because it cannot happen. A later-phase file dirty BEFORE the commit stops
    # the ladder at the tree rung, so the human is never asked; nothing can make
    # a file appear between the commit and the re-evaluation. What IS reachable
    # is leftover unlisted work, which is what this test covers.
    def test_leftover_work_after_the_commit_blocks_with_the_next_phase(self):
        root = self._started()
        with open(os.path.join(root, "stray.py"), "w") as fh:
            fh.write("belongs to no phase")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        reason = json.loads(out)["reason"]
        self.assertIn("Phase 2", reason)
        self.assertIn("[phase-2]", reason)

    def test_the_human_is_asked_at_most_once_per_stop(self):
        root = self._started()
        with open(os.path.join(root, "stray.py"), "w") as fh:
            fh.write("belongs to no phase")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertEqual(len(events_of(root, "arbiter_pending")), 1)

    def test_the_phase_gate_event_carries_the_command(self):
        root = self._started()
        _code, _out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        payloads = events_of(root, "phase_gate")
        self.assertIn("git add", payloads[-1]["command"])
        self.assertEqual(payloads[-1]["message"], "[phase-1] One")

    def test_tests_are_not_rerun_after_the_commit(self):
        counter = os.path.join(tempfile.mkdtemp(), "runs")
        root = project(test_command="python3 -c \"open(r'%s','a').write('x')\"" % counter)
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        enable_arbiter(root)
        beat(root)
        answer_commit(root)
        run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        with open(counter) as fh:
            self.assertEqual(fh.read(), "x")

    def test_failing_tests_never_reach_the_human(self):
        root = project(test_command="python3 -c 'import sys; sys.exit(1)'")
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        enable_arbiter(root)
        beat(root)
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("tests are failing", json.loads(out)["reason"])
        self.assertEqual(events_of(root, "arbiter_pending"), [])

    def test_open_cf_items_never_reach_the_human(self):
        root = self._started()
        enable_arbiter(root)
        beat(root)
        with open(os.path.join(root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-14 -- a.py\n- finding\n")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": root}, root)
        self.assertIn("CF.md", json.loads(out)["reason"])
        self.assertEqual(events_of(root, "arbiter_pending"), [])


PROMPT = os.path.join(ROOT, "scripts", "prompt_hook.py")


class TestAcceptedFixesReachClaude(unittest.TestCase):
    """Clicking 'have Claude fix this' has to actually reach the coder, and has
    to be closed out by evidence rather than by assumption."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from ccalib import fixes
        self.fixes = fixes
        self.root = project()

    def _accept(self, suggestion="Call applyDiscount() from lib/pricing.ts"):
        return self.fixes.accept(self.root, {
            "file": "src/pricing.ts", "line": 42, "severity": "critical",
            "kind": "reuse", "critic": "design", "issue": "Reimplements applyDiscount()",
            "suggestion": suggestion, "evidence": "lib/pricing.ts:88",
        })

    def test_stop_is_blocked_with_the_instruction(self):
        self._accept()
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": self.root}, self.root)
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Call applyDiscount() from lib/pricing.ts", payload["reason"])
        self.assertIn("src/pricing.ts:42", payload["reason"])

    def test_a_dispatched_fix_is_not_sent_twice(self):
        self._accept()
        run(DRAIN, {"stop_hook_active": False, "cwd": self.root}, self.root)
        self.assertEqual(self.fixes.todo_fixes(self.root), [])
        self.assertEqual(len(self.fixes.open_fixes(self.root)), 1)

    def test_fixes_outrank_open_cf_items(self):
        self._accept()
        with open(os.path.join(self.root, "CF.md"), "w") as fh:
            fh.write("## 2026-09-15 -- a.py\n- finding\n")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": self.root}, self.root)
        self.assertIn("applyDiscount", json.loads(out)["reason"])

    def test_no_accepted_fixes_leaves_the_ladder_untouched(self):
        with open(os.path.join(self.root, "a.py"), "w") as fh:
            fh.write("phase 1 work")
        _code, out, _err = run(DRAIN, {"stop_hook_active": False, "cwd": self.root}, self.root)
        self.assertIn("git add", json.loads(out)["reason"])

    def test_the_prompt_hook_hands_them_to_an_idle_session(self):
        self._accept()
        _code, out, _err = run(PROMPT, {"cwd": self.root, "prompt": "carry on"}, self.root)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("applyDiscount", context)
        self.assertEqual(self.fixes.todo_fixes(self.root), [])

    def test_the_prompt_hook_is_silent_with_an_empty_queue(self):
        _code, out, _err = run(PROMPT, {"cwd": self.root, "prompt": "hello"}, self.root)
        self.assertEqual(out.strip(), "")

    def test_the_prompt_hook_never_blocks_the_operator_typing(self):
        os.makedirs(os.path.join(self.root, ".cca"), exist_ok=True)
        with open(os.path.join(self.root, ".cca", "fixes.json"), "w") as fh:
            fh.write("{ not json")
        code, out, _err = run(PROMPT, {"cwd": self.root, "prompt": "hello"}, self.root)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
