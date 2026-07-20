"""
Libretto — dépouillement des jugements humains.

Répond à trois questions, dans cet ordre — chacune conditionne la suivante.

1. **Les jugements sont-ils exploitables ?** Les paires de contrôle opposent
   un morceau à lui-même. Un annotateur attentif y répond « aucune
   différence » ; s'il tranche systématiquement, il répond au hasard ou croit
   entendre ce qu'on lui suggère, et le reste du lot ne vaut rien. C'est le
   garde-fou : sans lui, un taux de détection élevé peut n'être qu'un biais
   de réponse.

2. **L'hypothèse fondatrice tient-elle ?** Les dégradations sont supposées
   rendre la musique moins bien structurée. Si l'oreille ne les entend pas,
   la calibration contrastive optimise contre un fantôme. Le résultat se lit
   dégradation par dégradation : certaines peuvent être audibles et d'autres
   pas, ce qui indiquerait lesquelles retirer du protocole.

3. **Le moteur est-il d'accord avec l'oreille ?** Sur les paires que
   l'humain a tranchées, Libretto désigne-t-il le même côté ? C'est la seule
   mesure de ce dossier qui porte sur la validité *externe* du score : tout
   le reste — AUC, validation croisée, F-mesure — ne teste que sa cohérence
   avec ses propres hypothèses.

Un accord fort ne prouve pas que le score SMS capte la qualité musicale : il
prouve que moteur et oreille s'accordent sur *ces* dégradations-là. Un
désaccord, lui, est une réfutation nette.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from .axes import SenseOfMusicalStructure
from .builder import build_score
from .calibrate import DEGRADATIONS
from .midi import parse_midi

CONTROL = "__control__"


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance de Wilson à 95 %. Sur vingt jugements, une
    proportion brute ne veut pas dire grand-chose ; l'intervalle dit à partir
    de quand un écart à 0.5 est réel."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def analyse(judgements_path: str | Path, corpus_dir: str | Path | None = None,
            seed: int = 1) -> dict:
    data = json.loads(Path(judgements_path).read_text(encoding="utf-8"))
    records = data.get("judgements", [])
    if not records:
        raise ValueError(f"aucun jugement dans {judgements_path}")

    controls = [r for r in records if r["degradation"] == CONTROL]
    real = [r for r in records if r["degradation"] != CONTROL]

    # 1. Fiabilité de l'annotateur
    ctrl_same = sum(1 for r in controls if r["choice"] == "same")
    control_quality = (ctrl_same / len(controls)) if controls else None

    # 2. Détection humaine, par dégradation
    by_deg: dict[str, list[dict]] = defaultdict(list)
    for r in real:
        by_deg[r["degradation"]].append(r)
    detection = {}
    for name, rows in sorted(by_deg.items()):
        decided = [r for r in rows if r["picked_original"] is not None]
        hits = sum(1 for r in decided if r["picked_original"])
        lo, hi = _wilson(hits, len(decided))
        detection[name] = {
            "n": len(rows),
            "n_decided": len(decided),
            "n_same": len(rows) - len(decided),
            "taux_original": round(hits / len(decided), 3) if decided else None,
            "ic95": [round(lo, 3), round(hi, 3)],
            # Au-dessus de 0.5 avec un intervalle qui ne le contient pas :
            # la dégradation s'entend.
            "audible": bool(decided) and lo > 0.5,
            # Symétrique, et bien plus grave : l'intervalle entier est SOUS
            # 0.5, donc l'oreille préfère la version dégradée. Ce n'est pas
            # un résultat nul, c'est une réfutation — la « dégradation » n'en
            # est pas une, et tout axe calibré contre elle a été optimisé
            # vers l'inverse de ce qu'on croyait mesurer.
            "inversee": bool(decided) and hi < 0.5,
        }

    decided_all = [r for r in real if r["picked_original"] is not None]
    hits_all = sum(1 for r in decided_all if r["picked_original"])
    lo, hi = _wilson(hits_all, len(decided_all))

    report = {
        "n_judgements": len(records),
        "n_control": len(controls),
        "control_answered_same": round(control_quality, 3) if control_quality is not None else None,
        "n_decided": len(decided_all),
        "n_same": len(real) - len(decided_all),
        "taux_original_global": round(hits_all / len(decided_all), 3) if decided_all else None,
        "ic95_global": [round(lo, 3), round(hi, 3)],
        "detection_par_degradation": detection,
        "warnings": [],
    }

    inverted = [n for n, d in detection.items() if d["inversee"]]
    if inverted:
        report["warnings"].append(
            "dégradation(s) que l'oreille juge MEILLEURES que l'original : "
            + ", ".join(inverted)
            + ". Elles ne sont pas des négatifs valides — les axes calibrés "
            "contre elles ont été optimisés à contresens.")

    if controls and control_quality is not None and control_quality < 0.5:
        report["warnings"].append(
            f"paires de contrôle : seulement {control_quality:.0%} de « aucune "
            "différence » sur des extraits IDENTIQUES. L'annotateur tranche des "
            "paires indiscernables — les autres taux sont à lire avec méfiance.")
    if not controls:
        report["warnings"].append(
            "aucune paire de contrôle : impossible d'estimer le bruit de réponse.")
    if len(decided_all) < 20:
        report["warnings"].append(
            f"{len(decided_all)} jugements tranchés seulement : les intervalles "
            "de confiance sont trop larges pour conclure.")

    if corpus_dir is not None:
        report["accord_moteur"] = _engine_agreement(real, Path(corpus_dir), seed)
    return report


def _engine_agreement(records: list[dict], corpus_dir: Path, seed: int) -> dict:
    """Sur les paires que l'humain a tranchées, le moteur choisit-il le même
    côté ? On recalcule les deux scores plutôt que de les lire ailleurs : le
    jugement et le score doivent porter exactement sur les mêmes octets."""
    import random

    cache: dict[str, float] = {}

    def score_of(path: Path) -> float | None:
        key = str(path)
        if key not in cache:
            try:
                sms = SenseOfMusicalStructure(build_score(parse_midi(path)))
                sms.calculate()
                cache[key] = sms.get_score()
            except (ValueError, OSError):
                return None
        return cache[key]

    agree = 0
    total = 0
    per_deg: dict[str, list[int]] = defaultdict(list)
    for r in records:
        if r["picked_original"] is None:
            continue
        path = corpus_dir / r["file"]
        if not path.exists():
            continue
        pos = score_of(path)
        if pos is None:
            continue
        try:
            md = parse_midi(path)
            rng = random.Random(f"{seed}:{r['file']}:{r['degradation']}")
            degraded = DEGRADATIONS[r["degradation"]](md, rng)
            sms = SenseOfMusicalStructure(build_score(degraded))
            sms.calculate()
            neg = sms.get_score()
        except (ValueError, OSError, KeyError):
            continue
        engine_picks_original = pos > neg
        same = engine_picks_original == bool(r["picked_original"])
        agree += int(same)
        total += 1
        per_deg[r["degradation"]].append(int(same))

    if not total:
        return {"n": 0, "note": "aucune paire recalculable (corpus introuvable ?)"}
    lo, hi = _wilson(agree, total)
    return {
        "n": total,
        "accord": round(agree / total, 3),
        "ic95": [round(lo, 3), round(hi, 3)],
        "par_degradation": {k: round(sum(v) / len(v), 3)
                            for k, v in sorted(per_deg.items())},
    }


def format_report(report: dict) -> str:
    lines = ["=== ACCORD HUMAIN / LIBRETTO ==="]
    lines.append(f"jugements : {report['n_judgements']}  "
                 f"(tranchés {report['n_decided']}, « aucune différence » "
                 f"{report['n_same']}, contrôles {report['n_control']})")
    ctrl = report["control_answered_same"]
    if ctrl is not None:
        verdict = "cohérent" if ctrl >= 0.5 else "SUSPECT"
        lines.append(f"contrôles (extraits identiques) : {ctrl:.0%} de "
                     f"« aucune différence » — {verdict}")
    taux = report["taux_original_global"]
    if taux is not None:
        lo, hi = report["ic95_global"]
        lines.append(f"\nl'oreille désigne l'original dans {taux:.0%} des cas "
                     f"(IC95 {lo:.2f}–{hi:.2f})")
        if lo > 0.5:
            lines.append("  → les dégradations s'entendent : l'hypothèse "
                         "contrastive tient.")
        else:
            lines.append("  → indistinguable du hasard : sur ce lot, rien ne "
                         "montre que les dégradations s'entendent.")
    lines.append("\n── par dégradation ──")
    for name, d in report["detection_par_degradation"].items():
        if d["taux_original"] is None:
            lines.append(f"  {name:20} aucun jugement tranché")
            continue
        mark = ("audible" if d["audible"] else
                "INVERSÉE — l'oreille préfère le dégradé" if d["inversee"] else
                "non concluant")
        lines.append(f"  {name:20} {d['taux_original']:.0%} "
                     f"(IC95 {d['ic95'][0]:.2f}–{d['ic95'][1]:.2f}, "
                     f"n={d['n_decided']}, ø={d['n_same']}) — {mark}")
    acc = report.get("accord_moteur")
    if acc and acc.get("n"):
        valid = {k: v for k, v in acc["par_degradation"].items()
                 if k not in {n for n, d in report["detection_par_degradation"].items()
                              if d["inversee"]}}
        lines.append(f"\n── accord avec le moteur ──")
        lines.append(f"  même côté choisi dans {acc['accord']:.0%} des cas "
                     f"(IC95 {acc['ic95'][0]:.2f}–{acc['ic95'][1]:.2f}, n={acc['n']})")
        for name, v in acc["par_degradation"].items():
            flag = "  (dégradation invalidée par l'oreille)" if name not in valid else ""
            lines.append(f"    {name:20} {v:.0%}{flag}")
        if valid and len(valid) < len(acc["par_degradation"]):
            moy = sum(valid.values()) / len(valid)
            lines.append(f"  hors dégradations invalidées : {moy:.0%} d'accord")
    for w in report["warnings"]:
        lines.append(f"\n⚠ {w}")
    return "\n".join(lines)
