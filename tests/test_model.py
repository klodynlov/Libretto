import unittest

from libretto.axes import AXES_META, estimate_key, band, pearson
from libretto.model import Accidentals, NoteType, Pitch, pitch_from_midi


class TestPitch(unittest.TestCase):
    def test_midi_mapping_diatonic(self):
        # Bug v1 : index diatonique utilisé comme demi-tons (D4 = 61 = C#4).
        self.assertEqual(Pitch(NoteType.C).midi, 60)
        self.assertEqual(Pitch(NoteType.D).midi, 62)
        self.assertEqual(Pitch(NoteType.E).midi, 64)
        self.assertEqual(Pitch(NoteType.F).midi, 65)
        self.assertEqual(Pitch(NoteType.G).midi, 67)
        self.assertEqual(Pitch(NoteType.A).midi, 69)
        self.assertEqual(Pitch(NoteType.B).midi, 71)

    def test_accidentals_and_octaves(self):
        self.assertEqual(Pitch(NoteType.B, Accidentals.FLAT, 3).midi, 58)
        self.assertEqual(Pitch(NoteType.F, Accidentals.SHARP, 5).midi, 78)
        self.assertEqual(Pitch(NoteType.C, octave=3).midi, 48)

    def test_pitch_from_midi_roundtrip(self):
        for m in range(24, 96):
            self.assertEqual(pitch_from_midi(m).midi, m)

    def test_enharmonic_same_pc(self):
        cs = Pitch(NoteType.C, Accidentals.SHARP, 4)
        df = Pitch(NoteType.D, Accidentals.FLAT, 4)
        self.assertEqual(cs.pc, df.pc)


class TestHelpers(unittest.TestCase):
    def test_weights_sum_to_one(self):
        total = sum(meta[2] for meta in AXES_META.values())
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_band_shape(self):
        self.assertEqual(band(0, 1, 3, 7, 12), 0.0)
        self.assertEqual(band(5, 1, 3, 7, 12), 1.0)
        self.assertAlmostEqual(band(2, 1, 3, 7, 12), 0.5)
        self.assertAlmostEqual(band(9.5, 1, 3, 7, 12), 0.5)
        self.assertEqual(band(20, 1, 3, 7, 12), 0.0)

    def test_pearson_bounded(self):
        self.assertAlmostEqual(pearson([0, 1, 2], [0, 10, 20]), 1.0)
        self.assertAlmostEqual(pearson([0, 1, 2], [20, 10, 0]), -1.0)
        self.assertEqual(pearson([0, 1, 2], [5, 5, 5]), 0.0)

    def test_estimate_key_c_major(self):
        hist = [0.0] * 12
        for pc in (0, 2, 4, 5, 7, 9, 11):  # gamme de Do majeur
            hist[pc] += 2.0
        hist[0] += 3.0  # tonique renforcée
        hist[7] += 1.5  # dominante
        root, mode, corr, margin = estimate_key(hist)
        self.assertEqual(root, 0)
        self.assertEqual(mode, "maj")
        self.assertGreater(corr, 0.6)
        self.assertGreater(margin, 0.0)

    def test_estimate_key_a_minor(self):
        hist = [0.0] * 12
        for pc in (9, 11, 0, 2, 4, 5, 8):  # La mineur harmonique
            hist[pc] += 2.0
        hist[9] += 3.0
        hist[4] += 1.5
        root, mode, _corr, _m = estimate_key(hist)
        self.assertEqual(root, 9)
        self.assertEqual(mode, "min")


if __name__ == "__main__":
    unittest.main()
