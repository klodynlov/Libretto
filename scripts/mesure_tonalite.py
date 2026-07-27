"""Mesure de l'estimation de tonalité contre vérité terrain.

Usage :
    python3 scripts/mesure_tonalite.py ~/Desktop/SAMPLES/MIDI [--json out.json]

Krumhansl-Kessler (`libretto.axes.estimate_key`) alimente sept axes du
groupe B et n'a jamais été confronté à une tonalité connue : le corpus de
validation est synthétique, donc la tonalité y est celle que le générateur
a écrite — vérifier l'estimateur dessus reviendrait à vérifier qu'il
retrouve ce qu'on vient d'y mettre. Les packs de loops du commerce, eux,
annoncent la tonalité dans le nom des fichiers et des dossiers
(`Bonfire_Dm_100_Piano_Chords.mid`, `KIT_4_Emin _100BPM/`). C'est une
étiquette humaine, indépendante du moteur : la seule vérité terrain tonale
disponible sans annoter à la main.

Deux estimateurs sont mesurés côte à côte, parce qu'ils ne servent pas la
même chose :

  · `pipeline` — `estimate_key(_pc_hist(sections))`, exactement ce que fait
    le moteur dans les axes 8-14 : histogramme construit sur les accords
    détectés et la voix supérieure, pas sur les notes brutes ;
  · `brut` — histogramme des notes non-percussives pondéré par la durée.
    C'est le candidat pour indexer un pack (une boucle de 4 mesures n'a ni
    sections ni progression, le pipeline y travaille à vide).

Réserves inscrites dans la sortie plutôt que dans une note de bas de page :

  · l'étiquette vaut pour le **kit**, pas pour le fichier — un snare
    étiqueté `G_m` n'a aucune hauteur à estimer. Les fichiers percussifs
    sont donc écartés de la mesure et comptés à part ;
  · les fichiers d'un même kit partagent une étiquette : ce ne sont pas des
    tirages indépendants. D'où l'exactitude **macro** (moyenne par kit) à
    côté de la micro (moyenne par fichier) ;
  · l'étiquette elle-même peut être fausse — c'est un nom de fichier, pas
    une annotation contrôlée. Les désaccords massifs sur un kit entier sont
    à lire dans les deux sens.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from libretto.axes import PC_NAMES, SenseOfMusicalStructure, estimate_key  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402

# La lecture des étiquettes d'un pack vit dans `examples/loop_index.py` : ce
# harnais MESURE ce que cette lecture vaut, il ne peut pas en tenir une
# seconde copie — deux parseurs qui divergent, et le chiffre ne dit plus de
# quoi il parle.
from loop_index import (hist_brut, label_for,  # noqa: E402,F401
                        parse_key_label, role_of)


def hist_pipeline(md) -> list[float]:
    """L'histogramme que le moteur utilise réellement (accords + mélodie)."""
    score = build_score(md)
    sms = SenseOfMusicalStructure(score)
    return sms._pc_hist(score.sections)


# ──────────────────────────────────────────────
# Classement des erreurs
# ──────────────────────────────────────────────

def error_kind(pred_pc: int, pred_mode: str, true_pc: int, true_mode: str | None) -> str:
    """Nature du désaccord — les confusions de KK ont chacune leur sens.

    « relative » (Dm ↔ F) et « parallèle » (Dm ↔ D) sont des quasi-succès :
    le matériau de hauteurs est le bon, c'est le centre qui est mal placé.
    « dominante » / « sous-dominante » indiquent une boucle qui insiste sur
    le V ou le IV. Le reste est une vraie erreur.
    """
    if pred_pc == true_pc:
        if true_mode is None or pred_mode == true_mode:
            return "exact"
        return "parallele"
    if true_mode == "min" and pred_pc == (true_pc + 3) % 12 and pred_mode == "maj":
        return "relative"
    if true_mode == "maj" and pred_pc == (true_pc - 3) % 12 and pred_mode == "min":
        return "relative"
    if true_mode is None and pred_pc in ((true_pc + 3) % 12, (true_pc - 3) % 12):
        return "relative"
    if pred_pc == (true_pc + 7) % 12:
        return "dominante"
    if pred_pc == (true_pc + 5) % 12:
        return "sous-dominante"
    return "autre"


def pct(num: int, den: int) -> str:
    return f"{num / den:.3f}" if den else "  —  "


# ──────────────────────────────────────────────
# Collecte : deux sources d'étiquettes
# ──────────────────────────────────────────────

def collect_pack(root: Path, limit: int = 0) -> tuple[list[dict], dict]:
    """Packs du commerce : l'étiquette est dans le nom du fichier ou du kit."""
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in (".mid", ".midi"))
    if limit:
        files = files[:limit]
    stats = Counter()
    stats["total"] = len(files)
    specs: list[dict] = []
    for path in files:
        lab = label_for(path, root)
        if lab is None:
            stats["sans_etiquette"] += 1
            continue
        true_pc, true_mode, source = lab
        role = role_of(path)
        if role == "batterie":
            stats["percussifs"] += 1
            continue
        propre = parse_key_label(path.stem)
        if propre and propre[0] != true_pc:
            stats["conflits"] += 1
        specs.append({"path": path, "fichier": str(path.relative_to(root)),
                      "kit": str(path.parent.relative_to(root)), "role": role,
                      "source": source, "vrai_pc": true_pc, "vrai_mode": true_mode})
    return specs, stats


def collect_control(root: Path) -> tuple[list[dict], dict]:
    """Corpus généré par `make_corpus` : la tonalité est connue par construction.

    Contrôle positif — sans lui, un taux bas sur les packs ne distingue pas
    « l'estimateur est faible » de « ce répertoire lui échappe ». Les
    morceaux qui modulent sont écartés : ils n'ont pas *une* tonalité.
    """
    truth = json.loads((root / "annotations.json").read_text(encoding="utf-8"))
    stats = Counter()
    stats["total"] = len(truth)
    specs: list[dict] = []
    for name, t in sorted(truth.items()):
        if "tonic" not in t:
            stats["sans_etiquette"] += 1
            continue
        if t.get("modulate_at"):
            stats["modulants"] += 1
            continue
        # KK ne connaît que maj/min : sur un mode ecclésiastique, seule la
        # tonique est comparable.
        mode = t["mode"] if t["mode"] in ("maj", "min") else None
        specs.append({"path": root / name, "fichier": name, "kit": t["form"],
                      "role": t["mode"], "source": "annotations",
                      "vrai_pc": t["tonic"], "vrai_mode": mode})
    return specs, stats


# ──────────────────────────────────────────────
# Mesure
# ──────────────────────────────────────────────

def measure(specs: list[dict], stats: Counter) -> list[dict]:
    rows: list[dict] = []
    for spec in specs:
        try:
            md = parse_midi(spec["path"])
        except Exception:
            stats["illisibles"] += 1
            continue
        # Percussion non nommée : le canal 9 fait la loi, quel que soit le nom.
        if md.notes and sum(1 for n in md.notes if n.channel == 9) / len(md.notes) >= 0.8:
            stats["percussifs"] += 1
            continue
        if not any(n.channel != 9 for n in md.notes):
            stats["sans_note_tonale"] += 1
            continue
        true_pc, true_mode = spec["vrai_pc"], spec["vrai_mode"]
        row = {k: v for k, v in spec.items() if k != "path"}
        row["vrai"] = f"{PC_NAMES[true_pc]}{' ' + true_mode if true_mode else ''}"
        brut = hist_brut(md)
        row["n_pc"] = sum(1 for x in brut if x > 0)
        for nom, hist in (("brut", brut), ("pipeline", hist_pipeline(md))):
            pc, mode, corr, margin = estimate_key(hist)
            row[nom] = {
                "predit": f"{PC_NAMES[pc]} {mode}",
                "pc": pc,
                "mode": mode,
                "correlation": round(corr, 3),
                "marge": round(margin, 3),
                "erreur": error_kind(pc, mode, true_pc, true_mode),
                "hist": [round(x / sum(hist), 4) if sum(hist) else 0.0 for x in hist],
            }
        rows.append(row)
    return rows


def report(rows: list[dict], stats: Counter, titre: str, pool: bool = True) -> None:
    n = len(rows)
    with_mode = [r for r in rows if r["vrai_mode"]]

    print(f"\nCORPUS  {titre}")
    print(f"  {stats['total']} fichiers · {stats['sans_etiquette']} sans étiquette"
          f" · {stats['percussifs']} percussifs écartés"
          f" · {stats['illisibles']} illisibles"
          f" · {stats['sans_note_tonale']} sans note tonale"
          + (f" · {stats['modulants']} modulants écartés" if stats["modulants"] else ""))
    print(f"  → {n} fichiers mesurés, dont {len(with_mode)} avec mode étiqueté")
    par_source = Counter(r["source"] for r in rows)
    print(f"  étiquette lue dans : {dict(par_source)}")
    if stats["conflits"]:
        print(f"  {stats['conflits']} fichiers portent une fondamentale locale différente"
              f" de la tonalité du kit (nommage par accord, pas par tonalité)")

    for est in ("brut", "pipeline"):
        kinds = Counter(r[est]["erreur"] for r in rows)
        exact_root = sum(1 for r in rows if r[est]["pc"] == r["vrai_pc"])
        exact_full = sum(1 for r in with_mode if r[est]["erreur"] == "exact")
        # macro : un kit = une voix, les fichiers d'un kit ne sont pas indépendants
        par_kit: dict[str, list[bool]] = defaultdict(list)
        for r in rows:
            par_kit[r["kit"]].append(r[est]["pc"] == r["vrai_pc"])
        macro = sum(sum(v) / len(v) for v in par_kit.values()) / len(par_kit)

        print(f"\nESTIMATEUR « {est} »")
        print(f"  tonique juste          {pct(exact_root, n)}  ({exact_root}/{n})")
        print(f"  tonique+mode justes    {pct(exact_full, len(with_mode))}"
              f"  ({exact_full}/{len(with_mode)})")
        print(f"  macro par kit          {macro:.3f}  ({len(par_kit)} kits)")
        print("  nature des désaccords  " + " · ".join(
            f"{k} {v}" for k, v in kinds.most_common()))

        # La marge KK est-elle un indicateur ? Exactitude par tranche.
        print("  marge KK →  seuil   couverture   tonique juste")
        for seuil in (0.0, 0.05, 0.10, 0.15, 0.20):
            sel = [r for r in rows if r[est]["marge"] >= seuil]
            if not sel:
                continue
            ok = sum(1 for r in sel if r[est]["pc"] == r["vrai_pc"])
            print(f"              {seuil:>5.2f}   {len(sel) / n:>8.2f}   {pct(ok, len(sel))}")

        par_role: dict[str, list[bool]] = defaultdict(list)
        for r in rows:
            par_role[r["role"]].append(r[est]["pc"] == r["vrai_pc"])
        print("  par rôle  " + " · ".join(
            f"{role} {sum(v)}/{len(v)}"
            for role, v in sorted(par_role.items(), key=lambda kv: -len(kv[1]))[:10]))

        # Mise en commun par kit : chaque boucle vote avec un histogramme
        # normalisé (sinon la plus longue décide seule). C'est la parade
        # évidente au manque de matière d'une boucle isolée — reste à savoir
        # si elle paie.
        pooled: dict[str, list[float]] = defaultdict(lambda: [0.0] * 12)
        kit_true: dict[str, Counter] = defaultdict(Counter)
        for r in rows if pool else []:
            for i, x in enumerate(r[est]["hist"]):
                pooled[r["kit"]][i] += x
            kit_true[r["kit"]][(r["vrai_pc"], r["vrai_mode"])] += 1
        if pooled:
            ok = sum(1 for k, h in pooled.items()
                     if estimate_key(h)[0] == kit_true[k].most_common(1)[0][0][0])
            print(f"  kit mis en commun      {pct(ok, len(pooled))}  ({ok}/{len(pooled)})")

    # Kits où le moteur contredit l'étiquette en bloc : soit la boucle est
    # ambiguë, soit l'étiquette est fausse. Les deux méritent un œil.
    print("\nKITS EN DÉSACCORD TOTAL (brut, ≥ 3 fichiers)")
    par_kit_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        par_kit_rows[r["kit"]].append(r)
    shown = 0
    for kit, rs in sorted(par_kit_rows.items()):
        if len(rs) < 3 or any(r["brut"]["pc"] == r["vrai_pc"] for r in rs):
            continue
        labels = Counter(r["vrai"] for r in rs)
        preds = Counter(r["brut"]["predit"] for r in rs)
        print(f"  {kit}  étiqueté {dict(labels)} → {dict(preds)}")
        shown += 1
        if shown >= 12:
            print("  …")
            break
    if not shown:
        print("  aucun")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("racine", help="dossier de packs MIDI (étiquettes dans les noms)")
    ap.add_argument("--controle", metavar="DIR",
                    help="corpus make_corpus (annotations.json) en contrôle positif")
    ap.add_argument("--json", dest="json_out", help="rapport détaillé en JSON")
    ap.add_argument("--limit", type=int, default=0, help="n premiers fichiers (debug)")
    args = ap.parse_args(argv)

    sorties = {}
    if args.controle:
        cdir = Path(args.controle).expanduser().resolve()
        specs, stats = collect_control(cdir)
        rows = measure(specs, stats)
        if rows:
            # pas de mise en commun ici : les « kits » du contrôle sont des
            # formes, pas des familles tonales — les additionner n'a pas de sens.
            report(rows, stats, f"{cdir}  (contrôle positif, tonalité écrite)", pool=False)
            sorties["controle"] = {"racine": str(cdir), "lignes": rows}
        else:
            print(f"Contrôle ignoré : pas de tonalité dans {cdir}/annotations.json "
                  f"(corpus généré avant l'ajout du champ — le régénérer).")

    root = Path(args.racine).expanduser().resolve()
    specs, stats = collect_pack(root, args.limit)
    rows = measure(specs, stats)
    if not rows:
        print("Aucun fichier étiqueté exploitable.")
        return 1
    report(rows, stats, f"{root}  (packs, étiquette humaine)")
    sorties["packs"] = {"racine": str(root), "lignes": rows}

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(sorties, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nrapport JSON → {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
