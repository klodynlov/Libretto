"""play_gadget — audition d'un .mid dans Gadget via port MIDI virtuel.

On teste la partie déterministe et sans dépendance : `schedule_events`
(notes → messages MIDI datés) et le `--dry-run`. python-rtmidi (backend
CoreMIDI) n'est PAS requis ici — il n'est importé qu'à l'ouverture réelle d'un
port, ce qu'on ne fait pas en test. Le message d'erreur « dépendance manquante »
est vérifié en simulant l'absence du module.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

import play_gadget  # noqa: E402
from libretto.midi import MidiData, MidiNote  # noqa: E402
from play_gadget import NOTE_OFF, NOTE_ON, schedule_events  # noqa: E402

EXAMPLE_MID = Path(__file__).resolve().parent.parent / "examples" / "morceau_naija.mid"


def _two_notes() -> MidiData:
    # 120 bpm, 480 ppq : une noire = 0.5 s. Deux noires qui s'enchaînent.
    return MidiData(ppq=480, tempos=[(0, 120.0)], notes=[
        MidiNote(0, 240, 60, 100, 0, 0),
        MidiNote(240, 480, 64, 90, 0, 0),
    ])


class TestSchedule(unittest.TestCase):
    def test_pairs_and_tempo(self):
        ev = schedule_events(_two_notes())
        self.assertEqual(len(ev), 4)  # 2 notes → 2 on + 2 off
        times = [t for t, _ in ev]
        self.assertEqual(times, sorted(times))
        # 480 ppq @120 bpm → 240 ticks = 0.25 s
        self.assertAlmostEqual(ev[0][0], 0.0, places=4)
        self.assertAlmostEqual(ev[-1][0], 0.5, places=4)
        ons = [m for _, m in ev if m[0] & 0xF0 == NOTE_ON]
        offs = [m for _, m in ev if m[0] & 0xF0 == NOTE_OFF]
        self.assertEqual(len(ons), 2)
        self.assertEqual(len(offs), 2)

    def test_off_before_on_at_equal_time(self):
        # À 0.25 s : note-off du sol précède le note-on du mi (note pas coupée).
        ev = schedule_events(_two_notes())
        at_025 = [m for t, m in ev if abs(t - 0.25) < 1e-6]
        self.assertEqual(at_025[0][0] & 0xF0, NOTE_OFF)
        self.assertEqual(at_025[1][0] & 0xF0, NOTE_ON)

    def test_force_channel(self):
        ev = schedule_events(_two_notes(), channel=10)
        for _, m in ev:
            self.assertEqual(m[0] & 0x0F, 9)  # canal 10 → nibble 9

    def test_clamps_pitch_and_velocity(self):
        md = MidiData(ppq=480, tempos=[(0, 120.0)],
                      notes=[MidiNote(0, 240, 200, 200, 0, 0)])
        (_, on), = [(t, m) for t, m in schedule_events(md) if m[0] & 0xF0 == NOTE_ON]
        self.assertEqual(on[1], 127)  # pitch borné
        self.assertEqual(on[2], 127)  # vélocité bornée

    def test_zero_length_note_gets_floor(self):
        # start == end : durée plancher, off strictement après on.
        md = MidiData(ppq=480, tempos=[(0, 120.0)],
                      notes=[MidiNote(100, 100, 60, 80, 0, 0)])
        ev = schedule_events(md)
        self.assertGreater(ev[1][0], ev[0][0])

    def test_real_file(self):
        from libretto.midi import parse_midi
        md = parse_midi(EXAMPLE_MID)
        ev = schedule_events(md)
        self.assertEqual(len(ev), 2 * len(md.notes))
        self.assertEqual([t for t, _ in ev], sorted(t for t, _ in ev))
        ons = sum(1 for _, m in ev if m[0] & 0xF0 == NOTE_ON)
        self.assertEqual(ons, len(md.notes))


class TestCli(unittest.TestCase):
    def test_dry_run_needs_no_backend(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = play_gadget.main([str(EXAMPLE_MID), "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", buf.getvalue())

    def test_missing_rtmidi_is_clean_error(self):
        # rtmidi absent → SystemExit avec l'astuce d'installation, jamais un
        # ImportError nu qui remonterait à l'utilisateur.
        with mock.patch.dict(sys.modules, {"rtmidi": None}):
            with self.assertRaises(SystemExit) as ctx:
                play_gadget._import_rtmidi()
        self.assertIn("python-rtmidi", str(ctx.exception))

    def test_bad_channel_rejected(self):
        with self.assertRaises(SystemExit):
            play_gadget.main([str(EXAMPLE_MID), "--channel", "0"])


if __name__ == "__main__":
    unittest.main()
