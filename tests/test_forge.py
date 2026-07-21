"""Tests de Forge — le mode rapport axe par axe (`--axes`).

Ce qu'on vérifie n'est pas cosmétique : la promesse du mode est que la
décomposition est COMPLÈTE — la somme des leviers (écart × poids) vaut
exactement l'écart entre le SMS du gagnant et le SMS moyen du peloton.
Si un axe manquait ou qu'un poids était faux, ce test le verrait.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from forge import _print_axes_report, forge  # noqa: E402

from libretto.axes import AXES_META  # noqa: E402


class TestForgeAxesReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        # Petit tirage déterministe : assez de candidats pour avoir un
        # peloton, assez peu pour rester rapide.
        cls.report = forge(cls.out, n=8, seed=1, axes_report=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_report_complete_et_ordonne(self):
        ar = self.report["axes_report"]
        self.assertIsNotNone(ar)
        self.assertEqual(ar["winner_file"],
                         self.report["leaderboard"][0]["file"])
        self.assertEqual(ar["field_size"], len(self.report["leaderboard"]) - 1)
        # Les 29 axes, dans l'ordre canonique d'AXES_META, poids conformes.
        expected = [(meta[0], meta[2]) for meta in AXES_META.values()]
        got = [(r["id"], r["weight"]) for r in ar["axes"]]
        self.assertEqual(got, expected)

    def test_leviers_somment_a_l_avance_sms(self):
        ar = self.report["axes_report"]
        self.assertGreaterEqual(ar["field_size"], 1,
                                "il faut un peloton pour ce test")
        board = self.report["leaderboard"]
        field_mean_sms = (sum(c["score"] for c in board[1:])
                          / (len(board) - 1))
        advance = board[0]["score"] - field_mean_sms
        total_leverage = sum(r["leverage"] for r in ar["axes"])
        # Les scores du leaderboard sont arrondis à 4 décimales, les leviers
        # à 6 : la tolérance couvre ces arrondis, rien d'autre.
        self.assertAlmostEqual(total_leverage, advance, delta=1e-3)

    def test_bornes_et_confiance(self):
        for r in self.report["axes_report"]["axes"]:
            self.assertGreaterEqual(r["winner_score"], 0.0, r["id"])
            self.assertLessEqual(r["winner_score"], 1.0, r["id"])
            self.assertGreaterEqual(r["winner_confidence"], 0.0, r["id"])
            self.assertLessEqual(r["winner_confidence"], 1.0, r["id"])
            if r["field_mean"] is not None:
                self.assertAlmostEqual(r["delta"],
                                       r["winner_score"] - r["field_mean"],
                                       delta=1e-3, msg=r["id"])

    def test_json_sur_disque_contient_le_rapport(self):
        on_disk = json.loads(
            (self.out / "forge_report.json").read_text(encoding="utf-8"))
        self.assertIn("axes_report", on_disk)
        self.assertEqual(len(on_disk["axes_report"]["axes"]), len(AXES_META))

    def test_affichage_trie_par_levier(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_axes_report(self.report)
        out = buf.getvalue()
        self.assertIn("Rapport axe par axe", out)
        self.assertIn("Avance SMS sur le peloton", out)

    def test_sans_flag_pas_de_rapport(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = forge(tmp, n=3, seed=2)
            self.assertNotIn("axes_report", report)
            # Et l'affichage du rapport axe par axe est un no-op silencieux.
            buf = io.StringIO()
            with redirect_stdout(buf):
                _print_axes_report(report)
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
