import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dashboard
from ccalib import events as events_mod

ROOT = os.path.join(os.path.dirname(__file__), "..")


def E(kind, payload):
    return {"type": kind, "ts": 1.0, "payload": payload}


class TestNewEventTypes(unittest.TestCase):
    def test_phase_events_are_registered(self):
        for kind in ("phase_context", "phase_fenced", "phase_gate"):
            self.assertIn(kind, events_mod.EVENT_TYPES, kind)


class TestPhaseStrip(unittest.TestCase):
    def test_empty_when_no_phase_events(self):
        self.assertIsNone(dashboard.phase_strip([])["current"])

    def test_reads_current_and_total_from_context(self):
        strip = dashboard.phase_strip([E("phase_context", {"current": 3, "total": 11})])
        self.assertEqual(strip["current"], 3)
        self.assertEqual(strip["total"], 11)

    def test_latest_gate_result_wins(self):
        strip = dashboard.phase_strip([
            E("phase_context", {"current": 3, "total": 11}),
            E("phase_gate", {"phase": 3, "ok": False, "rung": "tests",
                             "checks": {"tests": False}}),
            E("phase_gate", {"phase": 3, "ok": False, "rung": "commit",
                             "checks": {"tests": True, "critics": True, "commit": False}}),
        ])
        self.assertEqual(strip["rung"], "commit")
        self.assertTrue(strip["checks"]["tests"])

    def test_counts_fence_denials_only(self):
        strip = dashboard.phase_strip([
            E("phase_fenced", {"file": "c.py", "current": 2, "owner": 3}),
            E("phase_fenced", {"file": "z.py", "current": 2, "owner": None, "allowed": True}),
        ])
        self.assertEqual(strip["fenced"], 1)


class TestPage(unittest.TestCase):
    def test_page_renders_the_phase_state(self):
        """The strip became the booking ladder, but it must still be fed by the
        same two events and still show every rung."""
        with open(os.path.join(ROOT, "dashboard", "dashboard.js")) as fh:
            body = fh.read()
        self.assertIn("phase_context", body)
        self.assertIn("phase_gate", body)
        for rung in ("tests", "critics", "commit", "tree"):
            self.assertIn('"%s"' % rung, body)


if __name__ == "__main__":
    unittest.main()
