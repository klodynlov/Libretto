import unittest

from libretto.axes import (KEY_PROFILES, KK_MAJOR, KK_MINOR, MODAL_PROFILES,
                           PARENT_MODE, SenseOfMusicalStructure, estimate_key)
from libretto.demo import demo_score
from libretto.model import Chord, NoteType, Pitch, Score, Section


def _hist(pcs: dict[int, float]) -> list[float]:
    h = [0.0] * 12
    for pc, poids in pcs.items():
        h[pc] = poids
    return h


# gamme complète, tonique appuyée : la matière minimale pour que KK tranche
SOL_MIXOLYDIEN = _hist({7: 3.0, 9: 1.0, 11: 1.0, 0: 1.5, 2: 1.5, 4: 1.0, 5: 1.5})
RE_DORIEN = _hist({2: 3.0, 4: 1.0, 5: 1.5, 7: 1.5, 9: 1.5, 11: 1.0, 0: 1.0})
DO_MAJEUR = _hist({0: 3.0, 2: 1.0, 4: 1.5, 5: 1.0, 7: 2.0, 9: 1.0, 11: 1.0})


class TestProfilsModaux(unittest.TestCase):
    """Les profils modaux sont **optionnels et validés à part** : KK ne
    connaît que majeur et mineur, et lit une pièce mixolydienne à la
    sous-dominante. Ce que ces tests garantissent, c'est le contrat —
    le défaut ne bouge pas, et le vocabulaire élargi trouve bien la tonique
    que le vocabulaire réduit manque."""

    def test_defaut_modal(self):
        # depuis la bascule, le vocabulaire par défaut est le vocabulaire
        # élargi ; KEY_PROFILES reste nommé, témoin des mesures antérieures
        self.assertEqual(set(KEY_PROFILES), {"maj", "min"})
        self.assertEqual(estimate_key(RE_DORIEN)[1], "dorien")
        self.assertIn(estimate_key(RE_DORIEN, KEY_PROFILES)[1], ("maj", "min"))

    def test_dorien_reconnu(self):
        self.assertEqual(estimate_key(RE_DORIEN)[:2], (2, "dorien"))

    def test_mixolydien_reconnu(self):
        self.assertEqual(estimate_key(SOL_MIXOLYDIEN)[:2], (7, "mixolydien"))

    def test_le_profil_juste_correle_mieux(self):
        """Sur un histogramme d'école, KK trouve déjà la bonne tonique et se
        trompe seulement de mode — la confusion de sous-dominante mesurée sur
        le corpus (mixolydien 4/26) vient de pièces où la tonique n'est pas
        aussi appuyée. Ce qui se vérifie ici est en amont : le profil modal
        colle mieux à sa propre gamme que le profil parent, condition
        nécessaire pour qu'il gagne quand la matière est ambiguë."""
        for hist, mode in ((RE_DORIEN, "dorien"), (SOL_MIXOLYDIEN, "mixolydien")):
            sans = estimate_key(hist, KEY_PROFILES)
            avec = estimate_key(hist)
            self.assertEqual(sans[0], avec[0])          # même tonique
            self.assertNotEqual(sans[1], avec[1])       # mode corrigé
            self.assertEqual(avec[1], mode)
            self.assertGreater(avec[2], sans[2])        # et corrélation plus haute

    def test_majeur_franc_reste_majeur(self):
        # le vocabulaire élargi ne doit pas voler les pièces qu'on lisait bien
        self.assertEqual(estimate_key(DO_MAJEUR)[:2], (0, "maj"))

    def test_les_axes_qui_deduisent_une_gamme_passent_par_le_parent(self):
        """Les axes 9 et 14 transforment le mode en gamme. Depuis la bascule,
        ce mode peut valoir « dorien » : sans `PARENT_MODE`, un morceau
        mixolydien serait analysé sur une gamme mineure."""
        harmony = [_chord(NoteType.G), _chord(NoteType.F), _chord(NoteType.C),
                   _chord(NoteType.G)]
        melody = [Pitch(n) for n in (NoteType.G, NoteType.B, NoteType.D, NoteType.F,
                                     NoteType.E, NoteType.D, NoteType.C, NoteType.G)]
        score = Score(sections=[Section("a", 1, 9, "verse", harmony=harmony,
                                        melody_pitches=melody)])
        axes = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}
        self.assertGreater(axes["09_progression_complexity"].score, 0.0)
        self.assertGreater(axes["14_bass_melodic"].score, 0.0)

    def test_profils_derives_de_leur_parent(self):
        # construction assumée : un seul degré échangé, le reste intact
        dorien, mixo = MODAL_PROFILES["dorien"], MODAL_PROFILES["mixolydien"]
        self.assertEqual(sorted(dorien), sorted(KK_MINOR))
        self.assertEqual([i for i in range(12) if dorien[i] != KK_MINOR[i]], [8, 9])
        self.assertEqual(sorted(mixo), sorted(KK_MAJOR))
        self.assertEqual([i for i in range(12) if mixo[i] != KK_MAJOR[i]], [10, 11])

    def test_parent_de_chaque_mode(self):
        # les axes 9 et 14 déduisent une gamme du mode : chaque mode doit
        # savoir de quel parent il relève
        self.assertEqual(set(PARENT_MODE), set(MODAL_PROFILES))
        self.assertTrue(set(PARENT_MODE.values()) <= {"maj", "min"})

    def test_histogramme_vide(self):
        self.assertEqual(estimate_key([0.0] * 12, MODAL_PROFILES), (0, "maj", 0.0, 0.0))


def _chord(note: NoteType, quality: str = "maj") -> Chord:
    ivs = {"maj": [0, 4, 7], "min": [0, 3, 7]}[quality]
    return Chord(Pitch(note, octave=3), ivs, quality)


class TestEngineRobustness(unittest.TestCase):
    def test_empty_score_no_crash(self):
        sms = SenseOfMusicalStructure(Score())
        axes = sms.calculate()
        self.assertEqual(len(axes), 29)
        for ax in axes:
            self.assertGreaterEqual(ax.score, 0.0, ax.id)
            self.assertLessEqual(ax.score, 1.0, ax.id)
        self.assertTrue(0.0 <= sms.get_score() <= 1.0)

    def test_single_section_no_crash(self):
        score = Score(sections=[Section("a", 1, 9, "verse", harmony=[_chord(NoteType.C)])])
        sms = SenseOfMusicalStructure(score)
        sms.calculate()
        self.assertTrue(0.0 <= sms.get_score() <= 1.0)

    def test_axes_unique_ids_and_order(self):
        sms = SenseOfMusicalStructure(demo_score())
        axes = sms.calculate()
        ids = [ax.id for ax in axes]
        self.assertEqual(len(set(ids)), 29)
        self.assertEqual(ids, sorted(ids))  # 01..29 dans l'ordre

    def test_all_scores_bounded_on_demo(self):
        sms = SenseOfMusicalStructure(demo_score())
        for ax in sms.calculate():
            self.assertGreaterEqual(ax.score, 0.0, ax.id)
            self.assertLessEqual(ax.score, 1.0, ax.id)

    def test_demo_score_sane(self):
        # Une pièce pop bien formée doit scorer nettement au-dessus du bruit.
        sms = SenseOfMusicalStructure(demo_score())
        sms.calculate()
        self.assertGreater(sms.get_score(), 0.45)
        self.assertLess(sms.get_score(), 0.95)

    def test_v1_crash_regression_axis17(self):
        # v1 : AttributeError `self.score_sections` — l'axe 17 doit tourner.
        sms = SenseOfMusicalStructure(demo_score())
        axes = {ax.id: ax for ax in sms.calculate()}
        self.assertIn("17_theme_recognition", axes)
        self.assertGreater(axes["17_theme_recognition"].score, 0.0)

    def test_progression_score_bounded_extreme(self):
        # v1 : pente non bornée → score > 1 sur des énergies extrêmes.
        secs = [Section(f"s{i}", i * 4 + 1, i * 4 + 5, lbl, tempo=60 + i * 30)
                for i, lbl in enumerate(["intro", "verse", "chorus", "finale"])]
        sms = SenseOfMusicalStructure(Score(sections=secs))
        ax = {a.id: a for a in sms.calculate()}["05_section_progression"]
        self.assertLessEqual(ax.score, 1.0)


class TestMusicTheory(unittest.TestCase):
    def test_cadence_detected_any_voicing(self):
        # G → C = cadence authentique, quelle que soit l'octave du voicing.
        for g_octave in (2, 3, 4, 5):
            harmony = [Chord(Pitch(NoteType.G, octave=g_octave), [0, 4, 7], "maj"),
                       Chord(Pitch(NoteType.C, octave=4), [0, 4, 7], "maj")]
            score = Score(sections=[Section("a", 1, 5, "verse", harmony=harmony)])
            ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}["10_cadence_presence"]
            self.assertGreater(ax.score, 0.0, f"octave dominante = {g_octave}")
            self.assertEqual(ax.details["cadences"]["authentique"], 1)

    def test_plagal_cadence(self):
        harmony = [_chord(NoteType.F), _chord(NoteType.C)]
        score = Score(sections=[Section("a", 1, 5, "verse", harmony=harmony)])
        ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}["10_cadence_presence"]
        self.assertEqual(ax.details["cadences"]["plagale"], 1)

    def test_key_stability_c_major_progression(self):
        # I-IV-V-I : la v1 (ratio de la fondamentale dominante) scorait bas ;
        # KK doit reconnaître Do majeur.
        harmony = [_chord(NoteType.C), _chord(NoteType.F), _chord(NoteType.G), _chord(NoteType.C)]
        melody = [Pitch(n) for n in (NoteType.C, NoteType.E, NoteType.G, NoteType.E,
                                     NoteType.F, NoteType.D, NoteType.B, NoteType.C)]
        score = Score(sections=[Section("a", 1, 9, "verse", harmony=harmony,
                                        melody_pitches=melody)])
        ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}["08_key_stability"]
        self.assertEqual(ax.details["tonique"], "C")
        self.assertEqual(ax.details["mode"], "maj")
        self.assertGreater(ax.score, 0.5)

    def test_motifs_transposition_invariant(self):
        # Même motif répété transposé : v1 le voyait comme du matériau neuf.
        base = [60, 62, 64, 62]
        melody = []
        for shift in (0, 5, 7, 0, 5, 7):
            from libretto.model import pitch_from_midi
            melody.extend(pitch_from_midi(m + shift) for m in base)
        score = Score(sections=[Section("a", 1, 9, "verse", melody_pitches=melody)])
        ax = {a.id: a for a in SenseOfMusicalStructure(score).calculate()}["16_motivic_development"]
        self.assertGreater(ax.details["ratio_repetition"], 0.4)

    def test_emotional_arc_rewards_arch(self):
        # Arche (montée puis descente) : pénalisée en v1 (pente ≈ 0).
        def sec(i, vel):
            return Section(f"s{i}", i * 8 + 1, i * 8 + 9, "x", tempo=100,
                           mean_velocity=vel, note_density=vel / 10)
        arch = Score(sections=[sec(0, 40), sec(1, 70), sec(2, 100), sec(3, 110),
                               sec(4, 80), sec(5, 45)])
        flat = Score(sections=[sec(i, 70) for i in range(6)])
        ax_arch = {a.id: a for a in SenseOfMusicalStructure(arch).calculate()}["28_emotional_arc"]
        ax_flat = {a.id: a for a in SenseOfMusicalStructure(flat).calculate()}["28_emotional_arc"]
        self.assertGreater(ax_arch.score, 0.6)
        self.assertGreater(ax_arch.score, ax_flat.score)

    def test_axis02_plateau_tolerates_real_forms(self):
        """[4, 6, 15] (deux idées courtes, un long développement) est une
        forme, pas un défaut : elle doit scorer comme un AABA régulier.
        v2 (1 − CV) la faisait perdre contre sa propre version scindée
        [4, 6, 9, 6] — l'axe votait pour la dégradation (AUC 0.39 sur les
        paires dynamiques du corpus réel, session 5)."""
        def score_of(durs):
            secs, start = [], 1
            for i, d in enumerate(durs):
                secs.append(Section(f"s{i}", start, start + d, "x"))
                start += d
            axes = {a.id: a for a in
                    SenseOfMusicalStructure(Score(sections=secs)).calculate()}
            return axes["02_section_balance"].score

        varied = score_of([4, 6, 15])
        split = score_of([4, 6, 9, 6])
        self.assertEqual(varied, 1.0)          # sur le plateau
        self.assertGreaterEqual(varied, split)  # la scission ne gagne plus

    def test_axis02_degenerate_segmentation_falls(self):
        """La dégénérescence — une section qui avale tout, des miettes à
        côté — doit chuter : c'est une segmentation ratée, pas une forme."""
        def score_of(durs):
            secs, start = [], 1
            for i, d in enumerate(durs):
                secs.append(Section(f"s{i}", start, start + d, "x"))
                start += d
            axes = {a.id: a for a in
                    SenseOfMusicalStructure(Score(sections=secs)).calculate()}
            return axes["02_section_balance"].score

        self.assertLess(score_of([1, 1, 30]), 0.5)

    def test_axis02_single_section_is_neutral_not_zero(self):
        """Une seule section : rien à équilibrer. L'ancien 0.0 faisait
        perdre le fichier contre n'importe quelle version scindée — un
        extrait à travers-composé de 50 s perdait d'office contre sa
        propre dégradation (Rachmaninov, session 5)."""
        secs = [Section("s0", 1, 31, "x")]
        axes = {a.id: a for a in
                SenseOfMusicalStructure(Score(sections=secs)).calculate()}
        self.assertEqual(axes["02_section_balance"].score, 0.5)

    def test_axis04_monomaterial_is_not_symmetric(self):
        """`AAAAA` se lit pareil dans les deux sens, mais ce n'est pas une
        symétrie — c'est l'absence de forme. v2 lui donnait palindrome
        parfait + forme fermée : une pièce réduite en bouillie uniforme
        scorait 0.947 contre 0.293 pour la vraie architecture qu'elle
        dégradait (fichier 004 du corpus généré, session axes 03/04/06)."""
        def score_of(lbls):
            secs = [Section(f"s{i}", i * 8 + 1, i * 8 + 9, lbl)
                    for i, lbl in enumerate(lbls)]
            axes = {a.id: a for a in
                    SenseOfMusicalStructure(Score(sections=secs)).calculate()}
            return axes["04_symmetry"].score

        mono = score_of(["chorus"] * 5)
        real = score_of(["chorus", "chorus", "bridge", "outro"])
        symmetric = score_of(["verse", "chorus", "verse"])
        self.assertGreaterEqual(real, mono)      # v2 : 0.293 contre 0.947
        self.assertGreater(symmetric, mono)      # la vraie symétrie domine

    def test_axis04_palindrome_above_chance(self):
        """ABBA est un vrai palindrome (1.0 contre 1/3 de hasard) ; une
        suite sur-segmentée d'étiquettes répétitives n'aligne des paires
        symétriques que par accident — le hasard est soustrait, l'accident
        ne rapporte plus."""
        def score_of(lbls):
            secs = [Section(f"s{i}", i * 8 + 1, i * 8 + 9, lbl)
                    for i, lbl in enumerate(lbls)]
            axes = {a.id: a for a in
                    SenseOfMusicalStructure(Score(sections=secs)).calculate()}
            return axes["04_symmetry"].score

        # même multiset d'étiquettes, arrangements opposés
        self.assertGreater(score_of(["A", "B", "B", "A"]),
                           score_of(["A", "A", "B", "B"]))

    def test_axis24_not_duplicate_of_axis02(self):
        # v1 : axes 2 et 24 = même calcul (CV des durées) → feature comptée
        # double. v2 : durées équilibrées mais SANS carrure (7 mesures) doit
        # séparer les deux axes.
        secs = [Section(f"s{i}", i * 7 + 1, i * 7 + 8, lbl)
                for i, lbl in enumerate(["verse", "chorus", "verse", "chorus"])]
        axes = {a.id: a for a in SenseOfMusicalStructure(Score(sections=secs)).calculate()}
        self.assertGreater(axes["02_section_balance"].score, 0.9)   # durées égales
        self.assertEqual(axes["24_hypermetric_regularity"].score, 0.0)  # pas de carrure


if __name__ == "__main__":
    unittest.main()
