"""Tests de Forge — le mode rapport axe par axe (`--axes`) et les contraintes.

Ce qu'on vérifie n'est pas cosmétique : la promesse du mode est que la
décomposition est COMPLÈTE — la somme des leviers (écart × poids) vaut
exactement l'écart entre le SMS du gagnant et le SMS moyen du peloton.
Si un axe manquait ou qu'un poids était faux, ce test le verrait.
"""

import io
import json
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from forge import (Constraints, _print_axes_report,  # noqa: E402
                   apply_constraints, diverse_shortlist, forge, forge_from_dir,
                   forms_matching_bars, parse_meter, parse_mode, parse_tonic)
from make_corpus import FORMS, random_style, total_bars  # noqa: E402

from libretto.axes import AXES_META  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestPontsOptionnels(unittest.TestCase):
    """Les ponts à dépendances optionnelles (MusicLang, ACE-Step,
    round-trip) doivent rester IMPORTABLES sans leurs dépendances — le
    contrat stdlib du dépôt s'arrête à leur main(), pas à leur import."""

    def test_importables_sans_dependances(self):
        import audio2midi
        import forge_acestep
        import forge_embellish
        import forge_musiclang
        import transcription_roundtrip
        for mod in (audio2midi, forge_acestep, forge_embellish,
                    forge_musiclang, transcription_roundtrip):
            self.assertTrue(callable(mod.main), mod.__name__)


class TestEmbellishMoves(unittest.TestCase):
    """Les retouches doivent rester BORNÉES et déterministes : un embellisseur
    qui sort un pitch à 130 ou une durée négative produit un MIDI illégal, et
    Forge jugerait du bruit. On les teste sur des notes synthétiques (les
    invariants ne dépendent pas d'un vrai morceau)."""

    @staticmethod
    def _notes():
        from libretto.midi import MidiNote
        # Un accord tenu (3 notes simultanées) + une percussion + une note grave.
        return [
            MidiNote(0, 480, 60, 80, 0, 1),
            MidiNote(0, 480, 64, 70, 0, 1),
            MidiNote(0, 480, 67, 75, 0, 1),
            MidiNote(480, 960, 36, 100, 9, 2),   # percussion (canal 9)
            MidiNote(480, 960, 40, 60, 2, 3),    # basse
        ]

    def _assert_legal(self, notes):
        for n in notes:
            self.assertGreaterEqual(n.pitch, 0)
            self.assertLessEqual(n.pitch, 127)
            self.assertGreaterEqual(n.velocity, 1)
            self.assertLessEqual(n.velocity, 127)
            self.assertGreaterEqual(n.start, 0)
            self.assertGreater(n.end, n.start)   # durée ≥ 1 tick

    def test_humanize_borne_et_preserve_le_compte(self):
        import random
        from forge_embellish import humanize
        out = humanize(self._notes(), 480, random.Random("t"))
        self._assert_legal(out)
        self.assertEqual(len(out), 5)

    def test_arc_borne_et_preserve_le_compte(self):
        import random
        from forge_embellish import shape_dynamics
        out = shape_dynamics(self._notes(), 960, random.Random("t"))
        self._assert_legal(out)
        self.assertEqual(len(out), 5)

    def test_octaves_ajoute_sans_toucher_les_percussions(self):
        import random
        from forge_embellish import double_octaves
        src = self._notes()
        out = double_octaves(src, random.Random("t"))
        self._assert_legal(out)
        self.assertGreaterEqual(len(out), len(src))  # doublages = notes en plus
        # Aucune note ajoutée sur le canal 9 (percussions non transposées).
        drums_before = sum(1 for n in src if n.channel == 9)
        drums_after = sum(1 for n in out if n.channel == 9)
        self.assertEqual(drums_before, drums_after)

    def test_arpege_borne_et_decale_l_accord(self):
        import random
        from forge_embellish import arpeggiate
        out = arpeggiate(self._notes(), 480, random.Random("t"))
        self._assert_legal(out)
        self.assertEqual(len(out), 5)
        # Les 3 notes de l'accord (canal 0) n'attaquent plus toutes à 0.
        starts = sorted(n.start for n in out if n.channel == 0)
        self.assertGreater(starts[-1], 0)

    def test_deterministe(self):
        import random
        from forge_embellish import build_variation
        from libretto.midi import MidiData
        md = MidiData(ppq=480, notes=self._notes(), end_tick=960)
        moves = ("humanize", "arc", "octaves", "arp")
        a = build_variation(md, moves, random.Random("s:1"))
        b = build_variation(md, moves, random.Random("s:1"))
        self.assertEqual([(n.start, n.end, n.pitch, n.velocity) for n in a.notes],
                         [(n.start, n.end, n.pitch, n.velocity) for n in b.notes])


class TestEmbellishIntegration(unittest.TestCase):
    """L'original concourt (variante 000), le verdict compare gagnant vs
    original, et tout est rejouable — on part d'un morceau généré dont la
    structure est riche."""

    @classmethod
    def setUpClass(cls):
        from forge_embellish import embellish
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.src = root / "source.mid"
        # Un morceau généré comme séquence de départ (make_corpus via forge).
        forge(root / "g", n=1, seed=5, keep_all=True)
        (cls.src).write_bytes((root / "g" / "candidate_000.mid").read_bytes())
        cls.report = embellish(cls.src, root / "out", n=12, seed=1, shortlist=3)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_original_concourt_et_recettes_mappees(self):
        emb = self.report["embellish"]
        self.assertEqual(emb["recipes"]["variation_000.mid"], "original")
        # Chaque entrée du classement porte sa recette.
        for c in self.report["leaderboard"]:
            self.assertIn("recipe", c)

    def test_verdict_coherent(self):
        v = self.report["embellish"]["verdict"]
        self.assertIsNotNone(v)
        if v["original_eligible"] and not v["original_won"]:
            # L'amélioration est bien la différence gagnant − original.
            self.assertAlmostEqual(
                v["improvement"],
                v["winner_score"] - v["original_score"], delta=1e-3)
            self.assertGreater(v["improvement"], 0)

    def test_variantes_ecrites_et_jugees(self):
        cand = Path(self._tmp.name) / "out" / "candidates"
        mids = sorted(cand.glob("variation_*.mid"))
        self.assertEqual(len(mids), 12)
        # Le gagnant copié existe, le rapport JSON aussi.
        self.assertTrue((Path(self._tmp.name) / "out" / "forge_winner.mid").exists())


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

# --------------------------------------------------------------------------- #
# Contraintes — commander le générateur au lieu de le subir                   #
#                                                                             #
# Ce qui est vérifié tient en une phrase : ce qu'on impose sort tel quel, ce   #
# qu'on n'impose pas continue de varier. Le second point compte autant que le  #
# premier — un générateur entièrement contraint rendrait N clones et la        #
# sélection de Forge n'aurait plus d'objet.                                    #
# --------------------------------------------------------------------------- #


class TestParsers(unittest.TestCase):
    """Les noms arrivent d'un humain ou d'un LLM, pas d'un fichier MIDI."""

    def test_tonique_anglais_francais_alterations(self):
        for value, expected in [("F", 5), ("Fa", 5), ("fa#", 6), ("F#", 6),
                                ("Sib", 10), ("Bb", 10), ("B", 11), ("ré", 2),
                                ("do", 0), (7, 7), ("11", 11)]:
            with self.subTest(value=value):
                self.assertEqual(parse_tonic(value), expected)

    def test_tonique_b_seul_est_si_becarre(self):
        """Le piège : « b » est une note ET le signe bémol."""
        self.assertEqual(parse_tonic("b"), 11)
        self.assertEqual(parse_tonic("bb"), 10)

    def test_tonique_refusee(self):
        for bad in ("H", "", "12", "zz"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_tonic(bad)

    def test_mode_alias(self):
        for value in ("min", "minor", "mineur", "MINEUR", "aeolian"):
            with self.subTest(value=value):
                self.assertEqual(parse_mode(value), "min")
        self.assertEqual(parse_mode("dorian"), "dorien")
        with self.assertRaises(ValueError):
            parse_mode("phrygien")

    def test_metrique_refuse_ce_que_le_generateur_ignore(self):
        self.assertEqual(parse_meter("4/4"), (4, 4, 4.0, 1.0))
        self.assertEqual(parse_meter("6/8"), (6, 8, 3.0, 1.5))
        with self.assertRaises(ValueError):
            parse_meter("7/8")          # absente de METERS : refus, pas invention
        with self.assertRaises(ValueError):
            parse_meter("quatre")


class TestLongueur(unittest.TestCase):
    """`bars_per_section` est une consigne ; `total_bars` est le décompte."""

    def test_intro_et_outro_comptent_de_moitie(self):
        # verse_chorus = 8 sections dont intro + outro écourtées.
        self.assertEqual(total_bars("verse_chorus", 2), 6 * 2 + 2 * 2)
        self.assertEqual(total_bars("verse_chorus", 8), 6 * 8 + 2 * 4)
        # Sans intro ni outro, le produit simple s'applique.
        self.assertEqual(total_bars("binaire", 4), 16)

    def test_couples_pour_16_mesures(self):
        couples = forms_matching_bars(16)
        self.assertIn(("binaire", 4), couples)
        self.assertIn(("verse_chorus", 2), couples)
        for form, per in couples:
            self.assertEqual(total_bars(form, per), 16)

    def test_longueur_inatteignable(self):
        self.assertEqual(forms_matching_bars(17), [])
        rng = random.Random(0)
        with self.assertRaises(ValueError) as ctx:
            apply_constraints(random_style(rng), Constraints(bars=17), rng)
        # L'erreur doit ORIENTER, pas seulement refuser.
        self.assertIn("16", str(ctx.exception))


class TestApplication(unittest.TestCase):

    def test_impose_ce_qui_est_demande(self):
        rng = random.Random(1)
        cons = Constraints(tonic=5, mode="min", bpm=72.0,
                           meter=(4, 4, 4.0, 1.0), bars=16,
                           swing=True, syncopation=0.45, drums=True)
        for i in range(20):
            style = apply_constraints(random_style(random.Random(i)), cons, rng)
            with self.subTest(i=i):
                self.assertEqual(style.tonic, 5)
                self.assertEqual(style.mode, "min")
                self.assertEqual(style.bpm, 72.0)
                self.assertEqual(style.meter, (4, 4, 4.0, 1.0))
                self.assertTrue(style.swing)
                self.assertTrue(style.with_drums)
                self.assertEqual(total_bars(style.form, style.bars_per_section), 16)

    def test_tempo_impose_interdit_la_derive(self):
        """Sinon la contrainte ne tient qu'au premier temps."""
        rng = random.Random(2)
        # Une graine qui active la dérive, pour que le test morde.
        drifting = next(s for s in (random_style(random.Random(i)) for i in range(60))
                        if s.tempo_drift)
        style = apply_constraints(drifting, Constraints(bpm=90.0), rng)
        self.assertEqual(style.bpm, 90.0)
        self.assertFalse(style.tempo_drift)

    def test_sans_contrainte_le_style_est_intact(self):
        rng = random.Random(3)
        original = random_style(random.Random(9))
        self.assertEqual(apply_constraints(original, Constraints(), rng), original)

    def test_ce_qui_n_est_pas_impose_varie_encore(self):
        """Le point qui fait que Forge a encore quelque chose à choisir."""
        rng = random.Random(4)
        cons = Constraints(tonic=5, mode="min", bpm=72.0, bars=16)
        styles = [apply_constraints(random_style(random.Random(i)), cons, rng)
                  for i in range(30)]
        self.assertGreater(len({s.form for s in styles}), 1)
        self.assertGreater(len({s.arc for s in styles}), 1)
        self.assertGreater(len({s.n_voices for s in styles}), 1)

    def test_bars_ne_fige_pas_la_forme(self):
        """La contrainte porte sur la LONGUEUR ; plusieurs formes y répondent."""
        rng = random.Random(5)
        forms = {apply_constraints(random_style(random.Random(i)),
                                   Constraints(bars=16), rng).form
                 for i in range(40)}
        self.assertGreater(len(forms), 1)
        self.assertTrue(forms <= set(FORMS))


class TestForgeContraint(unittest.TestCase):

    def test_forge_bout_en_bout(self):
        """Le rapport doit permettre de VÉRIFIER la commande, pas seulement de
        constater un résultat : tonalité et longueur y figurent."""
        cons = Constraints(tonic=5, mode="min", bpm=72.0,
                           meter=(4, 4, 4.0, 1.0), bars=16)
        with tempfile.TemporaryDirectory() as tmp:
            report = forge(tmp, n=6, seed=1, constraints=cons)

            self.assertEqual(report["constraints"]["tonic_name"], "F")
            self.assertEqual(report["constraints"]["bars"], 16)
            self.assertTrue(report["leaderboard"], "aucun candidat éligible")
            for c in report["leaderboard"]:
                self.assertEqual(c["key"], "F min")
                self.assertEqual(c["bpm"], 72)
                self.assertEqual(c["meter"], "4/4")
                self.assertEqual(c["bars"], 16)
            if report["winner"]:
                self.assertTrue((Path(tmp) / report["winner_file"]).is_file())

    def test_rapport_sans_contrainte(self):
        """Rétrocompatible : `constraints` vaut None quand rien n'est imposé."""
        with tempfile.TemporaryDirectory() as tmp:
            report = forge(tmp, n=2, seed=1)
        self.assertIsNone(report["constraints"])


if __name__ == "__main__":
    unittest.main()
