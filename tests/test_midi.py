import tempfile
import unittest
from pathlib import Path

from libretto.midi import parse_midi, write_midi


class TestMidiRoundtrip(unittest.TestCase):
    def _roundtrip(self, tracks, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.mid"
            write_midi(path, tracks, **kwargs)
            return parse_midi(path)

    def test_basic_notes(self):
        md = self._roundtrip([[(0.0, 1.0, 60, 80, 0), (1.0, 1.0, 64, 90, 0),
                               (2.0, 2.0, 67, 100, 0)]], ppq=480, bpm=100)
        self.assertEqual(md.ppq, 480)
        self.assertEqual(len(md.notes), 3)
        n0, n1, n2 = md.notes
        self.assertEqual((n0.start, n0.end, n0.pitch, n0.velocity), (0, 480, 60, 80))
        self.assertEqual((n1.start, n1.pitch), (480, 64))
        self.assertEqual((n2.end, n2.pitch), (1920, 67))

    def test_tempo_and_markers(self):
        md = self._roundtrip([[(0.0, 4.0, 60, 80, 0)]], bpm=100,
                             markers=[(0.0, "Intro"), (4.0, "Refrain")],
                             tempo_changes=[(4.0, 92.0)])
        self.assertAlmostEqual(md.tempos[0][1], 100.0, places=1)
        self.assertAlmostEqual(md.tempos[1][1], 92.0, places=1)
        self.assertEqual([m[1] for m in md.markers], ["Intro", "Refrain"])
        self.assertEqual(md.markers[1][0], 4 * 480)

    def test_channels_kept(self):
        md = self._roundtrip([[(0.0, 1.0, 36, 100, 9)], [(0.0, 1.0, 60, 80, 2)]])
        channels = sorted(n.channel for n in md.notes)
        self.assertEqual(channels, [2, 9])

    def test_overlapping_same_pitch(self):
        # Deux notes superposées de même hauteur : appariement LIFO sans crash.
        md = self._roundtrip([[(0.0, 2.0, 60, 80, 0), (1.0, 2.0, 60, 70, 0)]])
        self.assertEqual(len(md.notes), 2)
        for n in md.notes:
            self.assertGreater(n.end, n.start)

    def test_time_signature(self):
        md = self._roundtrip([[(0.0, 3.0, 60, 80, 0)]], time_sig=(3, 4))
        self.assertEqual(md.time_sigs[0][1:], (3, 4))

    def test_reject_non_midi(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mid"
            path.write_bytes(b"pas du midi du tout")
            with self.assertRaises(ValueError):
                parse_midi(path)


class TestControls(unittest.TestCase):
    """CC64 (pédale) capturé et réécrit : une interprétation réelle de piano
    vit dessus, et la perdre d'un seul côté d'une paire A/B ferait entendre
    la pédale au lieu de la dégradation."""

    def _md_with_pedal(self):
        from libretto.midi import MidiData, MidiNote
        return MidiData(
            ppq=480,
            notes=[MidiNote(0, 480, 60, 80, 0, 0),
                   MidiNote(960, 1440, 64, 100, 0, 0)],
            tempos=[(0, 120.0)],
            controls=[(0, 0, 64, 127), (720, 0, 64, 0), (960, 0, 64, 90)],
            end_tick=1440)

    def test_controls_roundtrip(self):
        from libretto.midi import write_mididata
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cc.mid"
            write_mididata(path, self._md_with_pedal())
            back = parse_midi(path)
        self.assertEqual(back.controls,
                         [(0, 0, 64, 127), (720, 0, 64, 0), (960, 0, 64, 90)])

    def test_pedal_up_precedes_note_on_at_same_tick(self):
        """Au même tick, le contrôleur est écrit avant le note-on : une
        pédale levée ne doit pas soutenir l'attaque qui suit."""
        from libretto.midi import write_mididata
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ordre.mid"
            write_mididata(path, self._md_with_pedal())
            raw = path.read_bytes()
        self.assertLess(raw.find(bytes([0xB0, 64, 90])),
                        raw.find(bytes([0x90, 64, 100])))


class TestSlice(unittest.TestCase):
    """Découpe d'extraits pour le corpus d'interprétations réelles."""

    def _long_md(self):
        from libretto.midi import MidiData, MidiNote
        # 120 BPM, ppq 480 : 1 s = 960 ticks. Tempo double à t=10 s.
        notes = [MidiNote(i * 960, i * 960 + 480, 60 + i % 12, 40 + i % 60, 0, 0)
                 for i in range(30)]
        return MidiData(
            ppq=480, notes=notes,
            tempos=[(0, 120.0), (9600, 240.0)],
            time_sigs=[(0, 4, 4)],
            controls=[(960, 0, 64, 127), (12000, 0, 64, 0)],
            end_tick=notes[-1].end)

    def test_notes_rebased_and_clipped(self):
        from libretto.midi import slice_mididata
        ex = slice_mididata(self._long_md(), 2.0, 4.0)
        self.assertTrue(ex.notes)
        self.assertEqual(ex.notes[0].start, 0)
        self.assertTrue(all(n.end <= ex.end_tick for n in ex.notes))
        # l'attaque doit être DANS la fenêtre : pas de note entamée avant
        self.assertTrue(all(n.start >= 0 for n in ex.notes))

    def test_pedal_state_carried_to_tick_zero(self):
        """Pédale enfoncée à t=1 s : un extrait pris à t=5 s doit démarrer
        pédale enfoncée, sinon ses premières secondes sonnent sèches."""
        from libretto.midi import slice_mididata
        ex = slice_mididata(self._long_md(), 5.0, 4.0)
        self.assertIn((0, 0, 64, 127), ex.controls)

    def test_tempo_and_timesig_active_reemitted(self):
        from libretto.midi import slice_mididata
        ex = slice_mididata(self._long_md(), 12.0, 4.0)
        self.assertEqual(ex.tempos[0], (0, 240.0))
        self.assertEqual(ex.time_sigs[0], (0, 4, 4))

    def test_seconds_tick_inverse(self):
        from libretto.midi import seconds_to_tick, tick_to_seconds
        md = self._long_md()
        for s in (0.0, 3.7, 10.0, 12.5):
            self.assertAlmostEqual(
                tick_to_seconds(md, seconds_to_tick(md, s)), s, places=2)


if __name__ == "__main__":
    unittest.main()
