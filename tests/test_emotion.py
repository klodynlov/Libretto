"""
Tests du profil émotionnel : projection lisible des axes sur
(valence, énergie, tension), lexique de descripteurs, analyse d'une requête.
"""

from __future__ import annotations

import unittest

from libretto.axes import SenseOfMusicalStructure
from libretto.demo import demo_score
from libretto.emotion import (DESCRIPTORS, MODE_VALENCE, _fold, key_label,
                              mode_name, nearest_descriptors,
                              profile_from_axes, target_from_words,
                              weighted_distance)


class TestGeometry(unittest.TestCase):
    def test_distance_zero_and_symmetric(self):
        p, q = (0.2, 0.5, 0.8), (0.6, 0.1, 0.3)
        self.assertEqual(weighted_distance(p, p), 0.0)
        self.assertAlmostEqual(weighted_distance(p, q), weighted_distance(q, p))

    def test_nearest_returns_exact_anchor(self):
        coord = DESCRIPTORS["mélancolique"]
        self.assertEqual(nearest_descriptors(coord, top=1), ["mélancolique"])

    def test_nearest_always_at_least_one(self):
        # centre du cube : loin de tout, mais on rend quand même le plus proche
        self.assertTrue(nearest_descriptors((0.5, 0.5, 0.5), top=4))


class TestLexicon(unittest.TestCase):
    def test_fold_strips_accents(self):
        self.assertEqual(_fold("Mélancolique ÉNERGIE"), "melancolique energie")

    def test_word_matches_descriptor(self):
        res = target_from_words("je veux un truc mélancolique")
        self.assertIsNotNone(res)
        point, hits = res
        self.assertIn("melancolique", hits)
        # le point tombe sur l'ancre du mot
        self.assertLess(weighted_distance(point, DESCRIPTORS["mélancolique"]), 1e-9)

    def test_synonym_and_accentless(self):
        # « nostalgie » (synonyme) et sans accent doivent viser « nostalgique »
        res = target_from_words("ambiance nostalgie")
        self.assertIsNotNone(res)
        point, _ = res
        self.assertLess(weighted_distance(point, DESCRIPTORS["nostalgique"]), 1e-9)

    def test_bigram_expression(self):
        res = target_from_words("morceau sous tension")
        self.assertIsNotNone(res)
        _, hits = res
        self.assertIn("sous tension", hits)

    def test_multiple_words_average(self):
        res = target_from_words("triste et lumineux")
        self.assertIsNotNone(res)
        point, hits = res
        self.assertEqual(len(hits), 2)
        mid = tuple((DESCRIPTORS["triste"][k] + DESCRIPTORS["lumineux"][k]) / 2
                    for k in range(3))
        self.assertLess(weighted_distance(point, mid), 1e-9)

    def test_no_emotional_word(self):
        self.assertIsNone(target_from_words("8 mesures 120 bpm en Dm"))


class TestProfile(unittest.TestCase):
    def setUp(self):
        self.sms = SenseOfMusicalStructure(demo_score())
        self.sms.calculate()

    def test_ranges_bounded(self):
        prof = profile_from_axes(self.sms, mode="maj", bpm=120)
        for v in (prof.valence, prof.energy, prof.tension, prof.arc):
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)
        self.assertTrue(prof.descriptors)

    def test_mode_shifts_valence(self):
        # toutes choses égales, le majeur ancre la valence plus haut que le mineur
        maj = profile_from_axes(self.sms, mode="maj", bpm=120)
        mns = profile_from_axes(self.sms, mode="min", bpm=120)
        self.assertGreater(maj.valence, mns.valence)

    def test_tempo_lifts_energy(self):
        slow = profile_from_axes(self.sms, mode="maj", bpm=60)
        fast = profile_from_axes(self.sms, mode="maj", bpm=170)
        self.assertGreater(fast.energy, slow.energy)

    def test_mode_valence_monotonic(self):
        self.assertGreater(MODE_VALENCE["maj"], MODE_VALENCE["mixolydien"])
        self.assertGreater(MODE_VALENCE["mixolydien"], MODE_VALENCE["dorien"])
        self.assertGreater(MODE_VALENCE["dorien"], MODE_VALENCE["min"])

    def test_to_dict_roundtrippable(self):
        d = profile_from_axes(self.sms, mode="maj", bpm=120).to_dict()
        self.assertEqual(set(d), {"valence", "energy", "tension", "arc",
                                  "descriptors", "rationale"})


class TestLabels(unittest.TestCase):
    def test_key_label(self):
        self.assertEqual(key_label(2, "min"), "D mineur")
        self.assertEqual(key_label(6, "maj"), "F# majeur")
        self.assertIsNone(key_label(None, "min"))

    def test_mode_name(self):
        self.assertEqual(mode_name("min"), "mineur")
        self.assertEqual(mode_name("dorien"), "dorien")


if __name__ == "__main__":
    unittest.main()
