"""Sortie Gadget — lecteur non bloquant (port MIDI virtuel) + endpoints web.

`GadgetPlayer` est testé SANS python-rtmidi : on injecte une fausse sortie MIDI
(le vrai port n'est ouvert que si `_midiout is None`). On vérifie l'envoi des
messages, le canal forcé, l'arrêt propre (all-notes-off sur 16 canaux) et le
remplacement d'une lecture par une autre. Les endpoints `/api/gadget*` sont
testés via un serveur éphémère — le 503 « rtmidi manquant » n'est vérifié que
si la dépendance est absente (cas de la CI, 100 % stdlib).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto import gadget  # noqa: E402
from libretto.gadget import GadgetPlayer  # noqa: E402
from libretto.midi import MidiData, MidiNote  # noqa: E402
from libretto.server import serve  # noqa: E402


class FakeOut:
    """Fausse sortie rtmidi : mémorise les messages, n'ouvre aucun port."""

    def __init__(self):
        self.messages: list[list[int]] = []
        self.closed = False

    def send_message(self, msg):
        self.messages.append(list(msg))

    def close_port(self):
        self.closed = True


def _md(notes_spec, ppq=480, bpm=120.0):
    notes = [MidiNote(s, e, p, v, c, 0) for (s, e, p, v, c) in notes_spec]
    return MidiData(ppq=ppq, tempos=[(0, bpm)], notes=notes)


class TestGadgetPlayer(unittest.TestCase):
    def _player(self):
        p = GadgetPlayer()
        fake = FakeOut()
        p._midiout = fake  # court-circuite rtmidi : port déjà « ouvert »
        return p, fake

    def test_available_returns_bool(self):
        self.assertIsInstance(gadget.available(), bool)

    def test_play_sends_all_notes_on_forced_channel(self):
        p, fake = self._player()
        md = _md([(0, 24, 60, 100, 3), (24, 48, 64, 90, 5)])  # canaux variés
        info = p.play(md, channel=1, loop=False)
        self.assertEqual(info["notes"], 2)
        self.assertEqual(info["port"], "Libretto")
        p._thread.join(2.0)
        ons = [m for m in fake.messages if m[0] & 0xF0 == 0x90]
        offs = [m for m in fake.messages if m[0] & 0xF0 == 0x80]
        self.assertEqual(len(ons), 2)
        self.assertEqual(len(offs), 2)
        self.assertTrue(all((m[0] & 0x0F) == 0 for m in ons))  # forcé canal 1

    def test_empty_midi_raises(self):
        p, _ = self._player()
        with self.assertRaises(gadget.GadgetError):
            p.play(_md([]), loop=False)

    def test_stop_sends_all_notes_off(self):
        p, fake = self._player()
        p.play(_md([(0, 4800, 60, 100, 0)]), channel=1, loop=True)  # note longue, boucle
        time.sleep(0.15)
        self.assertTrue(p.is_playing())
        p.stop()
        self.assertFalse(p.is_playing())
        panics = [m for m in fake.messages if m[0] & 0xF0 == 0xB0 and m[1] == 123]
        self.assertGreaterEqual(len(panics), 16)  # all-notes-off sur 16 canaux

    def test_new_play_replaces_current(self):
        p, _ = self._player()
        p.play(_md([(0, 4800, 60, 100, 0)]), loop=True)
        time.sleep(0.1)
        first = p._thread
        p.play(_md([(0, 24, 62, 100, 0)]), loop=False)
        self.assertIsNot(p._thread, first)
        p._thread.join(2.0)


class TestGadgetEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, json.loads(r.read())

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_analyses_exposes_can_gadget(self):
        st, data = self._get("/api/analyses")
        self.assertEqual(st, 200)
        self.assertIn("can_gadget", data)
        self.assertIsInstance(data["can_gadget"], bool)

    def test_gadget_stop_always_ok(self):
        st, data = self._post("/api/gadget_stop", {})
        self.assertEqual(st, 200)
        self.assertTrue(data["stopped"])

    def test_gadget_without_rtmidi_returns_503(self):
        if gadget.available():
            self.skipTest("python-rtmidi installé : le 503 ne s'applique pas")
        st, data = self._post("/api/gadget", {"id": 999})
        self.assertEqual(st, 503)
        self.assertIn("rtmidi", data["error"])


if __name__ == "__main__":
    unittest.main()
