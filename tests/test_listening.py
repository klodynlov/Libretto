import json
import tempfile
import threading
import unittest
import http.client
from pathlib import Path
from unittest import mock

from libretto.listening import ListeningStore, preview_notes
from libretto.midi import MidiData, MidiNote, write_mididata
from libretto.server import serve


def candidates(workdir):
    results = []
    for i in range(3):
        p = Path(workdir) / f"candidate_{i}.mid"
        md = MidiData(ppq=480, tempos=[(0, 120), (480, 60)], end_tick=1440,
                      notes=[MidiNote(487, 967, 60 + i, 70 + i, 0, 0)])
        write_mididata(p, md)
        results.append({"path": str(p), "name": p.name, "score": .8 - .01 * i,
                        "role": "winner" if i == 0 else "shortlist", "confidence": .9})
    return results


class TestListeningStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "prefs.json"
        self.results = candidates(self.tmp.name)
        self.store = ListeningStore(self.path)

    def test_neutral_slots_conceal_rank_and_duplicate_midi(self):
        comp = self.store.create(self.results, {"seed": 1})
        self.assertEqual(set(comp), {"id", "slots"})
        self.assertEqual(comp["slots"], list("ABC"))
        duplicate = {**self.results[0], "name": "copy.mid"}
        self.assertIsNone(self.store.create([self.results[0], duplicate], {}))

    def test_choice_survives_restart_and_retries_do_not_duplicate(self):
        comp = self.store.create(self.results, {"seed": 1})
        self.store.record(comp["id"], "A")
        self.store.record(comp["id"], "same")
        self.assertEqual(ListeningStore(self.path).count(), 1)
        row = json.loads(self.path.read_text())["judgements"][0]
        self.assertEqual(row["choice"], "same")
        self.assertEqual(row["renderer"], "synth-v1")
        self.assertEqual(row["params"], {"seed": 1})
        self.assertEqual(len(row["candidates"]["A"]["sha256"]), 64)

    def test_unknown_comparison_and_slot_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record("unknown", "A")
        comp = self.store.create(self.results, {})
        with self.assertRaises(ValueError):
            self.store.record(comp["id"], "D")
        with self.assertRaises(ValueError):
            self.store.candidate(comp["id"], "../../etc/passwd")
        self.assertFalse(self.path.exists())

    def test_invalid_existing_file_is_preserved(self):
        self.path.write_text('{"version": 99, "judgements": []}')
        before = self.path.read_bytes()
        comp = self.store.create(self.results, {})
        with self.assertRaises(ValueError):
            self.store.record(comp["id"], "A")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_previous_choices(self):
        comp = self.store.create(self.results, {})
        self.store.record(comp["id"], "A")
        before = self.path.read_bytes()
        with mock.patch("libretto.listening.os.replace", side_effect=OSError("full disk")):
            with self.assertRaises(OSError):
                self.store.record(comp["id"], "B")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".libretto-*")), [])

    def test_preview_preserves_tempo_microtiming_and_velocity(self):
        md = MidiData(ppq=480, tempos=[(0, 120), (480, 60)], end_tick=20000,
                      notes=[MidiNote(487, 967, 60, 43, 0, 0),
                             MidiNote(19000, 19500, 64, 80, 0, 0)])
        preview = preview_notes(md)
        self.assertEqual(len(preview["notes"]), 1)
        self.assertAlmostEqual(preview["notes"][0][0], .5 + 7 / 480)
        self.assertAlmostEqual(preview["notes"][0][1], 1)
        self.assertEqual(preview["notes"][0][3], 43)

    def test_preview_note_limit_is_explicit(self):
        md = MidiData(ppq=480, notes=[MidiNote(0, 120, 60, 80, 0, 0)] * 4001)
        preview = preview_notes(md)
        self.assertEqual(len(preview["notes"]), 4000)
        self.assertTrue(preview["truncated"])


class TestListeningHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "prefs.json"
        def generate(params, workdir):
            return {"results": candidates(workdir), "summary": {"n_generated": 3}}
        cls.httpd = serve(port=0, generator=generate, preferences_path=cls.path)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        status, data = r.status, r.read()
        c.close()
        return status, json.loads(data)

    def test_generate_audition_prefer_and_read_back(self):
        status, generation = self.request("POST", "/api/generate", {"n": 3})
        self.assertEqual(status, 200, generation)
        comp = generation["comparison"]
        self.assertEqual(len(comp["slots"]), 3)
        status, audio = self.request("POST", "/api/audition",
                                     {"comparison": comp["id"], "slot": "B"})
        self.assertEqual(status, 200, audio)
        self.assertEqual(len(audio["notes"]), 1)
        self.assertNotIn("score", audio)
        status, saved = self.request("POST", "/api/preference",
                                    {"comparison": comp["id"], "choice": "B"})
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["saved"])
        self.assertIn("name", saved["candidates"]["B"])
        self.assertEqual(self.request("GET", "/api/preferences")[1]["count"], saved["count"])

    def test_arbitrary_path_and_invalid_payload_not_auditioned(self):
        self.assertEqual(self.request("POST", "/api/audition",
                                      {"path": "/etc/passwd"})[0], 400)
        self.assertEqual(self.request("POST", "/api/preference", ["A"])[0], 400)

    def test_browser_player_is_served(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/listening.js")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn(b"AudioContext", r.read())
        c.close()
