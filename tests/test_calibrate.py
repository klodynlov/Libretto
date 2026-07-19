"""Tests de la calibration contrastive (libretto.calibrate)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from make_demo import build  # noqa: E402

from libretto.axes import AXES_META, SenseOfMusicalStructure  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.calibrate import (  # noqa: E402
    AXIS_IDS,
    DEGRADATIONS,
    EXPERT_WEIGHTS,
    NOOP_EPS,
    axis_vector,
    evaluate,
    file_vectors,
    load_weights,
    optimize_weights,
    run_calibration,
)
from libretto.cli import main as cli_main  # noqa: E402
from libretto.midi import parse_midi, write_midi  # noqa: E402


class TestCalibrate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.corpus = Path(cls._tmp.name)
        cls.mid_path = cls.corpus / "demo.mid"
        build(cls.mid_path)
        cls.md = parse_midi(cls.mid_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_axis_vector_shape_and_bounds(self):
        vec = axis_vector(self.md)
        self.assertEqual(len(vec), len(AXIS_IDS))
        for v in vec:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_degradations_deterministic(self):
        v1 = file_vectors(self.mid_path, seed=7, variants=2)
        v2 = file_vectors(self.mid_path, seed=7, variants=2)
        self.assertEqual(v1, v2)
        v3 = file_vectors(self.mid_path, seed=8, variants=2)
        self.assertNotEqual(v1, v3)

    def test_degradations_preserve_note_count(self):
        import random
        for name, fn in DEGRADATIONS.items():
            degraded = fn(self.md, random.Random(1))
            self.assertEqual(len(degraded.notes), len(self.md.notes), name)
            # l'original ne doit jamais être muté
            self.assertEqual(len(self.md.notes), 564)

    def test_degraded_scores_lower_on_average(self):
        pos, negs, _skipped = file_vectors(self.mid_path, seed=42, variants=2)
        _acc, margin = evaluate(EXPERT_WEIGHTS, [(pos, n) for n in negs])
        self.assertGreater(margin, 0.0,
                           "les dégradations doivent baisser le score moyen")

    def test_noop_negatives_excluded(self):
        # 2 mesures (< 4 → shuffle_bars inapplicable) et vélocités uniformes
        # (std = 0 → flatten_dynamics inapplicable) : ces négatifs seraient
        # des no-ops à marge nulle, ils doivent être exclus et comptés.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny_uniform.mid"
            pitches = [60, 64, 67, 72, 71, 67, 64, 62]
            write_midi(path, [[(float(i), 0.9, p, 80, 0)
                               for i, p in enumerate(pitches)]])
            result = file_vectors(path, seed=42, variants=2)
        self.assertIsNotNone(result)
        pos, negs, skipped = result
        # au moins flatten_dynamics + shuffle_bars × 2 variantes
        self.assertGreaterEqual(skipped, 4)
        self.assertGreater(len(negs), 0)
        # aucun négatif conservé n'est un quasi no-op
        for neg in negs:
            self.assertGreaterEqual(
                max(abs(p - n) for p, n in zip(pos, neg)), NOOP_EPS)

    def test_optimize_respects_simplex(self):
        pos, negs, _skipped = file_vectors(self.mid_path, seed=42, variants=2)
        pairs = [(pos, n) for n in negs]
        w = optimize_weights(pairs, seed=42, iters=500)
        self.assertAlmostEqual(sum(w), 1.0, places=9)
        self.assertGreaterEqual(min(w), 0.005)
        # jamais pire que le prior sur l'objectif d'accuracy/marge combinés
        acc0, mar0 = evaluate(EXPERT_WEIGHTS, pairs)
        acc1, mar1 = evaluate(w, pairs)
        self.assertGreaterEqual(acc1 + mar1, acc0 + mar0 - 1e-9)

    def test_weights_override_changes_global_only(self):
        score = build_score(self.md)
        base = SenseOfMusicalStructure(score)
        base.calculate()
        heavy_id = AXIS_IDS[0]
        override = {aid: (1.0 if aid == heavy_id else 0.0) for aid in AXIS_IDS}
        custom = SenseOfMusicalStructure(score, weights=override)
        custom.calculate()
        # scores d'axes identiques, agrégat différent
        self.assertEqual([a.score for a in base.axes],
                         [a.score for a in custom.axes])
        self.assertAlmostEqual(custom.get_score(), custom.axes[0].score, places=9)

    def test_run_calibration_and_cli(self):
        out = self.corpus / "weights.json"
        rc = cli_main(["calibrate", str(self.corpus), "--out", str(out),
                       "--variants", "1", "--iters", "300"])
        self.assertEqual(rc, 0)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["n_files"], 1)
        self.assertEqual(set(report["weights"]), set(AXIS_IDS))
        self.assertIn("discrimination", report)
        self.assertIsInstance(report["skipped_noop"], int)
        self.assertGreaterEqual(report["skipped_noop"], 0)
        # rejouable : même graine → mêmes poids
        report2 = run_calibration(self.corpus, variants=1, iters=300)
        self.assertEqual(report["weights"], report2["weights"])
        # les poids se rechargent et alimentent analyze
        weights = load_weights(out)
        rc = cli_main(["analyze", str(self.mid_path), "--weights", str(out),
                       "--quiet"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(weights), len(AXIS_IDS))

    def test_load_weights_rejects_unknown_axes(self):
        bad = self.corpus / "bad.json"
        bad.write_text('{"weights": {"99_nope": 1.0}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_weights(bad)

    def test_expert_weights_match_axes_meta(self):
        self.assertEqual(EXPERT_WEIGHTS,
                         [AXES_META[n][2] for n in sorted(AXES_META)])
        self.assertAlmostEqual(sum(EXPERT_WEIGHTS), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
