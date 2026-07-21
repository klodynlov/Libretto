"""
Forge sweep — la règle « fiabilité d'abord » vaut-elle quelque chose ?

Ce que ça mesure
----------------
`forge.py` sélectionne le meilleur candidat *fiabilité d'abord* : tranche de
fiabilité (« élevée » avant « moyenne »), puis score à l'intérieur de la
tranche. Une question légitime : cette règle change-t-elle vraiment le
résultat, ou fait-elle la même chose que « prendre le meilleur score » ? Et
si elle change quelque chose, à quel prix en score brut ?

Un seul tirage ne répond pas — il donne une anecdote. Ce script balaie N
graines, et pour chacune compare le gagnant *fiabilité d'abord* au gagnant
qu'aurait désigné la règle *score seul* (le meilleur score parmi les
éligibles). Il agrège :

- **combien de fois la règle change le gagnant** — si c'est ~0, la règle est
  cosmétique ; si c'est ~100 %, c'est le tri par score qui est cassé ; entre
  les deux, la règle arbitre un vrai désaccord ;
- **le score brut sacrifié quand elle change** — ce qu'on paie, en score, pour
  gagner une tranche de fiabilité. Un prix élevé remettrait la règle en cause ;
- **combien de candidats le gate `--min-confidence` recale** — le garde-fou
  est-il sollicité, même sur un corpus 100 % « morceaux » ?
- **la collapse de diversité** — combien de formes distinctes survivent dans
  le top 5, tirage après tirage.

Reproductible
-------------
Tout est déterministe : mêmes graines → mêmes chiffres. Le générateur est
celui de `make_corpus` (via `forge`), stdlib pure. Les MIDI intermédiaires
sont écrits dans un dossier de travail temporaire puis effacés (`--keep` les
conserve pour inspection).

Usage
-----
    python3 examples/forge_sweep.py [n_seeds=10] [n=24] [start_seed=1]
        [--json rapport.json] [--keep dossier/]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge import forge  # noqa: E402

# Tranches, de la plus sûre à la moins sûre (pour l'affichage ordonné).
TIER_ORDER = ["élevée", "moyenne", "faible", "insuffisante"]


def _tier_counts(leaderboard: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in leaderboard:
        counts[c["level"]] = counts.get(c["level"], 0) + 1
    return counts


def sweep(n_seeds: int = 10, n: int = 24, start_seed: int = 1,
          work_dir: str | Path | None = None, keep: bool = False) -> dict:
    """Lance Forge sur `n_seeds` graines consécutives et agrège la
    comparaison fiabilité-d'abord vs score-seul. Renvoie un rapport
    sérialisable."""
    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="forge_sweep_"))
    base.mkdir(parents=True, exist_ok=True)
    seeds = list(range(start_seed, start_seed + n_seeds))

    per_seed = []
    for s in seeds:
        report = forge(base / f"seed_{s}", n=n, seed=s)
        lb = report["leaderboard"]  # déjà trié fiabilité-d'abord
        if not lb:
            # Aucun candidat éligible (gate trop haut / corpus trop pauvre) :
            # rien à comparer, on l'enregistre tel quel.
            per_seed.append({
                "seed": s, "eligible": report["n_eligible"],
                "rejected_confidence": report["n_rejected_confidence"],
                "winner": None, "pure_winner": None,
                "rule_changed_winner": False, "score_sacrificed": None,
                "tiers": {}, "diversity": report["diversity"],
            })
            continue

        winner = lb[0]                                   # fiabilité d'abord
        pure = max(lb, key=lambda c: c["score"])         # score seul
        changed = pure["file"] != winner["file"]
        per_seed.append({
            "seed": s,
            "eligible": report["n_eligible"],
            "rejected_confidence": report["n_rejected_confidence"],
            "winner": {"form": winner["form"], "mode": winner["mode"],
                       "meter": winner["meter"], "score": winner["score"],
                       "confidence": winner["confidence"], "level": winner["level"]},
            "pure_winner": {"form": pure["form"], "mode": pure["mode"],
                            "meter": pure["meter"], "score": pure["score"],
                            "confidence": pure["confidence"], "level": pure["level"]},
            "rule_changed_winner": changed,
            # Score brut cédé pour gagner une tranche de fiabilité (0 si la
            # règle ne change rien : le meilleur score était déjà le plus sûr).
            "score_sacrificed": round(pure["score"] - winner["score"], 4) if changed else 0.0,
            "tiers": _tier_counts(lb),
            "diversity": report["diversity"],
        })

    if not keep:
        shutil.rmtree(base, ignore_errors=True)

    return {
        "seeds": seeds,
        "n_per_seed": n,
        "per_seed": per_seed,
        "aggregate": _aggregate(per_seed),
        "work_dir": str(base) if keep else None,
    }


def _aggregate(per_seed: list[dict]) -> dict:
    scored = [r for r in per_seed if r["winner"] is not None]
    n = len(scored)
    changed = [r for r in scored if r["rule_changed_winner"]]
    gaps = [r["score_sacrificed"] for r in changed]
    total_eligible = sum(r["eligible"] for r in scored)
    tier_total: dict[str, int] = {}
    for r in scored:
        for level, c in r["tiers"].items():
            tier_total[level] = tier_total.get(level, 0) + c
    div_top = [r["diversity"]["distinct_forms_in_top"] for r in scored]
    div_all = [r["diversity"]["distinct_forms_overall"] for r in scored]

    return {
        "n_seeds_scored": n,
        "rule_changed_count": len(changed),
        "rule_changed_fraction": round(len(changed) / n, 3) if n else None,
        "score_sacrificed_mean": round(sum(gaps) / len(gaps), 4) if gaps else None,
        "score_sacrificed_min": min(gaps) if gaps else None,
        "score_sacrificed_max": max(gaps) if gaps else None,
        "gate_active_seeds": sum(1 for r in scored if r["rejected_confidence"]),
        "total_rejected_confidence": sum(r["rejected_confidence"] for r in scored),
        "total_eligible": total_eligible,
        "tier_totals": {t: tier_total.get(t, 0) for t in TIER_ORDER if tier_total.get(t)},
        "diversity_top_mean": round(sum(div_top) / len(div_top), 2) if div_top else None,
        "diversity_overall_mean": round(sum(div_all) / len(div_all), 2) if div_all else None,
    }


def _print_report(report: dict) -> None:
    ps = report["per_seed"]
    print(f"Forge sweep — {len(report['seeds'])} graines × {report['n_per_seed']} "
          f"candidats ({len(report['seeds']) * report['n_per_seed']} morceaux)\n")
    print("graine | gagnant (fiabilité d'abord)        | règle change ? | score cédé | recalés | tranches")
    print("-------|------------------------------------|----------------|------------|---------|---------")
    for r in ps:
        if r["winner"] is None:
            print(f"  {r['seed']:>2}   | (aucun éligible)                   | "
                  f"—              |     —      | {r['rejected_confidence']:>4}    |")
            continue
        w = r["winner"]
        wl = f"{w['form']}/{w['mode']} {w['meter']}"
        changed = r["rule_changed_winner"]
        chstr = "OUI" if changed else "non"
        gap = f"{r['score_sacrificed']:.3f}" if changed else "  —"
        tiers = " ".join(f"{t[:3]}:{r['tiers'][t]}"
                         for t in TIER_ORDER if r["tiers"].get(t))
        print(f"  {r['seed']:>2}   | {wl:<34} | {chstr:<14} | {gap:>6}     | "
              f"{r['rejected_confidence']:>4}    | {tiers}")

    a = report["aggregate"]
    print(f"\n=== AGRÉGAT ({a['n_seeds_scored']} graines) ===")
    if a["rule_changed_fraction"] is not None:
        print(f"La règle change le gagnant : {a['rule_changed_count']}/"
              f"{a['n_seeds_scored']} ({a['rule_changed_fraction']*100:.0f} %)")
    if a["score_sacrificed_mean"] is not None:
        print(f"  quand elle change : score brut cédé {a['score_sacrificed_mean']:.3f} "
              f"en moyenne (min {a['score_sacrificed_min']:.3f}, "
              f"max {a['score_sacrificed_max']:.3f})")
    print(f"Gate --min-confidence actif : {a['gate_active_seeds']}/{a['n_seeds_scored']} "
          f"graines, {a['total_rejected_confidence']} candidats recalés")
    tot = a["total_eligible"]
    tiers = "  ".join(f"{t} {c} ({100*c//tot} %)"
                      for t, c in a["tier_totals"].items()) if tot else "—"
    print(f"Tranches (sur {tot} éligibles) : {tiers}")
    print(f"Diversité : {a['diversity_top_mean']} formes distinctes dans le top 5, "
          f"pour {a['diversity_overall_mean']} générées en moyenne")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_sweep",
        description="Forge sweep — mesure ce qu'apporte la règle « fiabilité "
                    "d'abord », agrégé sur plusieurs graines.")
    parser.add_argument("n_seeds", nargs="?", type=int, default=10,
                        help="nombre de graines (défaut 10)")
    parser.add_argument("n", nargs="?", type=int, default=24,
                        help="candidats par graine (défaut 24)")
    parser.add_argument("start_seed", nargs="?", type=int, default=1,
                        help="première graine (défaut 1)")
    parser.add_argument("--json", metavar="OUT", help="écrire le rapport agrégé en JSON")
    parser.add_argument("--keep", metavar="DIR",
                        help="conserver les MIDI intermédiaires dans DIR (défaut : effacés)")
    args = parser.parse_args(argv)

    report = sweep(n_seeds=args.n_seeds, n=args.n, start_seed=args.start_seed,
                   work_dir=args.keep, keep=bool(args.keep))
    _print_report(report)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON  → {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
