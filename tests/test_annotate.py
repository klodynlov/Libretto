"""
Tests du protocole d'annotation humaine.

L'enjeu n'est pas cosmétique : un protocole d'écoute comparée qui fuite la
réponse, ou dont le dépouillement ne distingue pas un annotateur attentif
d'un annotateur au hasard, produit des chiffres qui ont l'air d'une
validation externe sans en être une. Ces tests portent donc sur les
propriétés qui rendent les jugements exploitables.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from libretto.agreement import CONTROL, analyse, format_report
from libretto.annotate import (
    build_tasks,
    notes_in_seconds,
    render_task,
    _Judgements,
)
from libretto.midi import parse_midi, write_midi


def _corpus(tmp: Path, n: int = 4) -> Path:
    for i in range(n):
        notes = []
        for bar in range(12):
            root = 60 + (bar % 4) * 2 + i
            for iv in (0, 4, 7):
                notes.append((bar * 4.0, 3.6, root + iv, 70 + bar, 0))
            notes.append((bar * 4.0, 1.8, root - 24, 80, 0))
            notes.append((bar * 4.0 + 2.0, 0.4, 38, 90, 9))     # percussion
        write_midi(tmp / f"p{i}.mid", [notes], ppq=480, bpm=100)
    return tmp


class TestTaskPreparation(unittest.TestCase):
    def test_tasks_are_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d))
            a = build_tasks(corpus, seed=3, per_file=2)
            b = build_tasks(corpus, seed=3, per_file=2)
            self.assertEqual([(t["file"], t["degradation"], t["original_slot"])
                              for t in a],
                             [(t["file"], t["degradation"], t["original_slot"])
                              for t in b])

    def test_original_slot_is_balanced(self):
        """Si l'original tombait toujours du même côté, un annotateur
        finirait par le repérer sans rien entendre."""
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=30), seed=5, per_file=2)
            share_a = sum(1 for t in tasks if t["original_slot"] == "A") / len(tasks)
            self.assertGreater(share_a, 0.3)
            self.assertLess(share_a, 0.7)

    def test_degradations_are_evenly_spread(self):
        """Un tirage indépendant par fichier produit des lots où une
        dégradation sort onze fois et une autre trois : on ne peut alors rien
        conclure sur les moins représentées."""
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=25), seed=1, per_file=1)
            counts: dict[str, int] = {}
            for t in tasks:
                if t["degradation"] != CONTROL:
                    counts[t["degradation"]] = counts.get(t["degradation"], 0) + 1
            self.assertGreaterEqual(len(counts), 4)
            # écart maximal d'une unité entre la plus et la moins servie
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1,
                                 counts)

    def test_control_pairs_are_included(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=40), seed=5, per_file=1)
            self.assertTrue(any(t["degradation"] == CONTROL for t in tasks))

    def test_control_pair_is_truly_identical(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            task = {"file": "p0.mid", "path": str(corpus / "p0.mid"),
                    "degradation": CONTROL, "id": 0, "original_slot": "A"}
            payload = render_task(task)
            self.assertEqual(payload["A"], payload["B"])

    def test_rendered_task_never_leaks_the_answer(self):
        """Le client ne doit recevoir ni le nom de la dégradation, ni la
        position de l'original, ni le chemin du fichier."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=2)
            for task in build_tasks(corpus, seed=1, per_file=2):
                payload = render_task(task)
                for leak in ("degradation", "original_slot", "path", "file"):
                    self.assertNotIn(leak, payload, leak)
                self.assertTrue(payload["A"] and payload["B"])

    def test_notes_in_seconds_follows_tempo(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.mid"
            # 4 noires à 60 bpm : la 4e commence à 3.0 s pile.
            write_midi(path, [[(float(i), 0.9, 60, 80, 0) for i in range(4)]],
                       ppq=480, bpm=60)
            notes = notes_in_seconds(parse_midi(path))
        self.assertEqual(len(notes), 4)
        self.assertAlmostEqual(notes[3][0], 3.0, places=2)

    def test_drums_are_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            notes = notes_in_seconds(parse_midi(corpus / "p0.mid"))
        self.assertTrue(any(n[4] == 1 for n in notes), "percussions non marquées")
        self.assertTrue(any(n[4] == 0 for n in notes))


class TestJudgementStore(unittest.TestCase):
    def test_records_and_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=2)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            store = _Judgements(out, tasks, 1)
            first = store.next_task()
            store.record(first["id"], "A", 12.0)
            # une session interrompue reprend où elle s'est arrêtée
            store2 = _Judgements(out, tasks, 1)
            self.assertIn(first["id"], store2.done_ids())
            self.assertNotEqual(store2.next_task()["id"], first["id"])

    def test_picked_original_is_derived_not_asked(self):
        """Le client envoie « A » ou « B » ; c'est le serveur, seul à savoir
        où était l'original, qui en déduit la bonne réponse."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            store = _Judgements(out, tasks, 1)
            task = tasks[0]
            store.record(task["id"], task["original_slot"], 5.0)
            rec = json.loads(out.read_text())["judgements"][0]
            self.assertTrue(rec["picked_original"])

    def test_same_answer_is_not_a_hit(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            store = _Judgements(out, tasks, 1)
            store.record(tasks[0]["id"], "same", 8.0)
            rec = json.loads(out.read_text())["judgements"][0]
            self.assertIsNone(rec["picked_original"])


def _judgements(rows: list[tuple[str, bool | None]]) -> dict:
    """(dégradation, a-t-il désigné l'original) → structure de jugements."""
    out = []
    for i, (deg, picked) in enumerate(rows):
        choice = "same" if picked is None else ("A" if picked else "B")
        out.append({"task_id": i, "file": "x.mid", "degradation": deg,
                    "original_slot": "A", "choice": choice,
                    "picked_original": picked, "listened_seconds": 10.0})
    return {"seed": 1, "n_tasks": len(rows), "judgements": out}


class TestAgreementAnalysis(unittest.TestCase):
    def _analyse(self, payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            return analyse(p)

    def test_attentive_annotator_is_conclusive(self):
        rows = [("shuffle_bars", True)] * 34 + [("shuffle_bars", False)] * 6
        rows += [(CONTROL, None)] * 8
        r = self._analyse(_judgements(rows))
        self.assertGreater(r["taux_original_global"], 0.8)
        self.assertGreater(r["ic95_global"][0], 0.5)      # exclut le hasard
        self.assertEqual(r["control_answered_same"], 1.0)
        self.assertEqual(r["warnings"], [])
        self.assertTrue(r["detection_par_degradation"]["shuffle_bars"]["audible"])

    def test_random_annotator_is_not_conclusive(self):
        rows = [("shuffle_bars", i % 2 == 0) for i in range(40)]
        rows += [(CONTROL, True)] * 8          # tranche des paires identiques
        r = self._analyse(_judgements(rows))
        self.assertLessEqual(r["ic95_global"][0], 0.5)    # le hasard reste possible
        self.assertFalse(r["detection_par_degradation"]["shuffle_bars"]["audible"])
        self.assertTrue(any("contrôle" in w for w in r["warnings"]),
                        "l'incohérence sur les contrôles doit être signalée")

    def test_small_sample_is_flagged(self):
        r = self._analyse(_judgements([("jitter_onsets", True)] * 5))
        self.assertTrue(any("trop larges" in w for w in r["warnings"]))

    def test_missing_controls_are_flagged(self):
        r = self._analyse(_judgements([("jitter_onsets", True)] * 30))
        self.assertTrue(any("aucune paire de contrôle" in w for w in r["warnings"]))

    def test_degradations_are_reported_separately(self):
        """Une dégradation peut s'entendre et une autre pas : le verdict doit
        être rendu par dégradation, pas seulement en bloc."""
        rows = ([("shuffle_bars", True)] * 30 +
                [("flatten_dynamics", i % 2 == 0) for i in range(30)] +
                [(CONTROL, None)] * 6)
        r = self._analyse(_judgements(rows))
        det = r["detection_par_degradation"]
        self.assertTrue(det["shuffle_bars"]["audible"])
        self.assertFalse(det["flatten_dynamics"]["audible"])

    def test_report_is_renderable(self):
        rows = [("shuffle_bars", True)] * 25 + [(CONTROL, None)] * 5
        text = format_report(self._analyse(_judgements(rows)))
        self.assertIn("ACCORD HUMAIN", text)
        self.assertIn("shuffle_bars", text)

    def test_empty_file_is_rejected(self):
        with self.assertRaises(ValueError):
            self._analyse({"judgements": []})


if __name__ == "__main__":
    unittest.main()
