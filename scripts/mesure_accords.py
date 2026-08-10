"""Mesure de la détection d'accords contre vérité terrain.

Usage :
    python3 examples/make_corpus.py /tmp/c7 40 7
    python3 scripts/mesure_accords.py /tmp/c7 [/tmp/c11 ...]

Le générateur écrit un accord vrai par mesure (`chords` dans annotations.json,
classe de fondamentale + qualité maj/min/dim). Ce harnais rejoue `_best_chord`
mesure à mesure et compare : exactitude de la fondamentale, de la qualité, des
deux ; matrice de confusion des qualités et histogramme des erreurs de
fondamentale (en demi-tons). Sans cette vérité on ne pouvait que constater
qu'un accord sortait, pas mesurer s'il était le bon.

La détection est plafonnée par construction — un accord par mesure,
renversements fusionnés, rien au-delà des 7èmes (les 7èmes sont ramenées à
leur triade pour la comparaison, le générateur n'écrivant que des triades).
Ce que ce harnais mesure, c'est l'exactitude *à cette résolution*.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import libretto.builder as builder  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402

# Ramène une qualité détectée (avec 7èmes) à la triade que le générateur écrit.
TRIAD_OF = {"dom7": "maj", "maj7": "maj", "min7": "min"}


def _chords_per_bar(path: Path) -> list:
    """Les accords que `_best_chord` produit, un par mesure, dans l'ordre.

    On intercepte les appels : `build_score` appelle `_best_chord` une fois
    par mesure, en ordre, pour bâtir `chords_by_bar`."""
    calls = []
    original = builder._best_chord

    def spy(weights):
        chord = original(weights)
        calls.append(chord)
        return chord

    builder._best_chord = spy
    try:
        build_score(parse_midi(path))
    finally:
        builder._best_chord = original
    return calls


def measure(corpora: list[str]) -> None:
    root_ok = qual_ok = both_ok = total = 0
    quality_confusion: Counter = Counter()      # (qualité vraie → détectée)
    root_error: Counter = Counter()             # (détectée − vraie) mod 12
    for corpus in corpora:
        ann = json.loads((Path(corpus) / "annotations.json").read_text(
            encoding="utf-8"))
        for path in sorted(Path(corpus).glob("*.mid")):
            meta = ann.get(path.name)
            if meta is None or "chords" not in meta:
                continue
            truth = meta["chords"]
            detected = _chords_per_bar(path)
            for bar in range(min(len(detected), len(truth))):
                chord = detected[bar]
                if chord is None:
                    continue
                true_root, true_qual = truth[bar]
                det_qual = TRIAD_OF.get(chord.quality, chord.quality)
                r = chord.root_pc == true_root
                q = det_qual == true_qual
                total += 1
                root_ok += r
                qual_ok += q
                both_ok += r and q
                quality_confusion[(true_qual, det_qual)] += 1
                root_error[(chord.root_pc - true_root) % 12] += 1

    if not total:
        print("aucune mesure évaluable — le corpus a-t-il le champ 'chords' ?")
        return
    print(f"── {', '.join(corpora)} ──")
    print(f"  mesures évaluées   : {total}")
    print(f"  fondamentale juste : {root_ok}/{total} = {100 * root_ok / total:.1f}%")
    print(f"  qualité juste      : {qual_ok}/{total} = {100 * qual_ok / total:.1f}%")
    print(f"  les deux           : {both_ok}/{total} = {100 * both_ok / total:.1f}%")
    errs = {f"+{k}": v for k, v in sorted(root_error.items()) if k != 0}
    print(f"  erreurs de fondamentale (demi-tons) : {errs}")
    conf = {f"{a}→{b}": v for (a, b), v in quality_confusion.most_common()
            if a != b}
    print(f"  confusions de qualité : {conf}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    measure(sys.argv[1:])
