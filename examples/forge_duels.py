"""
Duels d'écoute à partir d'un rapport Forge : le classement tient-il à l'oreille ?

Tout ce que Forge affirme repose sur une hypothèse jamais testée sur ses
propres sorties : que le candidat qu'il place devant est réellement le mieux
construit. Les sessions d'écoute passées ont validé les **dégradations** —
un morceau contre sa version abîmée. Elles n'ont jamais validé un
**classement** — un morceau contre un autre morceau.

Ce script prend un `forge_report.json` (de `forge.py`, `forge_loops.py`,
`forge_musiclang.py` — n'importe lequel), en tire des paires de candidats et
les écrit au format attendu par `libretto annotate`. Le côté « original » du
protocole devient le candidat que Forge préfère : si l'oreille le désigne
significativement plus souvent que le hasard, le classement veut dire
quelque chose.

    python3 examples/forge_loops.py /tmp/index.json sortie/ 48 3 --keep-all
    python3 examples/forge_duels.py sortie/ --out duels.json
    python3 -m libretto.cli annotate duels.json --out jugements_duels.json \
        --render instrument
    python3 -m libretto.cli agreement jugements_duels.json

Les paires sont réparties en deux tranches, parce que la question n'est pas
seulement « l'oreille suit-elle ? » mais **à partir de quel écart** :

  · `duel_ecart_fort`  — écart de score ≥ 0.10. Si l'oreille ne suit pas
    là, le score ne classe rien du tout ;
  · `duel_ecart_faible` — écart ≤ 0.04. Le classement fin de Forge (« le
    n°1 plutôt que le n°3 ») repose entièrement sur cette tranche ; c'est
    aussi celle où un désaccord serait le moins grave.

Le dépouillement se fait **sans `--corpus`** : `agreement` y recalculerait
la dégradation, qui n'existe pas ici. Le taux de détection par tranche EST
l'accord moteur/oreille.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import parse_midi, tick_to_seconds  # noqa: E402

TRANCHES = (("duel_ecart_fort", 0.10, 1.0), ("duel_ecart_faible", 0.0, 0.04))


def duree(path: Path) -> float:
    md = parse_midi(path)
    return tick_to_seconds(md, md.end_tick)


def construire_duels(rapport: dict, dossier: Path, n_par_tranche: int = 8,
                     seed: int = 1, max_secondes: float = 90.0) -> list[dict]:
    # Un duel se juge en écoutant les DEUX côtés en entier : deux candidats
    # de quatre minutes, c'est un quart d'heure pour une seule réponse, et
    # une session qu'on n'achève pas ne produit aucun intervalle de
    # confiance. Les longs sont écartés ici, pas en cours de route.
    ranked = [c for c in rapport["leaderboard"]
              if (dossier / c["file"]).exists()
              and duree(dossier / c["file"]) <= max_secondes]
    if len(ranked) < 2:
        raise SystemExit(
            f"{dossier}/forge_report.json : moins de deux candidats de moins de "
            f"{max_secondes:.0f} s encore sur le disque — relancez Forge avec "
            f"--keep-all (le gagnant seul ne permet aucun duel), ou relevez "
            f"--max-secondes.")

    rng = random.Random(seed)
    duels: list[dict] = []
    for etiquette, bas, haut in TRANCHES:
        paires = [(a, b) for a, b in combinations(ranked, 2)
                  if bas <= abs(a["score"] - b["score"]) <= haut]
        rng.shuffle(paires)
        # Un même candidat ne monopolise pas la tranche : sans ce garde-fou,
        # le gagnant se retrouve dans huit duels sur huit et la tranche ne
        # mesure plus que lui.
        vus: dict[str, int] = {}
        retenues = []
        for a, b in paires:
            if vus.get(a["file"], 0) >= 2 or vus.get(b["file"], 0) >= 2:
                continue
            vus[a["file"]] = vus.get(a["file"], 0) + 1
            vus[b["file"]] = vus.get(b["file"], 0) + 1
            retenues.append((a, b))
            if len(retenues) >= n_par_tranche:
                break
        for a, b in retenues:
            fort, faible = (a, b) if a["score"] >= b["score"] else (b, a)
            duels.append({
                "prefere": str((dossier / fort["file"]).resolve()),
                "autre": str((dossier / faible["file"]).resolve()),
                "etiquette": etiquette,
                "nom": f"{fort['file'].replace('.mid', '')}"
                       f"_vs_{faible['file'].replace('.mid', '')}",
                "ecart": round(fort["score"] - faible["score"], 4),
                "scores": [fort["score"], faible["score"]],
                "fiabilites": [fort["confidence"], faible["confidence"]],
            })
    return duels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("dossier", help="dossier de sortie Forge (avec forge_report.json)")
    ap.add_argument("--out", default="duels.json", help="fichier de duels")
    ap.add_argument("--par-tranche", type=int, default=8,
                    help="duels par tranche d'écart (défaut 8)")
    ap.add_argument("--max-secondes", type=float, default=90.0,
                    help="écarter les candidats plus longs (défaut 90 s)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)

    dossier = Path(args.dossier).expanduser().resolve()
    rapport = json.loads((dossier / "forge_report.json").read_text(encoding="utf-8"))
    duels = construire_duels(rapport, dossier, args.par_tranche, args.seed,
                             args.max_secondes)
    Path(args.out).write_text(
        json.dumps({"source": str(dossier), "duels": duels},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    par_tranche: dict[str, list[float]] = {}
    for d in duels:
        par_tranche.setdefault(d["etiquette"], []).append(d["ecart"])
    total = sum(duree(Path(d[c])) for d in duels for c in ("prefere", "autre"))
    print(f"{len(duels)} duels → {args.out}")
    for nom, ecarts in par_tranche.items():
        print(f"  {nom:18s} {len(ecarts):2d} paires · écart "
              f"{min(ecarts):.3f} à {max(ecarts):.3f}")
    print(f"  ~{total / 60:.0f} min d'écoute (les deux côtés, sans réécoute) "
          f"+ les paires de contrôle")
    print("\nécoute :  python3 -m libretto.cli annotate "
          f"{args.out} --out jugements_duels.json --render instrument")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
