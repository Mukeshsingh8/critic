import json, os, tempfile, unittest, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from ccalib import events


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_append_creates_file_and_dir(self):
        events.append("edit_started", {"file": "a.ts"}, root=self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".cca", "events.jsonl")))

    def test_append_writes_one_json_object_per_line(self):
        events.append("edit_started", {"file": "a.ts"}, root=self.tmp)
        events.append("vote", {"decision": "pass"}, root=self.tmp)
        with open(os.path.join(self.tmp, ".cca", "events.jsonl")) as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["type"], "edit_started")
        self.assertEqual(json.loads(lines[1])["payload"]["decision"], "pass")

    def test_every_event_carries_type_ts_and_payload(self):
        events.append("vote", {"decision": "block"}, root=self.tmp)
        rec = events.read_all(self.tmp)[0]
        self.assertEqual(set(rec.keys()), {"type", "ts", "payload"})
        self.assertIsInstance(rec["ts"], float)

    def test_read_all_on_missing_file_returns_empty(self):
        self.assertEqual(events.read_all(self.tmp), [])

    def test_read_all_skips_corrupt_lines(self):
        os.makedirs(os.path.join(self.tmp, ".cca"))
        with open(os.path.join(self.tmp, ".cca", "events.jsonl"), "w") as fh:
            fh.write('{"type":"vote","ts":1.0,"payload":{}}\n')
            fh.write('NOT JSON\n')
            fh.write('{"type":"queued","ts":2.0,"payload":{}}\n')
        self.assertEqual(len(events.read_all(self.tmp)), 2)

    def test_unknown_event_type_is_rejected(self):
        with self.assertRaises(ValueError):
            events.append("not_a_real_event", {}, root=self.tmp)


    def test_arbiter_event_types_are_accepted(self):
        events.append("arbiter_pending", {"id": "x", "kind": "block"}, root=self.tmp)
        events.append("arbitration", {"id": "x", "choice": "uphold"}, root=self.tmp)
        kinds = [e["type"] for e in events.read_all(self.tmp)]
        self.assertEqual(kinds, ["arbiter_pending", "arbitration"])


if __name__ == "__main__":
    unittest.main()
