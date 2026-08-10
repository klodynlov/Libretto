"""
Tests du pont Forge → bibliothèque : verser un `forge_report.json` dans
l'index, en imposant la tonalité/mode/tempo/mesures connus de Forge, et en
dédoublonnant le gagnant de sa propre shortlist.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.library import Library  # noqa: E402
from forge_library import _clean_meta, _collect, ingest_forge_output  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MIDI_A = EXAMPLES / "morceau_sloopy.mid"
MIDI_B = EXAMPLES / "morceau_naija.mid"


def _fake_forge_output(base: Path) -> None:
    """Fabrique une sortie Forge minimale : gagnant + shortlist (dont le
    gagnant en position 1) + un rapport qui porte les métadonnées voulues."""
    (base / "forge_winner.mid").write_bytes(MIDI_A.read_bytes())
    (base / "forge_short_01.mid").write_bytes(MIDI_A.read_bytes())   # = gagnant
    (base / "forge_short_02.mid").write_bytes(MIDI_B.read_bytes())
    report = {
        "seed": 3,
        "winner": {"index": 7, "file": "candidate_007.mid",
                   "tonic": 5, "mode": "min", "bpm": 92, "bars": 8},
        "winner_file": "forge_winner.mid",
        "leaderboard": [
            {"index": 7, "file": "candidate_007.mid", "tonic": 5,
             "mode": "min", "bpm": 92, "bars": 8},
        ],
        "shortlist": {"k": 2, "picks": [
            {"index": 7, "file": "candidate_007.mid",
             "file_out": "forge_short_01.mid", "tonic": 5, "mode": "min",
             "bpm": 92, "bars": 8},
            {"index": 11, "file": "candidate_011.mid",
             "file_out": "forge_short_02.mid", "tonic": 2, "mode": "min",
             "bpm": 100, "bars": 8},
        ]},
    }
    (base / "forge_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")


@unittest.skipUnless(MIDI_A.exists() and MIDI_B.exists(), "MIDI d'exemple absent")
class TestForgeLibrary(unittest.TestCase):
    def test_collect_dedups_winner(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _fake_forge_output(base)
            report = json.loads((base / "forge_report.json").read_text())
            items = _collect(report, base, include_all=False)
            roles = [role for _p, _m, role in items]
            # gagnant + une seule entrée de shortlist (l'autre = gagnant, écartée)
            self.assertEqual(roles, ["winner", "shortlist"])

    def test_ingest_imposes_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _fake_forge_output(base)
            libpath = base / "lib.json"
            res = ingest_forge_output(base, libpath, tags=["run3"])
            self.assertEqual(res["added"], 2)
            self.assertEqual(res["updated"], 0)

            lib = Library.load(libpath)
            winner = next(e for e in lib.entries
                          if e.path.endswith("forge_winner.mid"))
            # métadonnées IMPOSÉES par Forge, pas estimées
            self.assertEqual((winner.tonic, winner.mode, winner.bpm, winner.bars),
                             (5, "min", 92, 8))
            self.assertEqual(winner.key_source, "override")
            self.assertIsNone(winner.key_margin)
            self.assertIn("forge", winner.tags)
            self.assertIn("forge:winner", winner.tags)
            self.assertIn("run3", winner.tags)

    def test_report_path_accepted_directly(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _fake_forge_output(base)
            libpath = base / "lib.json"
            res = ingest_forge_output(base / "forge_report.json", libpath)
            self.assertEqual(res["n_items"], 2)

    def test_missing_report_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                ingest_forge_output(Path(d), Path(d) / "lib.json")

    def test_reingest_updates_not_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            _fake_forge_output(base)
            libpath = base / "lib.json"
            ingest_forge_output(base, libpath)
            res = ingest_forge_output(base, libpath)   # deuxième passe
            self.assertEqual(res["added"], 0)
            self.assertEqual(res["updated"], 2)
            self.assertEqual(len(Library.load(libpath).entries), 2)


class TestCleanMeta(unittest.TestCase):
    """Les sentinelles d'un rapport Forge (« — », None) ne doivent pas être
    imposées comme métadonnées : un générateur appris ne déclare ni tonalité
    ni longueur, `analyze_entry` doit alors estimer, pas recevoir « — »."""

    def test_sentinels_dropped(self):
        clean = _clean_meta({"tonic": None, "mode": "—", "bpm": 168, "bars": None})
        self.assertEqual(clean, {"tonic": None, "mode": None, "bpm": 168, "bars": None})

    def test_real_values_kept(self):
        clean = _clean_meta({"tonic": 5, "mode": "min", "bpm": 92, "bars": 8})
        self.assertEqual(clean, {"tonic": 5, "mode": "min", "bpm": 92, "bars": 8})

    def test_out_of_domain_rejected(self):
        clean = _clean_meta({"tonic": 42, "mode": "lydien", "bpm": -3, "bars": 0})
        self.assertEqual(clean, {"tonic": None, "mode": None, "bpm": None, "bars": None})


if __name__ == "__main__":
    unittest.main()
