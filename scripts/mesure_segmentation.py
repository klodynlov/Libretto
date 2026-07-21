"""Mesure de la segmentation contre vérité terrain.

Usage :
    python3 examples/make_corpus.py /tmp/c7 40 7
    python3 scripts/mesure_segmentation.py /tmp/c7 [/tmp/c11 ...]

Sépare les fichiers AVEC marqueurs (frontières lues, pas détectées — un
simple contrôle de plomberie, attendu parfait) des fichiers SANS (la vraie
mesure). Métriques par corpus : F-mesure des frontières à ±1 mesure,
exactitude du nombre de sections, exactitude de la séquence de forme
(étiquettes canonisées par ordre d'apparition), histogramme des décalages
des frontières appariées (à ±3), ventilation par forme.

Ce harnais existe parce que la campagne F historique (0.55 → 0.73) n'avait
laissé aucun outil rejouable : un chiffre qu'on ne peut pas remesurer ne
protège de rien. Réglage sur une graine, validation sur des graines tenues
à l'écart — la leçon du noyau de Foote, implémenté puis retiré parce qu'il
ne gagnait que sur le corpus qui avait servi à le régler.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.builder import build_score  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402


def canon(labels: list[str]) -> list[str]:
    mapping: dict[str, str] = {}
    out = []
    for lab in labels:
        if lab not in mapping:
            mapping[lab] = chr(ord("A") + len(mapping))
        out.append(mapping[lab])
    return out


def f1_boundaries(got: list[int], truth: list[int], tol: int = 1) -> float:
    """F-mesure appariée : une frontière détectée est juste si une vraie est
    à ±tol mesures ; chaque vraie ne compte qu'une fois."""
    if not got and not truth:
        return 1.0
    if not got or not truth:
        return 0.0
    used: set[int] = set()
    hits = 0
    for g in got:
        best = None
        for i, t in enumerate(truth):
            if i in used or abs(g - t) > tol:
                continue
            if best is None or abs(g - t) < abs(g - truth[best]):
                best = i
        if best is not None:
            used.add(best)
            hits += 1
    prec, rec = hits / len(got), hits / len(truth)
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def measure(corpus: Path) -> None:
    ann = json.loads((corpus / "annotations.json").read_text(encoding="utf-8"))
    agg: dict[bool, dict[str, list]] = {True: defaultdict(list),
                                        False: defaultdict(list)}
    per_form: dict[str, list[float]] = defaultdict(list)
    offsets: Counter = Counter()
    for path in sorted(corpus.glob("*.mid")):
        meta = ann.get(path.name)
        if meta is None:
            continue
        score = build_score(parse_midi(path))
        got = [s.start_bar - 1 for s in score.sections]
        got_l = [s.label for s in score.sections]
        truth = meta["boundaries"]
        wm = bool(meta.get("with_markers"))
        bucket = agg[wm]
        bucket["f1"].append(f1_boundaries(got, truth))
        bucket["n_ok"].append(int(len(got) == len(truth)))
        bucket["form_ok"].append(int(canon(got_l) == canon(meta["roles"])))
        if not wm:
            per_form[meta["form"]].append(bucket["f1"][-1])
            used: set[int] = set()
            for t in truth[1:]:
                best = None
                for gi, g in enumerate(got[1:]):
                    if gi in used or abs(g - t) > 3:
                        continue
                    if best is None or abs(g - t) < abs(got[1:][best] - t):
                        best = gi
                if best is not None:
                    used.add(best)
                    offsets[got[1:][best] - t] += 1

    print(f"── {corpus} ──")
    for wm, name in ((True, "avec marqueurs"), (False, "SANS marqueurs")):
        b = agg[wm]
        if not b["f1"]:
            continue
        n = len(b["f1"])
        print(f"  {name:<15} n={n:<3} F1 {sum(b['f1']) / n:.3f}  "
              f"sections exactes {sum(b['n_ok'])}/{n}  "
              f"formes exactes {sum(b['form_ok'])}/{n}")
    if offsets:
        print(f"  décalages appariés (±3) : {dict(sorted(offsets.items()))}")
    if per_form:
        worst = sorted(per_form.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        print("  par forme : "
              + "  ".join(f"{k} {sum(v) / len(v):.2f}" for k, v in worst))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for arg in sys.argv[1:]:
        measure(Path(arg))
