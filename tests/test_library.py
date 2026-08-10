"""
Tests de la bibliothèque cherchable : indexation d'un MIDI, persistance,
analyse d'une requête, filtres durs et classement.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libretto.library import (Entry, Library, analyze_entry, parse_query,
                              search, tonic_to_pc)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SLOOPY = EXAMPLES / "morceau_sloopy.mid"
NAIJA = EXAMPLES / "morceau_naija.mid"


def _entry(path, tonic, mode, bpm, bars, v, e, t, *,
           conf=0.9, level="élevée", score=0.7, descriptors=None,
           tags=None) -> Entry:
    """Entrée synthétique, sans passer par un MIDI (tests de recherche)."""
    return Entry(
        path=path, sha1="x", tonic=tonic, mode=mode, key_source="override",
        key_margin=None, bpm=bpm, bars=bars, global_score=score,
        confidence=conf, confidence_level=level,
        axes={f"{i:02d}_x": 0.5 for i in range(1, 30)},
        emotion={"valence": v, "energy": e, "tension": t, "arc": 0.5,
                 "descriptors": descriptors or [], "rationale": {}},
        tags=tags or [])


class TestTonicParse(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(tonic_to_pc("D"), 2)
        self.assertEqual(tonic_to_pc("F#"), 6)
        self.assertEqual(tonic_to_pc("Bb"), 10)
        self.assertEqual(tonic_to_pc("c"), 0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            tonic_to_pc("H")


class TestQueryParse(unittest.TestCase):
    def test_bars_bpm_key(self):
        q = parse_query("mélancolique 8 mesures ~90 bpm en Dm")
        self.assertEqual(q.bars, 8)
        self.assertEqual(q.bpm, 90.0)
        self.assertEqual((q.tonic, q.mode), (2, "min"))
        self.assertIn("melancolique", q.emotion_words)

    def test_bare_number_is_bpm(self):
        q = parse_query("sombre 120")
        self.assertEqual(q.bpm, 120.0)

    def test_bars_not_taken_as_bpm(self):
        q = parse_query("16 mesures")
        self.assertEqual(q.bars, 16)
        self.assertIsNone(q.bpm)

    def test_tempo_word_sets_bpm_when_no_number(self):
        q = parse_query("quelque chose de lent")
        self.assertIsNotNone(q.bpm)
        self.assertLess(q.bpm, 90)

    def test_number_beats_tempo_word(self):
        q = parse_query("lent 130 bpm")
        self.assertEqual(q.bpm, 130.0)

    def test_bare_letter_not_a_key(self):
        # « a » nu (sans altération ni mode) ne doit pas devenir une tonalité
        q = parse_query("une nappe lumineuse")
        self.assertIsNone(q.tonic)


class TestSearchRanking(unittest.TestCase):
    def setUp(self):
        self.entries = [
            _entry("/dark.mid", 2, "min", 90, 8, 0.20, 0.30, 0.35),   # sombre/lent
            _entry("/bright.mid", 0, "maj", 140, 8, 0.85, 0.80, 0.20),  # joyeux/rapide
            _entry("/tense.mid", 7, "min", 100, 16, 0.30, 0.70, 0.80),  # tendu
        ]

    def test_emotion_ranks_closest_first(self):
        hits = search(self.entries, "mélancolique triste")
        self.assertEqual(Path(hits[0].entry.path).name, "dark.mid")
        self.assertIsNotNone(hits[0].distance)
        # trié par distance croissante
        dists = [h.distance for h in hits]
        self.assertEqual(dists, sorted(dists))

    def test_bpm_filter_excludes(self):
        hits = search(self.entries, "joyeux 140 bpm", bpm_tol=10)
        names = {Path(h.entry.path).name for h in hits}
        self.assertEqual(names, {"bright.mid"})

    def test_key_filter(self):
        hits = search(self.entries, "sombre en Dm")
        names = {Path(h.entry.path).name for h in hits}
        self.assertEqual(names, {"dark.mid"})

    def test_bars_filter(self):
        hits = search(self.entries, "tendu 16 mesures", bars_tol=0)
        names = {Path(h.entry.path).name for h in hits}
        self.assertEqual(names, {"tense.mid"})

    def test_no_emotion_falls_back_to_reliability(self):
        low = _entry("/low.mid", 0, "maj", 120, 8, 0.5, 0.5, 0.5,
                     level="moyenne", score=0.95)
        high = _entry("/high.mid", 0, "maj", 120, 8, 0.5, 0.5, 0.5,
                      level="élevée", score=0.60)
        hits = search([low, high], "120 bpm")
        # fiabilité d'abord : « élevée » passe devant, même moins bien notée
        self.assertEqual(Path(hits[0].entry.path).name, "high.mid")
        self.assertIsNone(hits[0].distance)

    def test_mode_parent_equivalence(self):
        dorien = _entry("/dor.mid", 2, "dorien", 90, 8, 0.4, 0.4, 0.4)
        hits = search([dorien], "sombre en Dm")  # min accepte son dorien parent
        self.assertEqual(len(hits), 1)


class TestIndexAndPersistence(unittest.TestCase):
    @unittest.skipUnless(SLOOPY.exists(), "MIDI d'exemple absent")
    def test_analyze_entry_fields(self):
        e = analyze_entry(SLOOPY)
        self.assertEqual(len(e.axes), 29)
        self.assertEqual(e.mode and e.mode, "min")  # morceau en mineur
        self.assertGreater(e.bars, 1)
        self.assertIsNotNone(e.bpm)
        self.assertIn("valence", e.emotion)
        self.assertTrue(e.emotion["descriptors"])
        self.assertEqual(e.key_source, "estimé")

    @unittest.skipUnless(SLOOPY.exists(), "MIDI d'exemple absent")
    def test_override_metadata(self):
        e = analyze_entry(SLOOPY, tonic=5, mode="maj", bpm=128, bars=12,
                          tags=["forge"])
        self.assertEqual((e.tonic, e.mode, e.bpm, e.bars), (5, "maj", 128, 12))
        self.assertEqual(e.key_source, "override")
        self.assertIsNone(e.key_margin)
        self.assertIn("forge", e.tags)

    @unittest.skipUnless(SLOOPY.exists() and NAIJA.exists(), "MIDI d'exemple absent")
    def test_roundtrip_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            libpath = Path(d) / "lib.json"
            lib = Library()
            self.assertTrue(lib.add(analyze_entry(SLOOPY, tags=["a"])))
            self.assertTrue(lib.add(analyze_entry(NAIJA)))
            # ré-ajout du même chemin : mise à jour, pas doublon ; tag préservé
            self.assertFalse(lib.add(analyze_entry(SLOOPY, tags=["b"])))
            self.assertEqual(len(lib.entries), 2)
            lib.save(libpath)

            reloaded = Library.load(libpath)
            self.assertEqual(len(reloaded.entries), 2)
            sloopy = next(e for e in reloaded.entries
                          if e.path.endswith("morceau_sloopy.mid"))
            self.assertEqual(set(sloopy.tags), {"a", "b"})

    def test_load_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Library.load(Path(d) / "nope.json").entries, [])


@unittest.skipUnless(SLOOPY.exists() and NAIJA.exists(), "MIDI d'exemple absent")
class TestSearchToReaper(unittest.TestCase):
    """`library search --reaper` : envoyer le résultat choisi dans REAPER
    passe par le même `push_mididata` que la commande `reaper`. On le remplace
    par un espion pour vérifier le fichier poussé, sans REAPER lancé."""

    def _lib(self, d):
        libpath = Path(d) / "lib.json"
        lib = Library()
        lib.add(analyze_entry(SLOOPY))
        lib.add(analyze_entry(NAIJA))
        lib.save(libpath)
        return libpath

    def test_reaper_pushes_top_pick(self):
        from unittest import mock

        import libretto.reaper as reaper
        with tempfile.TemporaryDirectory() as d:
            libpath = self._lib(d)
            spy = mock.MagicMock(return_value={
                "reaper": "test", "tracks": [{"track": "x", "notes": 3}],
                "markers": 0, "playing": True, "total_notes": 3})
            with mock.patch.object(reaper, "push_mididata", spy):
                from libretto.cli import main as cli_main
                rc = cli_main(["library", "search", "mélancolique",
                               "--lib", str(libpath), "--reaper"])
            self.assertEqual(rc, 0)
            spy.assert_called_once()
            # le meilleur résultat, avec lecture, une piste nommée par l'intention
            _args, kwargs = spy.call_args
            self.assertTrue(kwargs["play"])
            self.assertEqual(len(kwargs["track_names"]), 1)

    def test_reaper_no_play(self):
        from unittest import mock

        import libretto.reaper as reaper
        with tempfile.TemporaryDirectory() as d:
            libpath = self._lib(d)
            spy = mock.MagicMock(return_value={
                "reaper": "t", "tracks": [], "markers": 0,
                "playing": False, "total_notes": 0})
            with mock.patch.object(reaper, "push_mididata", spy):
                from libretto.cli import main as cli_main
                rc = cli_main(["library", "search", "mélancolique",
                               "--lib", str(libpath), "--reaper", "--no-play"])
            self.assertEqual(rc, 0)
            self.assertFalse(spy.call_args.kwargs["play"])

    def test_reaper_pick_selects_second(self):
        from unittest import mock

        import libretto.reaper as reaper
        with tempfile.TemporaryDirectory() as d:
            libpath = self._lib(d)
            captured = {}

            def _cap(md, **kw):
                captured["notes"] = len(md.notes)
                return {"reaper": "t", "tracks": [], "markers": 0,
                        "playing": True, "total_notes": len(md.notes)}

            with mock.patch.object(reaper, "push_mididata", _cap):
                from libretto.cli import main as cli_main
                rc = cli_main(["library", "search", "mélancolique",
                               "--lib", str(libpath), "--reaper", "--pick", "2"])
            self.assertEqual(rc, 0)
            self.assertGreater(captured["notes"], 0)

    def test_reaper_pick_out_of_range(self):
        from unittest import mock

        import libretto.reaper as reaper
        with tempfile.TemporaryDirectory() as d:
            libpath = self._lib(d)
            spy = mock.MagicMock()
            with mock.patch.object(reaper, "push_mididata", spy):
                from libretto.cli import main as cli_main
                rc = cli_main(["library", "search", "mélancolique",
                               "--lib", str(libpath), "--reaper", "--pick", "99"])
            self.assertEqual(rc, 1)
            spy.assert_not_called()

    def test_reaper_bridge_absent_fails_cleanly(self):
        from unittest import mock

        import libretto.reaper as reaper
        with tempfile.TemporaryDirectory() as d:
            libpath = self._lib(d)

            def _boom(*a, **k):
                raise reaper.BridgeError("pont injoignable")

            with mock.patch.object(reaper, "push_mididata", _boom):
                from libretto.cli import main as cli_main
                rc = cli_main(["library", "search", "mélancolique",
                               "--lib", str(libpath), "--reaper"])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
