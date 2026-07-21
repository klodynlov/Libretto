import unittest

from libretto.axes import SenseOfMusicalStructure
from libretto.demo import demo_score
from libretto.model import Chord, NoteType, Pitch, Score, Section


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
