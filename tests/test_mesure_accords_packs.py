"""Tests du harnais de mesure d'accords sur packs — sans le pack.

Le pack EZKeys est commercial et hors dépôt ; la CI ne peut pas le lire. Ce
qui doit être gardé sans lui, c'est ce que le harnais *interprète* : la
lecture de l'étiquette `<FONDAMENTALE>_<QUALITE>_<HIT|RHY>` dans un nom de
fichier, et le chemin chroma→`_best_chord` sur lequel repose le chiffre. Une
règle de nom trop large, ou une pondération de chroma qui laisse fuir les
percussions, et le 99 % ne mesurerait plus le moteur mais le bruit qu'on lui
sert. On construit donc des accords synthétiques en mémoire — pas de fichier —
et on vérifie que le harnais les retrouve.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import MidiData, MidiNote  # noqa: E402
from scripts.mesure_accords_packs import INVOCAB, NAME, ROOT_PC, chroma_of  # noqa: E402
from libretto.builder import _best_chord  # noqa: E402


def chord_md(pitches, channel=0):
    """Un accord bloc : toutes les notes à t=0, durée 480, vélocité 80."""
    notes = [MidiNote(0, 480, p, 80, channel, 0) for p in pitches]
    return MidiData(ppq=480, notes=notes)


# do fondamentale (classe 0) pour chaque qualité du vocabulaire connu
VOICINGS = {
    "maj": [60, 64, 67],
    "min": [60, 63, 67],
    "maj7": [60, 64, 67, 71],
    "min7": [60, 63, 67, 70],
    "dom7": [60, 64, 67, 70],
}


class TestLectureEtiquette(unittest.TestCase):
    def test_formes_reconnues(self):
        for nom, (root, qual, tag) in [
            ("Gb_MAJ7TH_HIT.mid", ("Gb", "MAJ7TH", "HIT")),
            ("A_MIN7TH_RHY.mid", ("A", "MIN7TH", "RHY")),
            ("Bb_7TH_HIT.mid", ("Bb", "7TH", "HIT")),
            ("C_MAJOR_RHY.mid", ("C", "MAJOR", "RHY")),
            ("F#_SUS4_HIT.mid", ("F#", "SUS4", "HIT")),
        ]:
            m = NAME.match(nom)
            self.assertIsNotNone(m, nom)
            self.assertEqual((m.group(1), m.group(2).upper(), m.group(3).upper()),
                             (root, qual, tag), nom)

    def test_hors_format_rejete(self):
        # des noms réels du pack qui ne suivent PAS la convention accord
        for nom in ("70_Am_WhiteHill.mid", "A__RHY.mid",  # qualité vide
                    "Song_4_Key-C.mid", "randomfile.mid"):
            self.assertIsNone(NAME.match(nom), nom)

    def test_enharmonies_fondamentales(self):
        for tok, pc in [("GB", 6), ("BB", 10), ("C", 0), ("E#", 5), ("CB", 11)]:
            self.assertEqual(ROOT_PC[tok], pc, tok)

    def test_mapping_qualite_dans_le_moteur(self):
        # tout label du vocabulaire connu mappe une qualité que _best_chord peut rendre
        from libretto.builder import CHORD_TEMPLATES
        for qual in INVOCAB.values():
            self.assertIn(qual, CHORD_TEMPLATES, qual)


class TestCheminChroma(unittest.TestCase):
    def test_detecte_chaque_qualite(self):
        for qual, pitches in VOICINGS.items():
            chord = _best_chord(chroma_of(chord_md(pitches)))
            self.assertIsNotNone(chord, qual)
            self.assertEqual(chord.root.pc, 0, qual)      # do
            self.assertEqual(chord.quality, qual, qual)

    def test_percussions_exclues(self):
        # un do majeur propre + du bruit sur le canal 10 : le bruit ne doit
        # pas entrer dans le chroma (sinon les percussions fausseraient tout)
        pure = chroma_of(chord_md(VOICINGS["maj"]))
        noisy_md = chord_md(VOICINGS["maj"])
        noisy_md.notes += [MidiNote(0, 480, p, 120, 9, 1) for p in (36, 38, 42, 46)]
        self.assertEqual(chroma_of(noisy_md), pure)

    def test_accord_vide_rend_none(self):
        self.assertIsNone(_best_chord(chroma_of(MidiData(ppq=480, notes=[]))))


if __name__ == "__main__":
    unittest.main()
