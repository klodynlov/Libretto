"""
Tests du générateur appris (chaîne de Markov entraînée sur un corpus) :
apprentissage, normalisation tonale, génération déterministe et valide.
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.midi import parse_midi  # noqa: E402

import markov_gen  # noqa: E402
from make_corpus import build_corpus  # noqa: E402


class TestMarkovGen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = build_corpus(Path(cls._tmp.name) / "corpus", n=6, seed=3)
        cls.model, cls.skipped = markov_gen.train_from_paths(cls.corpus)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_training_learns_material(self):
        self.assertEqual(self.skipped, 0)
        self.assertGreater(self.model.n_files, 0)
        self.assertTrue(self.model.tracks)
        self.assertGreater(sum(t.n_onsets for t in self.model.tracks.values()), 0)
        # des intervalles ont bien été appris (chaîne non vide)
        self.assertTrue(any(t.iv_orders[0] for t in self.model.tracks.values()))

    def test_generation_is_valid(self):
        rng = random.Random(1)
        tracks, bpm = markov_gen.generate_one(self.model, rng, bars=8, tonic=0)
        self.assertTrue(tracks)
        self.assertGreater(bpm, 0)
        notes = [n for tr in tracks for n in tr]
        self.assertGreater(len(notes), 0)
        for start, dur, pitch, vel, chan in notes:
            self.assertGreaterEqual(pitch, 0)
            self.assertLessEqual(pitch, 127)
            self.assertGreater(dur, 0)
            self.assertGreaterEqual(start, 0)

    def test_generation_is_deterministic(self):
        a = markov_gen.generate_one(self.model, random.Random(42), bars=8, tonic=0)
        b = markov_gen.generate_one(self.model, random.Random(42), bars=8, tonic=0)
        self.assertEqual(a, b)
        c = markov_gen.generate_one(self.model, random.Random(43), bars=8, tonic=0)
        self.assertNotEqual(a, c)   # graine différente → matériau différent

    def test_written_midi_reparses(self):
        from libretto.midi import write_midi
        rng = random.Random(7)
        tracks, bpm = markov_gen.generate_one(self.model, rng, bars=12, tonic=2)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "gen.mid"
            write_midi(path, tracks, ppq=480, bpm=bpm, time_sig=(4, 4))
            md = parse_midi(path)          # ne doit pas lever
            self.assertGreater(len(md.notes), 0)

    def test_tonic_shift_normalises(self):
        # après décalage vers la tonique 0, l'estimateur doit voir une tonique
        # proche de 0 sur le fichier normalisé
        md = parse_midi(self.corpus[0])
        shift = markov_gen._tonic_shift(md)
        self.assertTrue(-6 <= shift <= 5)

    def test_empty_corpus_yields_empty_model(self):
        model = markov_gen.train([])
        self.assertEqual(model.n_files, 0)
        self.assertFalse(model.tracks)
        # génération sur modèle vide : pas de piste, pas d'exception
        tracks, _bpm = markov_gen.generate_one(model, random.Random(0), bars=8)
        self.assertEqual(tracks, [])


if __name__ == "__main__":
    unittest.main()
