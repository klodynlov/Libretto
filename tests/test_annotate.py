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
    RENDERER,
    build_duel_tasks,
    build_tasks,
    midi_pair,
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

    def test_control_count_is_guaranteed_not_drawn(self):
        """Le tirage fichier par fichier pouvait ne produire qu'un contrôle
        sur un lot entier. Sans eux, le bruit de réponse n'est pas estimable
        et aucun taux de détection n'est interprétable."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=20)
            for seed in (1, 2, 3, 7, 11):
                tasks = build_tasks(corpus, seed=seed, per_file=2)
                n_ctrl = sum(1 for t in tasks if t["degradation"] == CONTROL)
                self.assertGreaterEqual(n_ctrl, 4, f"graine {seed}")

    def test_targeted_session_keeps_its_controls(self):
        """Restreindre les dégradations ne doit pas faire fondre les
        contrôles : c'est justement une session ciblée qui a besoin d'un
        verdict net."""
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=20), seed=2, per_file=2,
                                only=["flatten_dynamics", "scramble_dynamics"])
            kinds = {t["degradation"] for t in tasks}
            self.assertEqual(kinds, {"flatten_dynamics", "scramble_dynamics", CONTROL})
            self.assertGreaterEqual(
                sum(1 for t in tasks if t["degradation"] == CONTROL), 4)

    def test_controls_cover_every_prefix(self):
        """Session 5 : le mélange avait groupé 6 contrôles sur 7 au-delà de
        la 30e tâche — l'annotateur arrêté à mi-lot n'avait qu'un contrôle,
        bruit de réponse inestimable. La répartition doit couvrir tout
        préfixe : l'écart entre contrôles consécutifs (bords compris) reste
        borné par deux tranches."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=24)
            for seed in (1, 2, 5, 7, 11):
                tasks = build_tasks(corpus, seed=seed, per_file=2)
                positions = [i for i, t in enumerate(tasks)
                             if t["degradation"] == CONTROL]
                n_ctrl = len(positions)
                step = len(tasks) / n_ctrl
                bounds = [-1] + positions + [len(tasks)]
                worst = max(b - a for a, b in zip(bounds, bounds[1:]))
                self.assertLessEqual(worst, 2 * step + 1, f"graine {seed}")

    def test_control_positions_vary_between_seeds(self):
        """Des positions de contrôle FIXES (un contrôle tous les k
        exactement) s'apprendraient : l'annotateur saurait répondre sans
        écouter. La tranche est garantie, la position dans la tranche non."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=24)
            seen = set()
            for seed in (1, 2, 5, 7, 11):
                tasks = build_tasks(corpus, seed=seed, per_file=2)
                seen.add(tuple(i for i, t in enumerate(tasks)
                               if t["degradation"] == CONTROL))
            self.assertGreater(len(seen), 1)

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


class TestDuels(unittest.TestCase):
    """Mode duel : deux morceaux DIFFÉRENTS, le « original » étant celui que
    le moteur place devant. Les garanties du protocole doivent tenir à
    l'identique — sans elles, le taux mesuré ne dit rien."""

    def _duels(self, tmp: Path, n: int = 6) -> Path:
        corpus = _corpus(tmp, n=n)
        fichiers = sorted(corpus.glob("*.mid"))
        duels = [{"prefere": str(fichiers[i]), "autre": str(fichiers[i + 1]),
                  "etiquette": "duel_ecart_fort" if i % 2 else "duel_ecart_faible",
                  "nom": f"{fichiers[i].stem}_vs_{fichiers[i + 1].stem}",
                  "ecart": 0.1}
                 for i in range(len(fichiers) - 1)]
        path = tmp / "duels.json"
        path.write_text(json.dumps({"duels": duels}), encoding="utf-8")
        return path

    def test_tache_oppose_deux_fichiers_distincts(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = build_duel_tasks(self._duels(Path(d)), seed=1)
            duel = next(t for t in tasks if t["degradation"] != CONTROL)
            self.assertNotEqual(duel["path"], duel["path_autre"])
            a, b = midi_pair(duel, seed=1)
            self.assertNotEqual([n.pitch for n in a.notes], [n.pitch for n in b.notes])

    def test_controles_presents_et_identiques(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = build_duel_tasks(self._duels(Path(d)), seed=1)
            controles = [t for t in tasks if t["degradation"] == CONTROL]
            self.assertGreaterEqual(len(controles), 4)
            a, b = midi_pair(controles[0], seed=1)
            self.assertEqual([n.pitch for n in a.notes], [n.pitch for n in b.notes])

    def test_position_equilibree_et_deterministe(self):
        with tempfile.TemporaryDirectory() as d:
            duels = self._duels(Path(d), n=20)
            tasks = build_duel_tasks(duels, seed=4)
            part_a = sum(1 for t in tasks if t["original_slot"] == "A") / len(tasks)
            self.assertGreater(part_a, 0.3)
            self.assertLess(part_a, 0.7)
            self.assertEqual([(t["file"], t["original_slot"]) for t in tasks],
                             [(t["file"], t["original_slot"])
                              for t in build_duel_tasks(duels, seed=4)])

    def test_le_client_ignore_qui_est_prefere(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = build_duel_tasks(self._duels(Path(d)), seed=1)
            paquet = render_task(tasks[0], seed=1)
            self.assertEqual(set(paquet), {"id", "n_total", "A", "B"})

    def test_fichier_manquant_ignore_sans_planter(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            path = self._duels(tmp)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["duels"].append({"prefere": str(tmp / "absent.mid"),
                                  "autre": str(tmp / "p0.mid"),
                                  "etiquette": "duel_ecart_fort"})
            path.write_text(json.dumps(data), encoding="utf-8")
            tasks = build_duel_tasks(path, seed=1)
            self.assertTrue(all(Path(t["path"]).exists() for t in tasks))


class TestRendererVersioning(unittest.TestCase):
    """Deux rendus audio sont deux expériences : les sessions 1-2 (v1-volume)
    ont conclu que la dynamique ne s'entend pas, conclusion qui ne vaut que
    pour ce rendu. Un fichier de jugements doit dire quel rendu l'a produit,
    et refuser d'en mélanger deux."""

    def test_renderer_is_recorded_in_judgements(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            store = _Judgements(out, tasks, 1)
            store.record(tasks[0]["id"], "A", 5.0)
            self.assertEqual(json.loads(out.read_text())["renderer"], RENDERER)

    def test_resume_with_other_renderer_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            out.write_text(json.dumps({
                "seed": 1, "n_tasks": 1, "renderer": "v0-imaginaire",
                "judgements": [{"task_id": 0, "file": "x", "degradation": "d",
                                "original_slot": "A", "choice": "A",
                                "picked_original": True, "listened_seconds": 1.0}],
            }))
            with self.assertRaises(ValueError):
                _Judgements(out, tasks, 1)

    def test_legacy_file_without_renderer_is_v1(self):
        """Les fichiers d'avant le versionnage sont v1-volume : les reprendre
        sous v2 mélangerait les rendus, donc refus."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            out.write_text(json.dumps({
                "seed": 1, "n_tasks": 1,
                "judgements": [{"task_id": 0, "file": "x", "degradation": "d",
                                "original_slot": "A", "choice": "A",
                                "picked_original": True, "listened_seconds": 1.0}],
            }))
            with self.assertRaises(ValueError):
                _Judgements(out, tasks, 1)

    def test_empty_legacy_file_is_resumable(self):
        # Aucun jugement encore : rien à mélanger, la reprise est saine.
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            tasks = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            out.write_text(json.dumps({"seed": 1, "n_tasks": 1, "judgements": []}))
            store = _Judgements(out, tasks, 1)
            self.assertEqual(store.records, [])

    def test_page_carries_renderer_placeholder(self):
        # Servi après substitution ; le placeholder doit exister dans la page
        # (bandeau visible par l'annotateur) — une page muette sur son rendu
        # redonnerait des sessions incomparables silencieusement.
        from libretto.annotate import PAGE
        self.assertGreaterEqual(PAGE.count("__RENDERER__"), 2)
        self.assertIn("sawtooth", PAGE)      # le timbre dépend bien d'un filtre
        self.assertIn("v*v", PAGE)           # coupure en v² — la marque du v2


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
            self.assertIn(first["cle"], store2.done_keys())
            self.assertNotEqual(store2.next_task()["id"], first["id"])

    def test_reprise_sur_un_lot_agrandi(self):
        """La reprise suit la comparaison, pas son rang.

        Agrandir un lot décale tous les rangs. Tant que la reprise se faisait
        sur `task_id`, les jugements déjà rendus se recollaient à d'autres
        comparaisons — sans rien casser, et avec des chiffres faux.
        """
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=6)
            petit = build_tasks(corpus, seed=1, per_file=1)
            out = Path(d) / "j.json"
            store = _Judgements(out, petit, 1)
            juge = store.next_task()
            store.record(juge["id"], "A", 9.0)

            grand = build_tasks(corpus, seed=1, per_file=2)      # lot élargi
            self.assertGreater(len(grand), len(petit))
            store2 = _Judgements(out, grand, 1)
            self.assertEqual(store2.done_keys(), {juge["cle"]})
            # la comparaison déjà jugée n'est pas reproposée…
            vues = set()
            while (t := store2.next_task()) is not None and len(vues) < len(grand):
                if t["cle"] in vues:
                    break
                vues.add(t["cle"])
                store2.record(t["id"], "same", 1.0)
            self.assertNotIn(juge["cle"], vues)
            # …et le jugement repris pointe sur la bonne tâche du nouveau lot
            repris = next(r for r in store2.records if r["cle"] == juge["cle"])
            self.assertEqual(repris["task_id"],
                             next(t["id"] for t in grand if t["cle"] == juge["cle"]))

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


class TestInterAnnotator(unittest.TestCase):
    """Accord entre deux annotateurs : jonction sur (fichier, dégradation)
    et sur la VERSION désignée — jamais sur l'id de tâche ni la lettre,
    tous deux remélangés d'un lot à l'autre."""

    @staticmethod
    def _rec(file, deg, slot, choice):
        return {"task_id": 0, "file": file, "degradation": deg,
                "original_slot": slot, "choice": choice,
                "picked_original": (choice == slot
                                    if choice in ("A", "B") else None),
                "listened_seconds": 30.0}

    def _write(self, tmp, name, rows, renderer="v3-instrument:X.sf3"):
        path = Path(tmp) / name
        path.write_text(json.dumps({"renderer": renderer, "judgements": rows}),
                        encoding="utf-8")
        return path

    def test_same_version_through_different_letters(self):
        """Le lot de B a l'original en face de l'autre lettre : deux choix
        de lettres opposés qui désignent la MÊME version doivent compter
        comme un accord parfait."""
        from libretto.agreement import inter_annotator
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.json", [
                self._rec("p0.mid", "flatten_dynamics", "A", "A"),
                self._rec("p1.mid", "scramble_dynamics", "B", "B")])
            b = self._write(d, "b.json", [
                self._rec("p0.mid", "flatten_dynamics", "B", "B"),
                self._rec("p1.mid", "scramble_dynamics", "A", "A")])
            report = inter_annotator(a, b)
        self.assertEqual(report["accord_brut"], 1.0)
        self.assertEqual(report["kappa"], 1.0)
        self.assertEqual(report["n_commun_reels"], 2)

    def test_kappa_zero_when_independent(self):
        """Réponses croisées symétriques : accord brut 50 % mais κ = 0 —
        l'accord observé est exactement celui du hasard des penchants."""
        from libretto.agreement import inter_annotator
        files = [f"p{i}.mid" for i in range(4)]
        a_rows = [self._rec(f, "flatten_dynamics", "A", c)
                  for f, c in zip(files, ["A", "A", "B", "B"])]
        b_rows = [self._rec(f, "flatten_dynamics", "A", c)
                  for f, c in zip(files, ["A", "B", "A", "B"])]
        with tempfile.TemporaryDirectory() as d:
            report = inter_annotator(self._write(d, "a.json", a_rows),
                                     self._write(d, "b.json", b_rows))
        self.assertEqual(report["accord_brut"], 0.5)
        self.assertEqual(report["kappa"], 0.0)

    def test_renderer_mismatch_is_refused(self):
        """Un verdict ne vaut que pour le rendu qui l'a produit : comparer
        un lot v2 à un lot v3 mélangerait deux expériences."""
        from libretto.agreement import inter_annotator
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.json",
                            [self._rec("p0.mid", "flatten_dynamics", "A", "A")],
                            renderer="v2-timbre")
            b = self._write(d, "b.json",
                            [self._rec("p0.mid", "flatten_dynamics", "A", "A")],
                            renderer="v3-instrument:X.sf3")
            with self.assertRaises(ValueError):
                inter_annotator(a, b)

    def test_controls_feed_criterion_not_kappa(self):
        """Les contrôles mesurent le critère de chaque annotateur (taux de
        « aucune différence » sur paires identiques) mais n'entrent pas dans
        l'accord réel : picked_original y est un tirage de lettre sans
        version sous-jacente."""
        from libretto.agreement import inter_annotator
        rows_a = [self._rec("p0.mid", "flatten_dynamics", "A", "A"),
                  self._rec("p1.mid", CONTROL, "A", "same")]
        rows_b = [self._rec("p0.mid", "flatten_dynamics", "A", "A"),
                  self._rec("p1.mid", CONTROL, "B", "A")]
        with tempfile.TemporaryDirectory() as d:
            report = inter_annotator(self._write(d, "a.json", rows_a),
                                     self._write(d, "b.json", rows_b))
        self.assertEqual(report["n_commun_controles"], 1)
        self.assertEqual(report["n_commun_reels"], 1)
        self.assertEqual(report["annotateurs"][0]["controles_aucune"], 1.0)
        self.assertEqual(report["annotateurs"][1]["controles_aucune"], 0.0)
        self.assertNotIn(CONTROL, report["verdict_groupe"])

    def test_pooled_counts_all_judgements(self):
        """Le verdict groupé additionne les jugements des deux annotateurs
        — y compris les paires qu'un seul a jugées."""
        from libretto.agreement import inter_annotator
        rows_a = [self._rec("p0.mid", "flatten_dynamics", "A", "A"),
                  self._rec("p1.mid", "flatten_dynamics", "B", "B")]
        rows_b = [self._rec("p0.mid", "flatten_dynamics", "B", "B")]
        with tempfile.TemporaryDirectory() as d:
            report = inter_annotator(self._write(d, "a.json", rows_a),
                                     self._write(d, "b.json", rows_b))
        pooled = report["verdict_groupe"]["flatten_dynamics"]
        self.assertEqual(pooled["n_decided"], 3)
        self.assertEqual(pooled["taux_original"], 1.0)

    def test_duplicate_key_is_refused(self):
        from libretto.agreement import inter_annotator
        rows = [self._rec("p0.mid", "flatten_dynamics", "A", "A"),
                self._rec("p0.mid", "flatten_dynamics", "B", "B")]
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.json", rows)
            b = self._write(d, "b.json", rows[:1])
            with self.assertRaises(ValueError):
                inter_annotator(a, b)


if __name__ == "__main__":
    unittest.main()
