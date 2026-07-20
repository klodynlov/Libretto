"""
Forge — sélection structurelle : générer N ébauches, garder celle que
Libretto juge la mieux construite.

L'idée
------
Un système génératif produit du matériau ; Libretto ne compose rien, il
*juge*. Forge branche le second sur le premier : le score SMS devient une
**fonction de fitness**. On tire N candidats, on les note, et on garde le
meilleur — mais « meilleur » est défini fiabilité d'abord (voir plus bas).
C'est exactement le trou que les modèles génératifs laissent ouvert — ils
sont forts sur le grain et le timbre, faibles sur la forme longue (couplets,
refrains, arc émotionnel), et c'est précisément ce que les 6 groupes d'axes
mesurent.

    ┌───────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
    │ générateur│ → │  → MIDI  │ → │  LIBRETTO  │ → │ sélection│
    │ (ébauches)│   │  (.mid)  │   │ SMS + fiab.│   │ du meilleur
    └───────────┘   └──────────┘   └────────────┘   └──────────┘

Le générateur ici est celui de `make_corpus` (procédural, stdlib, graine).
Dans une vraie chaîne « Forge » on remplacerait cette brique par une
transcription (basic-pitch) d'un rendu audio, ou par la sortie MIDI d'un
modèle — le reste ne bouge pas, puisque Forge ne parle que MIDI.

La règle de sélection : fiabilité d'abord
-----------------------------------------
Sélectionner sur `get_score()` seul reviendrait à comparer des chiffres qui
ne veulent pas tous dire la même chose (cf. README, « Fiabilité »). Deux
garde-fous :

1. **Gate.** Un candidat dont la fiabilité est sous `--min-confidence` (0.55
   par défaut) est écarté AVANT le classement : son score ne se lit pas, le
   prendre comme gagnant serait choisir un chiffre en l'air. Même contrat que
   `analyze --min-confidence`.
2. **Tri par tranche, puis score.** Parmi les éligibles, on classe D'ABORD
   par tranche de fiabilité (« élevée » ≥ 0.75 avant « moyenne » ≥ 0.55),
   puis par score À L'INTÉRIEUR de la tranche. Un morceau très bien noté mais
   moyennement fiable ne bat donc pas un morceau à peine moins noté mais
   pleinement fiable — parce que son score, lui, est plus digne de foi. Les
   seuils de tranche ne sont pas arbitraires : ce sont ceux que le README
   calibre sur 200 fichiers (élevée/moyenne ≈ 0.94 d'accuracy). On ne trie
   PAS sur la confiance brute, qui laisserait un 0.68 à confiance 1.00
   l'emporter sur un 0.88 à confiance 0.99 — absurde.

Le piège assumé : la circularité
--------------------------------
`make_corpus` varie *délibérément au-delà* de la zone de confort des axes
(formes à travers-composé, mesures impaires, arcs plats). Sélectionner le SMS
maximal rappelle donc mécaniquement l'esthétique inscrite dans les bandes de
tolérance — pop, en arche, carrée. Forge le montre au lieu de le cacher : le
rapport compare la distribution des formes du peloton de tête à celle de tous
les candidats. Si le top-K est plus pauvre, ce n'est pas un bug de Forge,
c'est ce que « optimiser un score » fait à la diversité. Un vrai pipeline en
tiendrait compte (diversité sous contrainte de score, pas score seul).

Usage
-----
    python3 examples/forge.py sortie/ [n=24] [seed=1]
        [--min-confidence 0.55] [--min-score 0.0]
        [--keep-all] [--reaper]

Sortie : `sortie/forge_winner.mid` (le gagnant), `sortie/forge_report.json`
(le classement complet), et un tableau lisible sur stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.axes import SenseOfMusicalStructure  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.midi import parse_midi, write_midi  # noqa: E402

# make_corpus n'est pas un package : on l'importe comme module voisin, le
# `sys.path` ci-dessus rend les deux imports (racine + examples/) possibles.
import random as _random  # noqa: E402
from make_corpus import Style, random_style, render  # noqa: E402

# Ordre des tranches de fiabilité (haut = plus fiable). Sert de clé de tri
# primaire : on préfère toujours une tranche plus sûre, et le score ne
# départage qu'à l'intérieur d'une même tranche. Les libellés viennent de
# SenseOfMusicalStructure.confidence_level().
_TIER_RANK = {"élevée": 3, "moyenne": 2, "faible": 1, "insuffisante": 0}


@dataclass
class Candidate:
    """Un candidat noté : le fichier, son style, et le verdict de Libretto."""
    index: int
    path: Path
    form: str
    mode: str
    meter: str
    bpm: int
    score: float
    confidence: float
    level: str
    interpretable: bool
    groups: dict[str, float]

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "file": self.path.name,
            "form": self.form,
            "mode": self.mode,
            "meter": self.meter,
            "bpm": self.bpm,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "level": self.level,
            "interpretable": self.interpretable,
            "groups": {g: round(v, 3) for g, v in self.groups.items()},
        }


def generate_and_score(index: int, seed: int, out_dir: Path) -> Candidate | None:
    """Tire un candidat déterministe, l'écrit en MIDI, le fait noter par
    Libretto. Renvoie None si le candidat n'a produit aucune note exploitable
    (le générateur peut, sur certaines graines, sortir une pièce vide de
    sections analysables — on l'écarte plutôt que de le classer)."""
    rng = _random.Random(f"forge:{seed}:{index}")
    style: Style = random_style(rng)
    tracks, markers, tempo_changes, _bars, _truth, _roles = render(style, rng)
    num, den, _bar_beats, _pulse = style.meter

    path = out_dir / f"candidate_{index:03d}.mid"
    write_midi(path, tracks, ppq=480, bpm=style.bpm,
               time_sig=(num, den), markers=markers or None,
               tempo_changes=tempo_changes or None)

    score_obj = build_score(parse_midi(path))
    if not score_obj.sections:
        path.unlink(missing_ok=True)
        return None

    sms = SenseOfMusicalStructure(score_obj)
    sms.calculate()
    return Candidate(
        index=index,
        path=path,
        form=style.form,
        mode=style.mode,
        meter=f"{num}/{den}",
        bpm=int(style.bpm),
        score=sms.get_score(),
        confidence=sms.confidence(),
        level=sms.confidence_level(),
        interpretable=sms.is_interpretable(),
        groups=sms.group_scores(),
    )


def forge(out_dir: str | Path, n: int = 24, seed: int = 1,
          min_confidence: float = SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
          min_score: float = 0.0, keep_all: bool = False) -> dict:
    """Génère n candidats, les note, sélectionne le meilleur — fiabilité
    d'abord (tranche puis score), après le gate `min_confidence`.

    Renvoie un rapport sérialisable. Le gagnant est copié en
    `forge_winner.mid`. Sans `keep_all`, les candidats non retenus sont
    effacés (un pipeline garde le gagnant, pas les 23 brouillons)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates: list[Candidate] = []
    empties = 0
    for i in range(n):
        cand = generate_and_score(i, seed, out)
        if cand is None:
            empties += 1
            continue
        candidates.append(cand)

    # Gate fiabilité AVANT le classement : un score non interprétable ne
    # concourt pas, quelle que soit sa valeur.
    eligible = [c for c in candidates
                if c.confidence >= min_confidence and c.score >= min_score]
    rejected_conf = [c for c in candidates if c.confidence < min_confidence]
    rejected_score = [c for c in candidates
                      if c.confidence >= min_confidence and c.score < min_score]

    # Fiabilité prioritaire : tranche d'abord (« élevée » avant « moyenne »),
    # score seulement à égalité de tranche. Un score plus haut mais moins
    # fiable ne l'emporte pas — son chiffre est moins digne de foi.
    ranked = sorted(eligible,
                    key=lambda c: (_TIER_RANK.get(c.level, 0), c.score),
                    reverse=True)
    winner = ranked[0] if ranked else None

    winner_path = None
    if winner is not None:
        winner_path = out / "forge_winner.mid"
        winner_path.write_bytes(winner.path.read_bytes())

    # Le gagnant est copié sous `forge_winner.mid` ; sans --keep-all on efface
    # tous les brouillons, l'original compris (un pipeline garde le gagnant,
    # pas les N ébauches).
    if not keep_all:
        for c in candidates:
            c.path.unlink(missing_ok=True)

    report = {
        "n_requested": n,
        "n_generated": len(candidates),
        "n_empty_skipped": empties,
        "seed": seed,
        "gates": {"min_confidence": min_confidence, "min_score": min_score},
        "n_eligible": len(eligible),
        "n_rejected_confidence": len(rejected_conf),
        "n_rejected_score": len(rejected_score),
        "winner": winner.as_dict() if winner else None,
        "winner_file": winner_path.name if winner_path else None,
        "leaderboard": [c.as_dict() for c in ranked],
        "rejected_low_confidence": [c.as_dict() for c in
                                    sorted(rejected_conf, key=lambda c: c.confidence)],
        "diversity": _diversity_report(candidates, ranked),
    }
    (out / "forge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _diversity_report(all_c: list[Candidate], ranked: list[Candidate],
                      top: int = 5) -> dict:
    """Compare la variété des formes du peloton de tête à celle de l'ensemble.
    Rend visible la collapse de diversité qu'induit toute sélection sur un
    score unique — le point d'honnêteté du README sur la circularité."""
    def dist(cs: list[Candidate]) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in cs:
            out[c.form] = out.get(c.form, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    head = ranked[:top]
    return {
        "top_n": len(head),
        "forms_overall": dist(all_c),
        "forms_in_top": dist(head),
        "distinct_forms_overall": len({c.form for c in all_c}),
        "distinct_forms_in_top": len({c.form for c in head}),
    }


def _print_report(report: dict) -> None:
    print(f"Forge — {report['n_generated']} candidats notés "
          f"(graine {report['seed']}, {report['n_empty_skipped']} vides écartés)")
    g = report["gates"]
    print(f"gates : fiabilité ≥ {g['min_confidence']:g}, score ≥ {g['min_score']:g}  "
          f"→ {report['n_eligible']} éligibles, "
          f"{report['n_rejected_confidence']} recalés (fiabilité), "
          f"{report['n_rejected_score']} recalés (score)")

    win = report["winner"]
    if not win:
        print("\n⚠ Aucun candidat fiable : rien à sélectionner. "
              "Augmentez n, ou baissez --min-confidence en sachant ce que ça "
              "coûte (README, « Fiabilité »).")
        return

    print(f"\n🏆 GAGNANT → {report['winner_file']}")
    print(f"   {win['form']} · {win['mode']} · {win['meter']} · {win['bpm']} bpm")
    print(f"   score {win['score']:.3f}  ·  fiabilité {win['confidence']:.3f} "
          f"({win['level']})")

    print("\nClassement (éligibles — tranche de fiabilité d'abord, puis score) :")
    print(f"  {'#':>2}  {'tranche':<11} {'score':>6}  {'fiab.':>6}  "
          f"{'forme':<18} {'mode':<11} {'métr.':>5}")
    for rank, c in enumerate(report["leaderboard"][:10], 1):
        flag = "  ←" if c["file"] == report["winner_file"] else ""
        print(f"  {rank:>2}  {c['level']:<11} {c['score']:>6.3f}  {c['confidence']:>6.3f}  "
              f"{c['form']:<18} {c['mode']:<11} {c['meter']:>5}{flag}")

    d = report["diversity"]
    print(f"\nDiversité des formes : {d['distinct_forms_overall']} sur l'ensemble, "
          f"{d['distinct_forms_in_top']} dans le top {d['top_n']}.")
    if d["distinct_forms_in_top"] < d["distinct_forms_overall"]:
        print("  → optimiser le SMS resserre le peloton de tête sur les formes que "
              "les bandes de tolérance récompensent. Attendu, pas accidentel "
              "(README, circularité) : un vrai pipeline sélectionnerait sous "
              "contrainte de diversité, pas sur le score seul.")


def _maybe_push_reaper(winner_file: Path) -> None:
    from libretto.reaper import BridgeError, push_mididata
    try:
        result = push_mididata(parse_midi(winner_file))
    except (ValueError, BridgeError) as exc:
        print(f"forge: REAPER indisponible : {exc}", file=sys.stderr)
        return
    print(f"→ poussé dans REAPER {result['reaper']} : {result['total_notes']} notes, "
          f"lecture lancée")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — sélection structurelle : génère N ébauches, "
                    "garde celle que Libretto juge la mieux construite.")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=24,
                        help="nombre de candidats (défaut 24)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--min-confidence", type=float,
                        default=SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--keep-all", action="store_true",
                        help="conserver tous les candidats (défaut : garder le gagnant)")
    parser.add_argument("--reaper", action="store_true",
                        help="pousser le gagnant dans REAPER via le pont Klody")
    args = parser.parse_args(argv)

    report = forge(args.out_dir, n=args.n, seed=args.seed,
                   min_confidence=args.min_confidence, min_score=args.min_score,
                   keep_all=args.keep_all)
    _print_report(report)

    if args.reaper and report["winner_file"]:
        _maybe_push_reaper(Path(args.out_dir) / report["winner_file"])

    # Exit 2 si aucun candidat fiable : Forge est utilisable comme gate.
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
