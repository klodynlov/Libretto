"""Tests de l'index de pack — tempo, pupitre, famille.

L'index est la seule chose que `forge_loops` sait d'un pack : une boucle
rangée dans le mauvais pupitre joue au mauvais moment dans le mauvais
registre, et une famille mal découpée mélange deux tonalités dans le même
morceau. Rien de tout ça ne casse bruyamment — d'où ces tests.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.midi import write_midi  # noqa: E402
from loop_index import _pupitre, familles, index_pack, parse_bpm  # noqa: E402


def ecrire_loop(path: Path, pitches, channel=0, bars=2, accords=False):
    """Boucle minimale : une note (ou un accord) par temps."""
    notes = []
    for beat in range(bars * 4):
        base = pitches[beat % len(pitches)]
        hauteurs = [base, base + 4, base + 7] if accords else [base]
        for p in hauteurs:
            notes.append((float(beat), 0.9, p, 90, channel))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_midi(path, [notes], ppq=480, bpm=100.0)


class TestTempo(unittest.TestCase):
    def test_formes_reconnues(self):
        for texte, attendu in [
            ("Bonfire_Dm_100_Piano_Chords", 100.0),
            ("SOSO_02_96bpm_MIDI_Hat", 96.0),        # le « 02 » est une prise
            ("t2_Cmn_134.23bpm midi", 134.23),
            ("Kit 4 Eb 090", 90.0),
            ("KIT_1_BASS_Bb_100BPM", 100.0),
        ]:
            self.assertEqual(parse_bpm(texte), attendu, texte)

    def test_hors_bornes_et_absents(self):
        for texte in ("loop_01", "take_12", "melodie_2000", "Chat To Me"):
            self.assertIsNone(parse_bpm(texte), texte)


class TestPupitre(unittest.TestCase):
    def test_le_nom_fait_foi(self):
        self.assertEqual(_pupitre("bass", 1.0, 40, False), "basse")
        self.assertEqual(_pupitre("808", 1.0, 30, False), "basse")
        self.assertEqual(_pupitre("chord", 1.0, 60, False), "harmonie")
        self.assertEqual(_pupitre("lead", 3.0, 72, False), "melodie")

    def test_le_contenu_tranche_quand_le_nom_se_tait(self):
        # polyphonie franche -> accompagnement ; monodie grave -> basse
        self.assertEqual(_pupitre("?", 3.0, 60, False), "harmonie")
        self.assertEqual(_pupitre("?", 1.0, 40, False), "basse")
        self.assertEqual(_pupitre("?", 1.0, 74, False), "melodie")

    def test_percussion_prioritaire(self):
        # canal 9 : même un nom tonal ne rattrape pas une piste de batterie
        self.assertEqual(_pupitre("piano", 2.0, 60, True), "batterie")


class TestIndex(unittest.TestCase):
    def test_familles_et_etiquettes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ecrire_loop(root / "Bonfire_Am_100_Chords.mid", [57, 60, 64], accords=True)
            ecrire_loop(root / "Bonfire_Am_100_Bass.mid", [33, 35, 40])
            ecrire_loop(root / "Bonfire_Am_100_Kick.mid", [36], channel=9)
            ecrire_loop(root / "Moody_Dm_90_Chords.mid", [50, 53, 57], accords=True)
            loops, stats = index_pack(root)

            self.assertEqual(stats["total"], 4)
            par_nom = {lp.nom: lp for lp in loops}

            self.assertEqual(par_nom["Bonfire_Am_100_Chords"].pupitre, "harmonie")
            self.assertEqual(par_nom["Bonfire_Am_100_Bass"].pupitre, "basse")
            self.assertEqual(par_nom["Bonfire_Am_100_Kick"].pupitre, "batterie")

            # tonalité : étiquette du nom, jamais estimée quand elle est là
            chords = par_nom["Bonfire_Am_100_Chords"]
            self.assertEqual((chords.tonique, chords.mode), (9, "min"))
            self.assertEqual(chords.source_tonalite, "fichier")
            self.assertEqual(chords.bpm, 100.0)

            # une percussion ne porte pas de tonalité, même étiquetée
            self.assertIsNone(par_nom["Bonfire_Am_100_Kick"].tonique)

            # deux chansons du même dossier = deux familles
            fam = familles(loops)
            self.assertEqual(par_nom["Bonfire_Am_100_Chords"].famille,
                             par_nom["Bonfire_Am_100_Bass"].famille)
            self.assertNotEqual(par_nom["Bonfire_Am_100_Chords"].famille,
                                par_nom["Moody_Dm_90_Chords"].famille)
            self.assertEqual(len(fam), 3)   # Bonfire tonal, Bonfire batterie, Moody

    def test_ambitus_et_mediane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ecrire_loop(root / "Test_Cm_100_Lead.mid", [72, 76, 79])
            lp = index_pack(root)[0][0]
            self.assertEqual(lp.ambitus, [72, 79])
            self.assertTrue(72 <= lp.hauteur_mediane <= 79)


if __name__ == "__main__":
    unittest.main()
