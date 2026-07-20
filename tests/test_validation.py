"""
Tests de la machinerie de validation (AUC, split par fichier, k-fold) et des
axes redressés en v3.

Ces tests portent sur le **sens** des mesures, pas sur des valeurs gelées :
un axe doit préférer l'original à sa version dégradée, un split ne doit pas
fuiter, l'AUC doit détecter une inversion. Ils échoueraient si l'un des huit
axes redressés repartait à l'envers.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libretto.axes import SenseOfMusicalStructure
from libretto.builder import build_score, meter_context
from libretto.calibrate import (
    AXIS_IDS,
    axis_auc,
    axis_auc_by_degradation,
    cross_validate,
    split_files,
)
from libretto.midi import parse_midi, write_midi


def _axes_of(md) -> dict:
    sms = SenseOfMusicalStructure(build_score(md))
    return {a.id: a.score for a in sms.calculate()}


class TestMeterContext(unittest.TestCase):
    """Item : les mesures composées ne se battent pas à la noire."""

    def test_simple_meters_are_binary(self):
        for num, den, pulse in [(4, 4, 1.0), (3, 4, 1.0), (2, 2, 2.0), (5, 4, 1.0)]:
            p, sub = meter_context(num, den)
            self.assertEqual((p, sub), (pulse, 2), f"{num}/{den}")

    def test_compound_meters_are_ternary(self):
        # 6/8, 9/8, 12/8 se battent à la noire pointée, subdivision ternaire.
        for num in (6, 9, 12):
            p, sub = meter_context(num, 8)
            self.assertEqual((p, sub), (1.5, 3), f"{num}/8")

    def test_three_eight_stays_binary(self):
        # 3/8 est trop court pour la noire pointée : il se bat à la croche.
        self.assertEqual(meter_context(3, 8), (0.5, 2))

    def test_compound_eighths_are_not_syncopated(self):
        """Le cas qui motive tout : en 6/8, des croches régulières sont SUR
        le temps. La v2, qui découpait en noires, les comptait à contretemps
        — deux tiers d'entre elles tombant hors des noires entières."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compound.mid"
            # 8 mesures de 6/8 : six croches par mesure, toutes sur la grille.
            notes = []
            for bar in range(8):
                for k in range(6):
                    notes.append((bar * 3.0 + k * 0.5, 0.45, 60 + (k % 3) * 4, 80, 0))
            write_midi(path, [notes], ppq=480, bpm=100, time_sig=(6, 8))
            score = build_score(parse_midi(path))
        self.assertEqual(score.subdivision, 3)
        self.assertEqual(score.pulse_beats, 1.5)
        phases = [p for s in score.sections for p in s.onset_phases]
        self.assertTrue(phases)
        # Toute attaque tombe sur 0, 1/3 ou 2/3 de pulsation : sur la grille.
        for p in phases:
            self.assertLess(min(abs(p - k / 3) for k in range(4)), 0.02, p)
        ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}
        self.assertEqual(ax["20_melodic_rhythm_syncopation"].details["subdivision"], 3)
        self.assertLess(ax["20_melodic_rhythm_syncopation"].details["ratio_syncopes"],
                        0.05, "des croches ternaires régulières ne sont pas des syncopes")


class TestAxisAUC(unittest.TestCase):
    def test_auc_detects_inversion(self):
        # positif toujours sous le négatif sur l'axe 0 → AUC 0 ; l'inverse
        # sur l'axe 1 → AUC 1 ; égalité → 0.5.
        n = len(AXIS_IDS)
        pos = [0.0, 1.0] + [0.5] * (n - 2)
        neg = [1.0, 0.0] + [0.5] * (n - 2)
        corpus = {"f": (pos, [("deg", neg)], 0)}
        auc = axis_auc(corpus)
        self.assertEqual(auc[AXIS_IDS[0]], 0.0)
        self.assertEqual(auc[AXIS_IDS[1]], 1.0)
        self.assertEqual(auc[AXIS_IDS[2]], 0.5)

    def test_auc_by_degradation_splits_buckets(self):
        n = len(AXIS_IDS)
        pos = [0.5] * n
        good = [0.0] * n     # détecté
        blind = [0.5] * n    # égalité
        corpus = {"f": (pos, [("a", good), ("b", blind)], 0)}
        by = axis_auc_by_degradation(corpus)
        self.assertEqual(sorted(by), ["a", "b"])
        self.assertEqual(by["a"][AXIS_IDS[0]], 1.0)
        self.assertEqual(by["b"][AXIS_IDS[0]], 0.5)


class TestSplitIntegrity(unittest.TestCase):
    def test_split_is_a_partition(self):
        keys = [f"f{i}" for i in range(23)]
        folds = split_files(keys, 5, seed=42)
        self.assertEqual(len(folds), 5)
        flat = [k for f in folds for k in f]
        self.assertEqual(sorted(flat), sorted(keys))       # rien perdu
        self.assertEqual(len(flat), len(set(flat)))        # rien dupliqué

    def test_split_is_deterministic(self):
        keys = [f"f{i}" for i in range(20)]
        self.assertEqual(split_files(keys, 4, 7), split_files(keys, 4, 7))
        self.assertNotEqual(split_files(keys, 4, 7), split_files(keys, 4, 8))

    def test_split_clamped_to_corpus_size(self):
        folds = split_files(["a", "b"], 10, seed=1)
        self.assertEqual(len(folds), 2)

    def test_no_file_appears_in_train_and_test(self):
        """Le cœur de l'affaire : les négatifs d'un fichier partagent son
        vecteur positif. Un split par PAIRE mettrait donc le même positif des
        deux côtés — l'accuracy de test mesurerait de la mémorisation."""
        n = len(AXIS_IDS)
        corpus = {f"f{i}": ([0.6] * n, [("d", [0.4] * n)] * 3, 0) for i in range(10)}
        report = cross_validate(corpus, k=5, seed=42, iters=50)
        self.assertEqual(report["n_folds"], 5)
        for fold in report["folds"]:
            self.assertEqual(fold["n_train_files"] + fold["n_test_files"], 10)

    def test_cross_validate_refuses_tiny_corpus(self):
        n = len(AXIS_IDS)
        report = cross_validate({"only": ([0.6] * n, [("d", [0.4] * n)], 0)},
                                k=5, seed=1, iters=10)
        self.assertEqual(report["n_folds"], 0)


class TestRepairedAxes(unittest.TestCase):
    """Chaque test isole l'inversion qui rendait l'axe faux, avec une paire
    construite à la main plutôt qu'un corpus — le sens doit tenir sans
    dépendre d'un échantillon."""

    def _score(self, notes, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mid"
            write_midi(path, [notes], ppq=480, bpm=100, **kw)
            return build_score(parse_midi(path))

    def test_axis12_accepts_one_chord_per_bar(self):
        """v2 : la bande retombait à 0 au-delà de 95 % de changements, donc
        « un accord neuf à chaque mesure » — la grille pop la plus banale —
        scorait 0."""
        notes = []
        for bar, root in enumerate([60, 67, 69, 65] * 4):
            for iv in (0, 4, 7):
                notes.append((bar * 4.0, 3.8, root + iv, 80, 0))
        score = self._score(notes)
        ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}
        self.assertGreater(ax["12_harmonic_rhythm"].score, 0.6,
                           "un accord par mesure est un rythme harmonique régulier")

    def test_axis14_accepts_bass_moving_every_chord(self):
        """v2 : band(..., 0.9, 1.01) plaçait un mouvement permanent (ratio
        1.0) dans la pente descendante."""
        notes = []
        for bar, root in enumerate([60, 67, 69, 65] * 4):
            for iv in (0, 4, 7):
                notes.append((bar * 4.0, 3.8, root + iv, 80, 0))
            notes.append((bar * 4.0, 3.8, root - 24, 88, 0))
        ax = {a.id: a for a in SenseOfMusicalStructure(self._score(notes)).calculate()}
        self.assertGreater(ax["14_bass_melodic"].details["ratio_mouvement"], 0.9)
        self.assertGreater(ax["14_bass_melodic"].score, 0.6)

    def test_axis07_rewards_relief_not_flatness(self):
        """v2 notait `1 − |Δénergie|` : aplatir toutes les vélocités d'un
        morceau lui faisait GAGNER des points (AUC 0.23 face à
        flatten_dynamics)."""
        def build(velocities):
            notes = []
            for i, vel in enumerate(velocities):
                for bar in range(4):
                    b = (i * 4 + bar) * 4.0
                    for iv in (0, 4, 7):
                        notes.append((b, 3.8, 60 + (bar * 5) % 12 + iv, vel, 0))
                    notes.append((b + 2.0, 1.0, 48, vel, 0))
            return self._score(notes)
        contrasted = build([50, 95, 55, 100])
        flat = build([75, 75, 75, 75])
        a_con = {a.id: a for a in SenseOfMusicalStructure(contrasted).calculate()}
        a_flat = {a.id: a for a in SenseOfMusicalStructure(flat).calculate()}
        self.assertGreaterEqual(a_con["07_section_transition"].score,
                                a_flat["07_section_transition"].score,
                                "un morceau à relief ne doit pas perdre contre un morceau aplati")

    def test_axis13_needs_an_anchor(self):
        """Le contraste tonal est une excursion DEPUIS un centre. Sans
        ancrage, des sections dispersées au hasard obtenaient le score
        maximal (AUC 0.36)."""
        # Ancré : tout en Do, une section s'écarte vers Sol.
        anchored, drifting = [], []
        for sec, root in enumerate([60, 60, 67, 60]):
            for bar in range(4):
                b = (sec * 4 + bar) * 4.0
                for iv in (0, 4, 7):
                    anchored.append((b, 3.8, root + iv, 80, 0))
        # Errant : chaque section dans une tonalité éloignée, aucun centre.
        for sec, root in enumerate([60, 66, 61, 68]):
            for bar in range(4):
                b = (sec * 4 + bar) * 4.0
                for iv in (0, 4, 7):
                    drifting.append((b, 3.8, root + iv, 80, 0))
        a1 = {a.id: a for a in SenseOfMusicalStructure(self._score(anchored)).calculate()}
        a2 = {a.id: a for a in SenseOfMusicalStructure(self._score(drifting)).calculate()}
        self.assertGreater(a1["13_tonal_contrast"].score, a2["13_tonal_contrast"].score)

    def test_axis27_does_not_punish_rich_orchestration(self):
        """v2 : band(..., 4.0, 8.0) faisait redescendre le score au-delà de
        4 voix. Un octuor n'est pas moins bien écrit qu'un trio — et surtout,
        la pente descendante rendait l'axe inversible par désynchronisation."""
        def build(n_voices):
            notes = []
            for bar in range(8):
                for v in range(n_voices):
                    notes.append((bar * 4.0, 3.8, 48 + v * 4, 80, 0))
            return self._score(notes)
        ax3 = {a.id: a for a in SenseOfMusicalStructure(build(3)).calculate()}
        ax7 = {a.id: a for a in SenseOfMusicalStructure(build(7)).calculate()}
        self.assertGreaterEqual(ax7["27_voice_count"].score,
                                ax3["27_voice_count"].score * 0.95)


class TestSegmentationRobustness(unittest.TestCase):
    def test_trailing_empty_bar_does_not_kill_segmentation(self):
        """Régression : une mesure de queue quasi vide produisait une
        nouveauté de 1.0 qui, via moyenne + σ, poussait le seuil au-dessus de
        toutes les vraies frontières — le morceau entier ressortait en une
        seule section. 18 fichiers sur 60 du corpus de validation étaient
        dans ce cas."""
        notes = []
        # 4 sections nettes de 8 mesures : harmonie ET intensité changent.
        for sec, (root, vel) in enumerate([(60, 55), (65, 100), (60, 60), (69, 95)]):
            for bar in range(8):
                b = (sec * 8 + bar) * 4.0
                for iv in (0, 4, 7):
                    notes.append((b, 3.8, root + iv, vel, 0))
                notes.append((b + 2.0, 1.5, root - 12, vel, 0))
        # la note tenue qui déborde et fabrique la mesure fantôme
        notes.append((31 * 4.0, 5.0, 60, 70, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tail.mid"
            write_midi(path, [notes], ppq=480, bpm=100)   # aucun marqueur
            score = build_score(parse_midi(path))
        self.assertGreaterEqual(len(score.sections), 2,
                                "la segmentation par nouveauté doit survivre à la mesure de queue")


class TestCorruptMetadata(unittest.TestCase):
    def test_absurd_time_signature_denominator_is_ignored(self):
        """Un octet corrompu dans une meta 0x58 donnait un dénominateur de
        2**200. La durée de mesure tombait alors à zéro et le builder
        découpait la pièce en autant de mesures qu'elle a de ticks : une
        bombe de calcul déclenchée par un seul octet."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.mid"
            notes = [(bar * 4.0 + k, 0.9, 60 + (k * 3) % 12, 80, 0)
                     for bar in range(240) for k in range(4)]
            write_midi(path, [notes], ppq=480)
            raw = bytearray(path.read_bytes())
            i = raw.find(bytes([0xFF, 0x58, 0x04]))
            self.assertGreater(i, 0, "meta time-signature introuvable")
            raw[i + 4] = 200                       # dénominateur = 2**200
            path.write_bytes(bytes(raw))
            md = parse_midi(path)
            self.assertEqual(md.time_sigs, [], "la signature aberrante doit être ignorée")
            score = build_score(md)                # ne doit ni exploser ni traîner
        self.assertEqual(score.pulse_beats, 1.0)   # repli sur 4/4

    def test_zero_numerator_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zero.mid"
            write_midi(path, [[(0.0, 1.0, 60, 80, 0)]], ppq=480)
            raw = bytearray(path.read_bytes())
            i = raw.find(bytes([0xFF, 0x58, 0x04]))
            raw[i + 3] = 0                         # numérateur = 0
            path.write_bytes(bytes(raw))
            self.assertEqual(parse_midi(path).time_sigs, [])


if __name__ == "__main__":
    unittest.main()
