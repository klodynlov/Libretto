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


if __name__ == "__main__":
    unittest.main()
