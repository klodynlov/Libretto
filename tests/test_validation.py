"""
Tests de la machinerie de validation (AUC, split par fichier, k-fold) et des
axes redressés en v3.

Ces tests portent sur le **sens** des mesures, pas sur des valeurs gelées :
un axe doit préférer l'original à sa version dégradée, un split ne doit pas
fuiter, l'AUC doit détecter une inversion. Ils échoueraient si l'un des huit
axes redressés repartait à l'envers.
"""

from __future__ import annotations

import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from libretto.axes import SenseOfMusicalStructure
from libretto.builder import (
    _assign_letters,
    _cosine_dist,
    _repetition_edges,
    _self_similarity,
    build_score,
    meter_context,
)
from libretto.calibrate import (
    AUDIBLE_DEGRADATIONS,
    AXIS_IDS,
    DEGRADATIONS,
    _applicable,
    degrade_scramble_dynamics,
    axis_auc,
    axis_auc_by_degradation,
    cross_validate,
    split_files,
)
from libretto.demo import demo_score
from libretto.midi import parse_midi, write_midi
from libretto.model import Score


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
        corpus = {"f": (pos, [("deg", neg)], 0, [1.0] * n)}
        auc = axis_auc(corpus)
        self.assertEqual(auc[AXIS_IDS[0]], 0.0)
        self.assertEqual(auc[AXIS_IDS[1]], 1.0)
        self.assertEqual(auc[AXIS_IDS[2]], 0.5)

    def test_auc_by_degradation_splits_buckets(self):
        n = len(AXIS_IDS)
        pos = [0.5] * n
        good = [0.0] * n     # détecté
        blind = [0.5] * n    # égalité
        corpus = {"f": (pos, [("a", good), ("b", blind)], 0, [1.0] * n)}
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
        corpus = {f"f{i}": ([0.6] * n, [("d", [0.4] * n)] * 3, 0, [1.0] * n)
                  for i in range(10)}
        report = cross_validate(corpus, k=5, seed=42, iters=50)
        self.assertEqual(report["n_folds"], 5)
        for fold in report["folds"]:
            self.assertEqual(fold["n_train_files"] + fold["n_test_files"], 10)

    def test_cross_validate_refuses_tiny_corpus(self):
        n = len(AXIS_IDS)
        report = cross_validate({"only": ([0.6] * n, [("d", [0.4] * n)], 0, [1.0] * n)},
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


class TestRepetitionSegmentation(unittest.TestCase):
    """La matrice d'auto-similarité apporte ce que la nouveauté locale ne
    peut pas voir : les retours. F-mesure des frontières 0.55 → 0.73 sur
    trois corpus annotés."""

    def _piece(self, plan, bars=8):
        """plan : suite de fondamentales, une par section."""
        notes = []
        bar = 0
        for root in plan:
            for _ in range(bars):
                b = bar * 4.0
                for iv in (0, 4, 7):
                    notes.append((b, 3.8, root + iv, 80, 0))
                notes.append((b + 2.0, 1.5, root - 12, 80, 0))
                bar += 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.mid"
            write_midi(path, [notes], ppq=480, bpm=100)   # aucun marqueur
            return build_score(parse_midi(path))

    def test_returning_section_creates_a_boundary(self):
        """A B A : le retour de A ne produit AUCUNE nouveauté — son matériau
        est déjà connu — mais forme une diagonale nette dans la matrice.
        C'est le cas que la segmentation par fenêtres seule manquait."""
        score = self._piece([60, 67, 60])
        self.assertGreaterEqual(len(score.sections), 3)

    def test_boundary_lands_on_the_transition_not_before(self):
        """Garde du recalage de pics. La nouveauté fenêtrée est lissée et
        biaisée vers l'amont — sur trois corpus annotés, les frontières
        appariées sortaient 2 mesures trop tôt quatre fois plus souvent que
        trop tard (22 contre 6). Chaque pic est recalé sur l'argmax de la
        nouveauté pas-à-pas : après recalage, 152 frontières exactes sur
        204 appariées (contre 138). Ce test fige le comportement sur un cas
        de remplissage : la frontière doit tomber SUR la bascule de
        matériau, pas dans la dérive qui la précède."""
        from libretto.builder import _detect_boundaries
        b1 = [[1.0, 0.05 * (i % 3), 0.02 * (i % 2)] for i in range(8)]
        fill = [[0.85, 0.15, 0.1], [0.7, 0.3, 0.15]]
        b2 = [[0.06 * (i % 3), 1.0, 0.03 * (i % 2)] for i in range(10)]
        bounds = _detect_boundaries(b1 + fill + b2)
        nearest = min(bounds, key=lambda b: abs(b - 10))
        self.assertEqual(nearest, 10,
                         f"la frontière doit tomber sur la bascule : {bounds}")

    def test_repetition_edges_finds_parallel_diagonal(self):
        # Deux blocs identiques séparés par un bloc différent.
        feats = ([[1.0, 0.0]] * 6) + ([[0.0, 1.0]] * 6) + ([[1.0, 0.0]] * 6)
        edges = _repetition_edges(_self_similarity(feats), min_len=4)
        self.assertTrue(edges, "aucune répétition détectée sur un motif A B A")
        # une frontière doit tomber près de 6 ou 12
        self.assertTrue(any(abs(e - 6) <= 1 or abs(e - 12) <= 1 for e in edges), edges)

    def test_phrase_lattice_does_not_emit_edges(self):
        """Garde de la porte d'échelle. Des phrases partagées entre sections
        produisent des alignements diagonaux courts (runs de 4-5 mesures)
        dont chaque bord devenait une frontière — jusqu'à 13 frontières
        inventées dans une seule pièce, espacées de la longueur de phrase.
        Un vrai retour de section dure une section (runs de 12-32 mesures
        sur les mêmes pièces). Le plus long run établit l'échelle ; un run
        sous le tiers de l'échelle est une phrase, pas une section.

        Matrice construite cellule par cellule pour contrôler exactement le
        seuil-quantile et les runs : un retour long (16 mesures, lag 20),
        un alignement de phrase (4 mesures, lag 8), fond sous le seuil."""
        n = 40
        ssm = [[0.2 if min(i, j) % 7 == 3 else 0.1 for j in range(n)]
               for i in range(n)]
        for i in range(n):
            ssm[i][i] = 1.0
        for i in range(16):                     # retour de section, lag 20
            ssm[i][i + 20] = ssm[i + 20][i] = 0.99
        for i in range(24, 28):                 # phrase partagée, lag 8
            ssm[i][i + 8] = ssm[i + 8][i] = 0.99
        sans_porte = _repetition_edges(ssm, min_len=4, scale_ratio=10 ** 9)
        self.assertIn(28, sans_porte,
                      "prémisse : sans porte, le bord de phrase existe")
        avec_porte = _repetition_edges(ssm, min_len=4)
        self.assertEqual(avec_porte, {16, 20, 36},
                         "seuls les bords du retour de section survivent")

    def test_labelling_threshold_adapts_to_material(self):
        """Seuil fixe à 0.82 : une pièce à couleur harmonique unique voyait
        toutes ses sections rangées dans le même groupe — 29 morceaux sur 34
        d'un corpus annoté ressortaient avec un seul type de section, et les
        axes 3 et 6 n'avaient plus rien à lire."""
        # Deux matériaux réellement distincts, mais dont la similarité
        # croisée (0.89) reste au-dessus de l'ancien seuil absolu de 0.82 :
        # celui-ci les fondait en un seul groupe.
        feats = [[1.0, 0.35, 0.0], [1.0, 0.35, 0.0],
                 [1.0, 0.0, 0.35], [1.0, 0.0, 0.35]]
        cross = 1.0 - _cosine_dist(feats[0], feats[2])
        self.assertGreater(cross, 0.82, "prémisse du test : au-dessus du seuil v0.2")
        letters = _assign_letters(feats)
        self.assertGreater(len(set(letters)), 1,
                           "le seuil doit s'adapter au matériau de la pièce")
        self.assertEqual(letters[0], letters[1])
        self.assertEqual(letters[2], letters[3])

    def test_uniform_material_stays_one_group(self):
        # Sections réellement identiques : un seul groupe, pas un découpage
        # inventé sur du bruit numérique.
        feats = [[1.0, 0.5, 0.2]] * 5
        self.assertEqual(len(set(_assign_letters(feats))), 1)


class TestScrambleDynamics(unittest.TestCase):
    """`scramble_dynamics` doit détruire l'ORGANISATION de la dynamique sans
    toucher à sa quantité — c'est ce qui la distingue de `flatten_dynamics`,
    que l'écoute a réfutée."""

    def _midi(self, tmp: Path) -> Path:
        notes = []
        for bar in range(16):
            vel = 40 + (bar // 4) * 25           # paliers d'intensité
            for iv in (0, 4, 7):
                notes.append((bar * 4.0, 3.6, 60 + iv, vel, 0))
            notes.append((bar * 4.0 + 2.0, 0.4, 38, vel + 5, 9))   # percussion
        path = tmp / "d.mid"
        write_midi(path, [notes], ppq=480, bpm=100)
        return path

    def test_preserves_velocity_histogram_per_channel(self):
        """L'amplitude dynamique est conservée : seule sa place change. Sans
        cette propriété, on ne saurait pas si l'oreille réagit au désordre ou
        à un simple écrasement de la plage."""
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            out = degrade_scramble_dynamics(md, random.Random(1))
        for channel in {n.channel for n in md.notes}:
            before = Counter(n.velocity for n in md.notes if n.channel == channel)
            after = Counter(n.velocity for n in out.notes if n.channel == channel)
            self.assertEqual(before, after, f"canal {channel}")

    def test_does_not_mix_velocities_across_channels(self):
        """Une permutation globale enverrait les vélocités des percussions sur
        le piano : ça déséquilibre le mixage, un artefact d'orchestration sans
        rapport avec la structure."""
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            out = degrade_scramble_dynamics(md, random.Random(2))
        drums_before = {n.velocity for n in md.notes if n.channel == 9}
        drums_after = {n.velocity for n in out.notes if n.channel == 9}
        self.assertEqual(drums_before, drums_after)

    def test_pitches_and_rhythm_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            out = degrade_scramble_dynamics(md, random.Random(3))
        self.assertEqual([(n.pitch, n.start, n.end) for n in md.notes],
                         [(n.pitch, n.start, n.end) for n in out.notes])

    def test_actually_destroys_the_arc(self):
        """Le contraste d'intensité entre sections doit s'effondrer — c'est
        la grandeur que mesurent les axes 26 et 28."""
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            out = degrade_scramble_dynamics(md, random.Random(4))

        def spread(m):
            secs = build_score(m).sections
            vels = [s.mean_velocity for s in secs if s.mean_velocity > 0]
            return (max(vels) - min(vels)) if len(vels) >= 2 else 0.0

        self.assertGreater(spread(md), spread(out))

    def test_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            a = degrade_scramble_dynamics(md, random.Random(7))
            b = degrade_scramble_dynamics(md, random.Random(7))
        self.assertEqual([n.velocity for n in a.notes],
                         [n.velocity for n in b.notes])

    def test_original_is_not_mutated(self):
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(self._midi(Path(d)))
            before = [n.velocity for n in md.notes]
            degrade_scramble_dynamics(md, random.Random(9))
        self.assertEqual([n.velocity for n in md.notes], before)

    def test_skipped_when_velocities_are_uniform(self):
        # Sans dispersion, permuter est un no-op : la paire serait vide de sens.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "flat.mid"
            write_midi(path, [[(float(i), 0.9, 60, 80, 0) for i in range(16)]],
                       ppq=480, bpm=100)
            self.assertFalse(_applicable("scramble_dynamics", parse_midi(path)))

    def test_audible_set_excludes_the_untested(self):
        """`flatten_dynamics` est réfutée par l'écoute, `scramble_dynamics`
        n'est pas encore testée : ni l'une ni l'autre ne doit figurer parmi
        les dégradations validées."""
        self.assertNotIn("flatten_dynamics", AUDIBLE_DEGRADATIONS)
        self.assertNotIn("scramble_dynamics", AUDIBLE_DEGRADATIONS)
        self.assertTrue(AUDIBLE_DEGRADATIONS <= set(DEGRADATIONS))


class TestConfidence(unittest.TestCase):
    """La fiabilité doit séparer ce qui est mesuré de ce qui est deviné.
    Validée empiriquement sur 200 fichiers : corrélation r = 0.59 entre la
    confiance annoncée et l'accuracy contrastive réellement obtenue."""

    def _score_from(self, notes, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mid"
            write_midi(path, [notes], ppq=480, bpm=100, **kw)
            return build_score(parse_midi(path))

    def test_drum_loop_is_flagged_uninterpretable(self):
        # Boucle de kick : 4 mesures, canal 9, aucune harmonie ni mélodie.
        notes = [(bar * 4.0 + beat, 0.3, 36, 90, 9)
                 for bar in range(4) for beat in (0.0, 1.5, 2.0, 3.5)]
        sms = SenseOfMusicalStructure(self._score_from(notes))
        sms.calculate()
        self.assertLess(sms.confidence(), 0.35)
        self.assertEqual(sms.confidence_level(), "insuffisante")
        self.assertFalse(sms.is_interpretable())
        self.assertIn("boucle", sms.diagnosis().lower())

    def test_structured_piece_is_confident(self):
        notes = []
        for sec, (root, vel) in enumerate([(60, 55), (65, 100), (60, 62), (69, 96)]):
            for bar in range(8):
                b = (sec * 8 + bar) * 4.0
                for iv in (0, 4, 7):
                    notes.append((b, 3.8, root + iv, vel, 0))
                notes.append((b, 1.8, root - 24, vel, 0))
                for k in range(4):          # mélodie sur la grille
                    notes.append((b + k, 0.9, root + 12 + (k * 2) % 7, vel, 0))
        sms = SenseOfMusicalStructure(self._score_from(notes))
        sms.calculate()
        self.assertGreater(sms.confidence(), 0.7)
        self.assertTrue(sms.is_interpretable())

    def test_confidence_is_reported_per_axis_and_in_dict(self):
        sms = SenseOfMusicalStructure(demo_score())
        for ax in sms.calculate():
            self.assertGreaterEqual(ax.confidence, 0.0, ax.id)
            self.assertLessEqual(ax.confidence, 1.0, ax.id)
        d = sms.to_dict()
        self.assertIn("confidence", d)
        self.assertIn("confidence_level", d)
        self.assertTrue(all("confidence" in a for a in d["axes"]))

    def test_axis29_never_more_confident_than_what_it_sums(self):
        # Une synthèse ne peut pas être plus sûre que ses composantes.
        notes = [(bar * 4.0, 3.8, 60, 80, 0) for bar in range(4)]
        sms = SenseOfMusicalStructure(self._score_from(notes))
        axes = sms.calculate()
        others = [a for a in axes if a.id != "29_global_cohesion"]
        cohesion = next(a for a in axes if a.id == "29_global_cohesion")
        self.assertLessEqual(cohesion.confidence, max(a.confidence for a in others) + 1e-9)

    def test_empty_score_has_zero_confidence(self):
        sms = SenseOfMusicalStructure(Score())
        sms.calculate()
        self.assertEqual(sms.confidence(), 0.0)
        self.assertEqual(sms.confidence_level(), "insuffisante")

    def test_diagnosis_distinguishes_loop_from_bad_segmentation(self):
        """Une boucle et un morceau mal segmenté donnent tous deux « une
        seule section » : le conseil ne doit pas être le même."""
        loop = [(bar * 4.0 + b, 0.3, 36, 90, 9) for bar in range(4) for b in (0.0, 2.0)]
        d_loop = SenseOfMusicalStructure(self._score_from(loop)).diagnosis()
        # Pièce harmoniquement riche mais d'un seul tenant.
        flat = []
        for bar in range(24):
            for iv in (0, 4, 7):
                flat.append((bar * 4.0, 3.8, 60 + (bar % 4) * 2 + iv, 80, 0))
            for k in range(4):
                flat.append((bar * 4.0 + k, 0.9, 72 + (k * 3) % 7, 80, 0))
        d_flat = SenseOfMusicalStructure(self._score_from(flat)).diagnosis()
        self.assertNotEqual(d_loop, d_flat)
        self.assertIn("boucle", d_loop.lower())


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
