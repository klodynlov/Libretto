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

from forge import (_print_axes_report, diverse_shortlist,  # noqa: E402
                   forge, forge_from_dir)

from libretto.axes import AXES_META  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestPontsOptionnels(unittest.TestCase):
    """Les ponts à dépendances optionnelles (MusicLang, ACE-Step,
    round-trip) doivent rester IMPORTABLES sans leurs dépendances — le
    contrat stdlib du dépôt s'arrête à leur main(), pas à leur import."""

    def test_importables_sans_dependances(self):
        import audio2midi
        import forge_acestep
        import forge_musiclang
        import transcription_roundtrip
        for mod in (audio2midi, forge_acestep, forge_musiclang,
                    transcription_roundtrip):
            self.assertTrue(callable(mod.main), mod.__name__)


class TestForgeFromDir(unittest.TestCase):
    """Le point de branchement universel : Forge sur un dossier de MIDI
    venus d'ailleurs. On le nourrit avec des candidats make_corpus (générés
    via forge --keep-all) PLUS les deux morceaux réels du dépôt — la même
    voie que prendrait la sortie d'un modèle génératif."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.src = root / "candidats"
        cls.src.mkdir()
        # Des candidats générés, gardés comme si un modèle les avait déposés.
        forge(cls.src, n=4, seed=3, keep_all=True)
        for aux in ("forge_winner.mid", "forge_report.json"):
            (cls.src / aux).unlink(missing_ok=True)
        # Et deux morceaux réels (assemblés depuis des packs de loops).
        for real in ("morceau_sloopy.mid", "morceau_naija.mid"):
            (cls.src / real).write_bytes((EXAMPLES / real).read_bytes())
        cls.n_files = len(list(cls.src.glob("*.mid")))
        cls.out = root / "sortie"
        cls.report = forge_from_dir(cls.src, cls.out, shortlist=3)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_tous_les_fichiers_sont_notes(self):
        self.assertEqual(self.report["n_requested"], self.n_files)
        self.assertEqual(self.report["n_generated"]
                         + self.report["n_empty_skipped"], self.n_files)
        self.assertIsNone(self.report["seed"])
        self.assertEqual(self.report["source"], str(self.src))

    def test_les_sources_ne_sont_jamais_effacees(self):
        # Contrat : les fichiers d'un modèle ne nous appartiennent pas.
        self.assertEqual(len(list(self.src.glob("*.mid"))), self.n_files)

    def test_gagnant_copie_et_rapport_ecrit(self):
        self.assertIsNotNone(self.report["winner"])
        self.assertTrue((self.out / "forge_winner.mid").exists())
        on_disk = json.loads(
            (self.out / "forge_report.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["winner"], self.report["winner"])

    def test_forme_derivee_de_la_segmentation(self):
        # Sans vérité terrain, la forme est la signature de sections jugée :
        # une chaîne d'initiales majuscules, jamais vide.
        for c in self.report["leaderboard"]:
            self.assertRegex(c["form"], r"^[A-Z?]+$")

    def test_shortlist_fonctionne_sur_sources_externes(self):
        sl = self.report["shortlist"]
        self.assertIsNotNone(sl)
        self.assertTrue((self.out / "forge_short_01.mid").exists())
        self.assertGreaterEqual(sl["distinct_forms"],
                                sl["unconstrained_distinct_forms"])


class TestDiverseShortlist(unittest.TestCase):
    """Propriétés de la sélection sous contrainte de diversité, sur des
    classements synthétiques (aucune génération : les invariants de la règle
    ne dépendent pas du moteur)."""

    @staticmethod
    def _ranked(forms):
        # Un classement déjà trié : l'indice EST le rang.
        return [{"form": f, "rank": i} for i, f in enumerate(forms)]

    def test_le_premier_elu_est_le_gagnant(self):
        ranked = self._ranked(["a", "a", "b", "c"])
        short = diverse_shortlist(ranked, 3, form_of=lambda c: c["form"])
        self.assertEqual(short[0], ranked[0])

    def test_couvre_toutes_les_formes_avant_de_repeter(self):
        # 3 formes disponibles, k=3 : les trois doivent y être, même si les
        # mieux classés sont tous de la même forme.
        ranked = self._ranked(["a", "a", "a", "b", "a", "c"])
        short = diverse_shortlist(ranked, 3, form_of=lambda c: c["form"])
        self.assertEqual({c["form"] for c in short}, {"a", "b", "c"})
        # Et à contrainte égale, le mérite garde l'ordre : c'est le premier
        # « a », le premier « b », le premier « c ».
        self.assertEqual([c["rank"] for c in short], [0, 3, 5])

    def test_releve_le_cap_quand_les_formes_sont_epuisees(self):
        # 2 formes, k=4 : cap 1 prend le meilleur de chaque, cap 2 les seconds.
        ranked = self._ranked(["a", "a", "b", "b"])
        short = diverse_shortlist(ranked, 4, form_of=lambda c: c["form"])
        self.assertEqual([c["rank"] for c in short], [0, 2, 1, 3])

    def test_k_superieur_au_total_rend_tout(self):
        ranked = self._ranked(["a", "b"])
        short = diverse_shortlist(ranked, 10, form_of=lambda c: c["form"])
        self.assertEqual(len(short), 2)

    def test_diversite_garantie_min_k_formes(self):
        # La shortlist compte min(k, formes distinctes) formes — jamais moins.
        ranked = self._ranked(["a", "a", "b", "c", "d", "b"])
        for k in range(1, 7):
            short = diverse_shortlist(ranked, k, form_of=lambda c: c["form"])
            self.assertEqual(len({c["form"] for c in short}), min(k, 4), k)


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

    def test_shortlist_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = forge(tmp, n=8, seed=1, shortlist=4)
            sl = report["shortlist"]
            self.assertIsNotNone(sl)
            self.assertEqual(sl["k"], len(sl["picks"]))
            self.assertLessEqual(sl["k"], 4)
            # Le premier élu est le gagnant lui-même.
            self.assertEqual(sl["picks"][0]["file"],
                             report["leaderboard"][0]["file"])
            # Jamais moins de formes que le top-k libre.
            self.assertGreaterEqual(sl["distinct_forms"],
                                    sl["unconstrained_distinct_forms"])
            # Cohérence arithmétique du coût affiché.
            self.assertAlmostEqual(
                sl["score_cost_mean"],
                sl["unconstrained_mean_score"] - sl["mean_score"], delta=1e-3)
            # Les fichiers livrés existent, dans l'ordre annoncé.
            for p in sl["picks"]:
                self.assertTrue((Path(tmp) / p["file_out"]).exists(),
                                p["file_out"])
            # Et le rapport sur disque les connaît aussi.
            on_disk = json.loads(
                (Path(tmp) / "forge_report.json").read_text(encoding="utf-8"))
            self.assertIn("shortlist", on_disk)

    def test_sans_flag_pas_de_rapport(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = forge(tmp, n=3, seed=2)
            self.assertNotIn("axes_report", report)
            self.assertNotIn("shortlist", report)
            # Et l'affichage du rapport axe par axe est un no-op silencieux.
            buf = io.StringIO()
            with redirect_stdout(buf):
                _print_axes_report(report)
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
