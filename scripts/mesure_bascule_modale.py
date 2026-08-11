"""Mesure d'une règle de bascule pour le vocabulaire modal — un résultat négatif.

Usage :
    python3 scripts/mesure_bascule_modale.py ["~/…/Toontrack - EZKeys MIDI Loops"]

Le vocabulaire modal par défaut (`estimate_key` avec dorien + mixolydien) *coûte*
sur du pop à dominante majeure : sur 234 chansons EZKeys agrégées, la tonique
tombe de ~73 % (Krumhansl-Kessler seul) à ~61 %. La cause est le flip de la paire
de quinte — un vrai majeur basculé vers son mixolydien sur la dominante (do→sol).
Ce harnais répond à la seule question qui vaille : **une règle de bascule
peut-elle garder le bénéfice modal là où il existe (vrais dorien/mixolydien) tout
en épargnant le pop ?**

Deux vérités terrain, côte à côte :
  · contrôle généré (`examples/make_corpus.build_corpus`) — mode ÉCRIT
    (maj/min/dorien/mixolydien), pièces non-modulantes seulement, tonique connue ;
  · pack EZKeys — tonique au dossier `…_Key-X_…`, agrégée par chanson, mode
    inconnu (du pop, donc maj/min en pratique).

Trois candidats de gate, tous greffés sur le flip maj→mixo existant (ils ne font
que le RESTREINDRE) :
  · `triade` — n'autoriser le flip que si le triade de la dominante (sol-si-ré)
    l'emporte sur celui de la tonique majeure (do-mi-sol) ;
  · `ton`   — que si la tonique majeure (do) est faible devant la dominante ;
  · `rang`  — que si la tonique majeure n'est pas parmi les classes les plus lourdes.

Métrique = tonique (classe de hauteur) juste : c'est ce que le profil mixolydien
existe pour récupérer (KK lit un mixolydien à sa sous-dominante). Réglage sur
graines 7/11, validation sur 23/31/47 jamais consultées pour choisir un seuil.

Résultat (validation) :

    variante          maj  min  dorien  mixo   PACK EZKeys
    MODAL (défaut)    93%  77%   100%    71%      61 %
    meilleur gate     95%  77%   100%    40%      67 %
    KK maj/min        99%  66%    80%    19%      73 %

**Verdict : ne pas gater le défaut.** Pack et vrai mixolydien partagent la
collection (do majeur = sol mixolydien) : ils sont indiscernables en masse, et les
cinq designs convergent vers un SEUL point de Pareto — +6 pts de pack pour −30 pts
de mixolydien. KK seul gagne le pack mais écrase aussi mineur (77→66) et dorien
(100→80). Le correctif n'est pas dans le moteur mais chez l'appelant : pour une
matière connue tonale et sans étiquette, `libretto.axes.estimate_key_tonal`
(KK seul) rend les points là où ils comptent ; le moteur garde MODAL pour ses axes.

100 % stdlib. Le contrôle est généré à la volée (déterministe, rien à versionner) ;
le pack est commercial (hors dépôt), passé en argument.
"""
from __future__ import annotations

import collections
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.axes import (FIFTH_PAIR_EPS, KEY_PROFILES, LEADING_MIN,  # noqa: E402
                           MEDIANT_RATIO, MODAL_PROFILES, RELATIVE_EPS,
                           RELATIVE_TIGHT, estimate_key as lib_estimate_key, pearson)
from libretto.midi import parse_midi  # noqa: E402
from make_corpus import build_corpus  # noqa: E402
from loop_index import hist_brut  # noqa: E402

TUNING = (7, 11)
VALIDATION = (23, 31, 47)
PER_SEED = 120


def triad(hist, r):
    return hist[r % 12] + hist[(r + 4) % 12] + hist[(r + 7) % 12]


def estimate(hist, profiles, gate=None):
    """Réplique `estimate_key`. `gate=(kind, param)` ajoute au flip maj→mixo une
    condition qui ne fait que le restreindre. `gate=None` ⇒ défaut lib (vérifié)."""
    if sum(hist) <= 0:
        return 0, next(iter(profiles))
    results = []
    for mode, profile in profiles.items():
        for root in range(12):
            rotated = [profile[(pc - root) % 12] for pc in range(12)]
            results.append((pearson(rotated, hist), root, mode))
    results.sort(key=lambda r: r[0], reverse=True)
    best_corr, best_root, best_mode = results[0]
    if best_mode == "maj" and "mixolydien" in profiles:
        rival_root = (best_root + 7) % 12
        argmax = max(range(12), key=lambda pc: hist[pc])
        cond = (argmax == rival_root
                and hist[(best_root + 4) % 12] < MEDIANT_RATIO * hist[rival_root])
        if cond and gate is not None:
            kind, param = gate
            if kind == "triade":
                cond = triad(hist, rival_root) > param * triad(hist, best_root)
            elif kind == "ton":
                cond = hist[best_root] < param * hist[rival_root]
            elif kind == "rang":
                rank = sorted(range(12), key=lambda pc: -hist[pc]).index(best_root)
                cond = rank >= param
        if cond:
            rival = next((r for r in results
                          if r[1] == rival_root and r[2] == "mixolydien"), None)
            if rival is not None and best_corr - rival[0] <= FIFTH_PAIR_EPS:
                best_corr, best_root, best_mode = rival
    if best_mode == "maj" and "min" in profiles and "mixolydien" in profiles:
        rel_root = (best_root + 9) % 12
        rival = next((r for r in results if r[1] == rel_root and r[2] == "min"), None)
        if rival is not None:
            gap = best_corr - rival[0]
            leading = hist[(best_root + 8) % 12] / sum(hist)
            if gap <= RELATIVE_TIGHT or (gap <= RELATIVE_EPS and leading > LEADING_MIN):
                best_corr, best_root, best_mode = rival
    return best_root, best_mode


def load_control(seeds):
    """(graine, mode écrit, tonique, histogramme brut) des pièces non-modulantes."""
    import json
    data = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            d = Path(tmp) / f"s{seed}"
            build_corpus(d, PER_SEED, seed)
            ann = json.loads((d / "annotations.json").read_text())
            for fname, t in ann.items():
                if t.get("modulate_at") or "tonic" not in t:
                    continue
                data.append((seed, t["mode"], t["tonic"], hist_brut(parse_midi(d / fname))))
    return data


KEYDIR = re.compile(r"Key-([A-G][b#]?)m?", re.I)
CONSTRUCT = re.compile(r"^[A-G][b#]?_[A-Z0-9#-]+_(HIT|RHY)\.mid$", re.I)
ROOT_PC = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
           "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
           "BB": 10, "B": 11, "CB": 11}


def load_pack(root: Path):
    """(tonique, histogramme brut agrégé) par chanson EZKeys ; None si absent."""
    if not root.is_dir():
        return None
    groups = collections.defaultdict(lambda: [0.0] * 12)
    label = {}
    for p in root.rglob("*.mid"):
        song = next((part for part in p.parts if KEYDIR.search(part)), None)
        if song is None or CONSTRUCT.match(p.name):
            continue
        tok = KEYDIR.search(song).group(1).upper()
        if tok not in ROOT_PC:
            continue
        h = hist_brut(parse_midi(p))
        g = groups[song]
        for i in range(12):
            g[i] += h[i]
        label[song] = ROOT_PC[tok]
    return [(label[s], groups[s]) for s in groups]


MODES = ["maj", "min", "dorien", "mixolydien"]
VARIANTS = [
    ("MODAL (défaut)", lambda h: estimate(h, MODAL_PROFILES)),
    ("KK maj/min", lambda h: estimate(h, KEY_PROFILES)),
    ("GATE triade 1.2", lambda h: estimate(h, MODAL_PROFILES, ("triade", 1.2))),
    ("GATE ton 0.8", lambda h: estimate(h, MODAL_PROFILES, ("ton", 0.8))),
    ("GATE rang 2", lambda h: estimate(h, MODAL_PROFILES, ("rang", 2))),
]


def main(argv: list[str]) -> int:
    pack_root = Path(argv[1]).expanduser() if len(argv) > 1 else \
        Path.home() / "Desktop/MIDI Loops Packs/Toontrack - EZKeys MIDI Loops"
    ctrl = load_control(TUNING + VALIDATION)
    pack = load_pack(pack_root)
    print(f"contrôle : {len(ctrl)} pièces non-modulantes"
          + (f" | pack : {len(pack)} chansons" if pack else " | pack : absent (contrôle seul)"))

    # garde-fou : la réplique doit être identique à la lib, sans gate
    bad = sum(1 for _s, _m, _t, h in ctrl
              if lib_estimate_key(h, MODAL_PROFILES)[:2] != estimate(h, MODAL_PROFILES))
    print(f"[sanity] réplique ≠ lib (modal, sans gate) : {bad} / {len(ctrl)} (attendu 0)\n")

    for split, seeds in [("RÉGLAGE " + "+".join(map(str, TUNING)), set(TUNING)),
                         ("VALIDATION " + "+".join(map(str, VALIDATION)), set(VALIDATION))]:
        hdr = f"{'variante':18s} | " + " ".join(f"{m[:5]:>6s}" for m in MODES) + " |   PACK"
        print(f"===== {split} =====\n{hdr}\n{'-' * len(hdr)}")
        for name, fn in VARIANTS:
            by, ok = collections.Counter(), collections.Counter()
            for seed, mode, tonic, h in ctrl:
                if seed not in seeds:
                    continue
                by[mode] += 1
                if fn(h)[0] == tonic:
                    ok[mode] += 1
            cells = " ".join(f"{(100 * ok[m] / by[m] if by[m] else 0):5.0f}%" for m in MODES)
            if pack:
                pk = sum(1 for tonic, h in pack if fn(h)[0] == tonic)
                pkcell = f"{100 * pk / len(pack):5.1f}% ({pk}/{len(pack)})"
            else:
                pkcell = "  —"
            print(f"{name:18s} | {cells} |  {pkcell}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
