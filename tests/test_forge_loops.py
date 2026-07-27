"""Tests de Forge sur loops — ce qui doit tenir dans l'arrangement.

Un arrangement raté ne lève pas d'exception : il sort un MIDI qui joue.
Ce qui est vérifié ici, c'est ce qui se voit seulement en relisant le
fichier — les percussions sur le canal 9 (sinon Libretto les compte comme
des hauteurs et l'analyse harmonique est fausse), les registres qui ne se
recouvrent pas, les marqueurs de section (sans eux la segmentation est
devinée), et le déterminisme : à graine égale, même verdict.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.midi import parse_midi, write_midi  # noqa: E402
from forge_loops import (REGISTRES, familles_arrangeables,  # noqa: E402
                         forge_loops, octave_de_registre, tirer_plan)
from loop_index import index_pack  # noqa: E402


def ecrire_loop(path: Path, pitches, channel=0, bars=2, accords=False, vel=90):
    notes = []
    for beat in range(bars * 4):
        base = pitches[beat % len(pitches)]
        hauteurs = [base, base + 3, base + 7] if accords else [base]
        for p in hauteurs:
            notes.append((float(beat), 0.9, p, vel, channel))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_midi(path, [notes], ppq=480, bpm=100.0)


def pack_fixture(root: Path) -> None:
    """Un pack minuscule mais complet : quatre pupitres, une seule famille."""
    d = root / "FixturePack" / "MIDI"
    ecrire_loop(d / "Sunrise_Am_100_Chords.mid", [57, 60, 62], accords=True)
    ecrire_loop(d / "Sunrise_Am_100_Piano.mid", [45, 48, 52], accords=True)
    ecrire_loop(d / "Sunrise_Am_100_Bass.mid", [33, 36, 40])
    ecrire_loop(d / "Sunrise_Am_100_Lead.mid", [69, 72, 76, 74])
    ecrire_loop(d / "Sunrise_Am_100_Kick.mid", [36], channel=9)
    ecrire_loop(d / "Sunrise_Am_100_Hats.mid", [42], channel=9)


class TestMatiere(unittest.TestCase):
    def test_famille_arrangeable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            loops, _ = index_pack(root)
            arr = familles_arrangeables(loops)
            self.assertEqual(len(arr), 1)
            pupitres = {lp.pupitre for lp in next(iter(arr.values()))}
            self.assertTrue({"harmonie", "basse", "melodie"} <= pupitres)

    def test_famille_pauvre_ecartee(self):
        # deux pads et rien d'autre : un seul pupitre tonal, pas un morceau
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ecrire_loop(root / "Solo_Am_100_Pad.mid", [57, 60], accords=True)
            ecrire_loop(root / "Solo_Am_100_Pad2.mid", [57, 62], accords=True)
            loops, _ = index_pack(root)
            self.assertEqual(familles_arrangeables(loops), {})

    def test_octave_de_registre(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            loops, _ = index_pack(root)
            for lp in loops:
                if lp.pupitre == "batterie":
                    self.assertEqual(octave_de_registre(lp), 0)
                    continue
                vise = lp.hauteur_mediane + octave_de_registre(lp)
                bas, haut = REGISTRES[lp.pupitre]
                # à une octave près de la bande : le décalage est un multiple
                # de 12, il ne peut pas viser le centre exactement
                self.assertTrue(bas - 12 <= vise <= haut + 12,
                                f"{lp.nom} {lp.pupitre} → {vise} hors de {bas}-{haut}")


class TestArrangement(unittest.TestCase):
    def test_rendu_complet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            out = root / "sortie"
            report = forge_loops(root, out, n=4, seed=1, keep_all=True)

            self.assertEqual(report["n_generated"], 4)
            self.assertIsNotNone(report["winner"])

            md = parse_midi(out / "forge_winner.mid")
            self.assertTrue(md.notes)
            # marqueurs de section : sans eux, la segmentation est devinée
            self.assertGreaterEqual(len(md.markers), 3)
            # percussions sur le canal 9, et rien d'autre dessus
            perc = [n for n in md.notes if n.channel == 9]
            self.assertTrue(perc)
            self.assertTrue(all(n.pitch in (36, 42) for n in perc))
            # la basse reste sous la mélodie
            graves = [n.pitch for n in md.notes if n.channel != 9]
            self.assertLess(min(graves), 55)
            self.assertGreater(max(graves), 65)

    def test_deterministe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            a = forge_loops(root, root / "a", n=3, seed=7)
            b = forge_loops(root, root / "b", n=3, seed=7)
            self.assertEqual([c["score"] for c in a["leaderboard"]],
                             [c["score"] for c in b["leaderboard"]])
            self.assertEqual(a["winner"]["form"], b["winner"]["form"])

    def test_graines_differentes_donnent_des_plans_differents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            loops, _ = index_pack(root)
            arr = familles_arrangeables(loops)
            import random
            plans = [tirer_plan(loops, arr, random.Random(f"t:{i}")) for i in range(12)]
            self.assertGreater(len({p.forme for p in plans}), 1)

    def test_index_partage_non_contamine(self):
        """Un emprunt transposé ne doit pas déteindre sur les candidats
        suivants : la transposition vit dans le plan, pas dans l'index."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_fixture(root)
            # seconde famille, autre tonalité : rend l'emprunt possible
            d = root / "FixturePack" / "MIDI"
            ecrire_loop(d / "Dusk_Dm_100_Chords.mid", [50, 53, 57], accords=True)
            ecrire_loop(d / "Dusk_Dm_100_Lead.mid", [74, 77, 81])
            loops, _ = index_pack(root)
            arr = familles_arrangeables(loops)
            import random
            for i in range(20):
                tirer_plan(loops, arr, random.Random(f"emprunt:{i}"))
            self.assertTrue(all(not hasattr(lp, "transposition") for lp in loops))


if __name__ == "__main__":
    unittest.main()
