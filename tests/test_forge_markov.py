"""
Test du pont Forge × markov_gen : entraîner sur un corpus, générer des
candidats, les faire trier par Forge — la chaîne « modèle appris → juge ».
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

import markov_gen  # noqa: E402
from make_corpus import build_corpus  # noqa: E402


class TestForgeMarkov(unittest.TestCase):
    def test_generate_then_forge(self):
        from forge import forge_from_dir
        from forge_markov import generate_candidates

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            corpus = build_corpus(base / "corpus", n=6, seed=5)
            model, _skipped = markov_gen.train_from_paths(corpus)

            out = base / "out"
            cand = generate_candidates(model, out / "candidates", n=4, seed=1,
                                       bars=8, tonic=None)
            self.assertTrue(cand)
            self.assertTrue(all(p.exists() for p in cand))

            report = forge_from_dir(out / "candidates", out,
                                    min_confidence=0.0, min_score=0.0)
            # gate à 0 : tous les candidats concourent, un gagnant sort
            self.assertEqual(report["n_generated"], len(cand))
            self.assertIsNotNone(report["winner"])
            self.assertTrue((out / "forge_winner.mid").exists())
            self.assertTrue((out / "forge_report.json").exists())

    def test_deterministic_candidates(self):
        from forge_markov import generate_candidates

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            corpus = build_corpus(base / "corpus", n=5, seed=9)
            model, _ = markov_gen.train_from_paths(corpus)
            a = generate_candidates(model, base / "a", n=3, seed=2, bars=8, tonic=1)
            b = generate_candidates(model, base / "b", n=3, seed=2, bars=8, tonic=1)
            self.assertEqual([p.read_bytes() for p in a],
                             [p.read_bytes() for p in b])


if __name__ == "__main__":
    unittest.main()
