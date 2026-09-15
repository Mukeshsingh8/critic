import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dashboard

ROOT = os.path.join(os.path.dirname(__file__), "..")


def E(kind, payload):
    return {"type": kind, "ts": 1.0, "payload": payload}


class TestStats(unittest.TestCase):
    def test_counts_edits_blocks_and_queues(self):
        events = [E("edit_started", {"file": "a"}), E("vote", {"decision": "block"}),
                  E("edit_started", {"file": "b"}), E("vote", {"decision": "queue"})]
        s = dashboard.stats(events)
        self.assertEqual(s["edits"], 2)
        self.assertEqual(s["blocked"], 1)
        self.assertEqual(s["queued"], 1)

    def test_agreement_rate_when_critics_agree(self):
        events = [E("critic_verdict", {"critic": "design", "verdict": "pass", "file": "a"}),
                  E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a"})]
        self.assertEqual(dashboard.stats(events)["agreement_rate"], 1.0)

    def test_agreement_rate_when_critics_disagree(self):
        events = [E("critic_verdict", {"critic": "design", "verdict": "fail", "file": "a"}),
                  E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a"})]
        self.assertEqual(dashboard.stats(events)["agreement_rate"], 0.0)

    def test_unique_findings_per_critic_shows_who_earns_their_keep(self):
        events = [
            E("critic_verdict", {"critic": "design", "verdict": "fail", "file": "a",
                                 "findings": [{"kind": "reuse", "severity": "major"}]}),
            E("critic_verdict", {"critic": "correctness", "verdict": "pass", "file": "a",
                                 "findings": []}),
        ]
        s = dashboard.stats(events)
        self.assertEqual(s["findings_by_critic"]["design"], 1)
        self.assertEqual(s["findings_by_critic"]["correctness"], 0)

    def test_empty_log_does_not_divide_by_zero(self):
        s = dashboard.stats([])
        self.assertEqual(s["edits"], 0)
        self.assertEqual(s["agreement_rate"], None)


class TestPage(unittest.TestCase):
    """The board is three files now. index.html is a shell; the behaviour the
    original test cared about lives in dashboard.js, so assert it there."""

    def _read(self, name):
        with open(os.path.join(ROOT, "dashboard", name)) as fh:
            return fh.read()

    def test_shell_loads_the_skin_and_the_engine(self):
        shell = self._read("index.html")
        self.assertIn("dashboard.css", shell)
        self.assertIn("dashboard.js", shell)

    def test_standalone_host_polls_the_event_log(self):
        body = self._read("dashboard.js")
        self.assertIn("events.jsonl", body)
        self.assertIn("setInterval", body)

    def test_the_same_file_also_serves_the_webview(self):
        """One source, two transports -- that is what stops the two boards
        drifting apart the way they did before."""
        body = self._read("dashboard.js")
        self.assertIn("acquireVsCodeApi", body)
        self.assertIn("postMessage", body)

    def test_the_board_answers_the_three_questions(self):
        """A verdict without the change it judged, or without the remedy, is
        half a board. All three columns come from the same event stream."""
        body = self._read("dashboard.js")
        for heading in ("WHAT CLAUDE DID", "WHAT THE CRITICS SAID", "WHAT TO DO NEXT"):
            self.assertIn(heading, body)

    def test_the_actionable_half_of_a_finding_is_rendered(self):
        """`suggestion` was captured by every critic and thrown away by the
        board. It is the only field that says what to do next."""
        self.assertIn("finding.suggestion", self._read("dashboard.js"))

    def test_the_change_summary_is_rendered(self):
        body = self._read("dashboard.js")
        for field in ("change.added", "change.removed", "change.excerpt"):
            self.assertIn(field, body)

    def test_the_board_respects_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", self._read("dashboard.css"))
        self.assertIn("prefers-reduced-motion", self._read("dashboard.js"))


if __name__ == "__main__":
    unittest.main()


class TestServeRobustness(unittest.TestCase):
    """Found while investigating a reaped background server: a port clash
    produced a raw OSError traceback, and serve() chdir'd the whole process."""

    def test_port_clash_reports_clearly_instead_of_a_traceback(self):
        import socket, subprocess, sys, tempfile
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        held.listen(1)
        try:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "scripts", "dashboard.py"),
                 tempfile.mkdtemp(), str(port)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            _out, err = proc.communicate(timeout=20)
        finally:
            held.close()
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", err)
        self.assertIn("already in use", err.lower())
        self.assertIn(str(port), err)

    def test_serve_does_not_chdir_the_process(self):
        import tempfile, threading, time
        before = os.getcwd()
        root = tempfile.mkdtemp()
        thread = threading.Thread(target=dashboard.serve, args=(root, 0), daemon=True)
        thread.start()
        time.sleep(0.4)
        self.assertEqual(os.getcwd(), before)


class TestProgress(unittest.TestCase):
    """After 'send to Claude' the board went quiet until it was all over. The
    stages between are all derivable from events that already existed."""

    def _read(self):
        with open(os.path.join(ROOT, "dashboard", "dashboard.js")) as fh:
            return fh.read()

    def test_every_stage_is_rendered(self):
        body = self._read()
        for stage in ("waiting", "working", "reviewing", "cleared", "rejected"):
            self.assertIn('"%s"' % stage, body)

    def test_it_reports_each_officer_separately(self):
        """'the critic and 2 officers are convinced' means per-critic, not a
        single aggregate verdict."""
        body = self._read()
        self.assertIn("officer-call", body)
        self.assertIn("officers", body)

    def test_progress_is_derived_from_a_dispatch(self):
        self.assertIn("fixProgress", self._read())

    def test_the_only_motion_here_respects_reduced_motion(self):
        with open(os.path.join(ROOT, "dashboard", "dashboard.css")) as fh:
            css = fh.read()
        self.assertIn("@keyframes pulse", css)
        tail = css[css.index("prefers-reduced-motion"):]
        self.assertIn(".progress.working .stage::after", tail)


class TestRapSheet(unittest.TestCase):
    """The sheet was history with no state: a finding you fixed, one you
    skipped and one still open all looked identical, and two critics agreeing
    printed as two separate defects."""

    def _read(self):
        with open(os.path.join(ROOT, "dashboard", "dashboard.js")) as fh:
            return fh.read()

    def test_findings_are_merged_per_defect_not_per_critic(self):
        body = self._read()
        self.assertIn("mergeFindings", body)
        self.assertIn("both officers agreed", body)

    def test_every_row_carries_a_state(self):
        body = self._read()
        for state in ("open", "queued", "fixed", "skipped", "superseded"):
            self.assertIn('"%s"' % state, body)

    def test_superseded_is_not_reported_as_fixed(self):
        """A file passing review later is not evidence anyone acted on this
        particular finding, so the two must stay distinct."""
        body = self._read()
        self.assertIn("superseded", body)
        self.assertIn("different thing from fixed", body)

    def test_closed_rows_are_separated_from_open_ones(self):
        self.assertIn("details", self._read())

    def test_merging_keeps_the_highest_severity(self):
        self.assertIn("SEV_RANK", self._read())

    def test_the_closed_group_survives_a_repaint(self):
        """The board repaints twice a second; without this the disclosure snaps
        shut while you are reading it."""
        body = self._read()
        self.assertIn("SHEET_OPEN", body)
        self.assertIn('"toggle"', body)
