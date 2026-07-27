"""Tests du harnais de mesure de tonalité — la lecture des étiquettes.

Une vérité terrain lue dans un nom de fichier ne vaut que ce que vaut le
parseur : une seule règle trop large et le taux mesuré ne dit plus rien du
moteur, seulement du bruit qu'on lui a servi. Deux pièges ont réellement
faussé la première mesure et sont fixés ici — `d cymbl.mid` lu comme un ré,
et `KIT_1_PAD_Eb_100BPM.mid` (fondamentale locale) préféré au dossier
`KIT_1_Gmin_100BPM` qui, lui, annonce la tonalité.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from loop_index import label_for, parse_key_label, role_of  # noqa: E402
from scripts.mesure_tonalite import error_kind  # noqa: E402


class TestParseEtiquette(unittest.TestCase):
    def test_formes_reconnues(self):
        for texte, attendu in [
            ("Bonfire_Dm_100_Piano_Chords", (2, "min")),
            ("Lonely_F_m_102_Guitar_Chords", (5, "min")),
            ("CHEMS_108_G_m_Bass", (7, "min")),
            ("KIT_4_Emin _100BPM", (4, "min")),
            ("t2_Cmn_134.23bpm midi", (0, "min")),
            ("G Minor 140.38 bpm", (7, "min")),
            ("Kit 4 Eb 090", (3, None)),
            ("hi lead_100_f#", (6, None)),
            ("Moves_B_95", (11, None)),
            ("Ireti_95_Bm_Guitar_Chords", (11, "min")),
            ("track_C_maj_120", (0, "maj")),
        ]:
            self.assertEqual(parse_key_label(texte), attendu, texte)

    def test_initiales_de_mots_ignorees(self):
        # « Bonfire » n'est pas un si, « Amapiano » n'est pas un la mineur.
        for texte in ("Bonfire", "Amapiano", "Chat To Me", "TB-Trapmaticz",
                      "Afro Haven", "Ignite_105_Strings"):
            self.assertIsNone(parse_key_label(texte), texte)

    def test_tonique_nue_minuscule_rejetee(self):
        # Convention de nommage de batterie, pas une tonalité.
        self.assertIsNone(parse_key_label("d cymbl"))
        self.assertIsNone(parse_key_label("d fall"))
        # …mais une minuscule altérée reste une tonalité.
        self.assertEqual(parse_key_label("lead_100_f#"), (6, None))

    def test_etiquette_contradictoire_refusee(self):
        self.assertIsNone(parse_key_label("Kit_Dm_and_Gm_100"))


class TestPrecedence(unittest.TestCase):
    def test_mode_explicite_bat_tonique_nue(self):
        root = Path("/packs")
        p = root / "KIT_1_Gmin_100BPM" / "MIDI" / "KIT_1_PAD_SOULFUL_Eb_100BPM.mid"
        self.assertEqual(label_for(p, root), (7, "min", "dossier"))

    def test_fichier_prioritaire_a_force_egale(self):
        root = Path("/packs")
        p = root / "Naija" / "MIDI FILES" / "Bonfire_Dm_100_Piano_Chords.mid"
        self.assertEqual(label_for(p, root), (2, "min", "fichier"))

    def test_sans_etiquette(self):
        root = Path("/packs")
        self.assertIsNone(label_for(root / "kits" / "loop_01.mid", root))


class TestRoles(unittest.TestCase):
    def test_percussions_reperees(self):
        for nom in ("Bonfire_Dm_100_Kick_01", "d cymbl", "CHEMS_108_G_m_Conga",
                    "hats_100", "KIT_2_SHAKER"):
            self.assertEqual(role_of(Path(f"{nom}.mid")), "batterie", nom)

    def test_808_reste_tonal(self):
        self.assertEqual(role_of(Path("KIT_4_808_HAPPY_D_100BPM.mid")), "808")


class TestNatureDesErreurs(unittest.TestCase):
    def test_exact_et_parallele(self):
        self.assertEqual(error_kind(2, "min", 2, "min"), "exact")
        self.assertEqual(error_kind(2, "maj", 2, "min"), "parallele")
        # mode non étiqueté : la tonique suffit
        self.assertEqual(error_kind(2, "maj", 2, None), "exact")

    def test_relative(self):
        self.assertEqual(error_kind(5, "maj", 2, "min"), "relative")   # Dm -> F
        self.assertEqual(error_kind(9, "min", 0, "maj"), "relative")   # C -> Am

    def test_dominante_et_sous_dominante(self):
        self.assertEqual(error_kind(7, "maj", 0, "maj"), "dominante")
        self.assertEqual(error_kind(5, "maj", 0, "maj"), "sous-dominante")

    def test_autre(self):
        self.assertEqual(error_kind(1, "maj", 0, "maj"), "autre")


if __name__ == "__main__":
    unittest.main()
