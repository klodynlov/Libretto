"""
Forge → bibliothèque : verser les livrables d'un run Forge dans l'index
cherchable par intention, en imposant ce que Forge connaît déjà.

Le chaînon
----------
`forge.py` (et ses variantes loops/musiclang/acestep) écrit un gagnant, une
shortlist diverse et un `forge_report.json`. Ce rapport porte, pour chaque
candidat retenu, la tonalité, le mode, le tempo et la longueur **demandés**
— pas estimés. Les reverser dans la bibliothèque, c'est donner à
`library add` des métadonnées sûres (`key_source="override"`) au lieu de les
laisser deviner à l'estimateur, dont on connaît la réserve sur les séquences
courtes (voir README, *Tonalité*).

    forge … sortie/               ──►  forge_winner.mid + forge_short_XX.mid
                                        + forge_report.json (tonic/mode/bpm/bars)
                                              │
    python3 examples/forge_library.py sortie/ --lib lib.json
                                              │
                                              ▼
                          index enrichi, cherchable par émotion

    python3 -m libretto.cli library search "mélancolique 8 mesures ~90 bpm" --lib lib.json

Ce qui est versé
----------------
Par défaut, les seuls fichiers que Forge garantit sur le disque : le
**gagnant** (`forge_winner.mid`) et la **shortlist** (`forge_short_XX.mid`).
Sans `--keep-all` côté Forge, les brouillons non retenus sont effacés ;
`--all` ici ne les récupère donc que si le run Forge les a conservés.

Le gagnant est souvent aussi la première entrée de la shortlist : on le
dédoublonne par l'index du candidat, pour ne pas indexer deux fois la même
matière sous deux chemins. Chaque entrée reçoit les étiquettes `forge` et
`forge:<rôle>` (winner / shortlist / leaderboard), en plus de celles passées
en `--tag`.

Rien n'est copié hors de `sortie/` : l'index ne retient que des chemins.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.library import Library, analyze_entry  # noqa: E402


def _collect(report: dict, base: Path, include_all: bool) -> list[tuple[Path, dict, str]]:
    """(chemin sur disque, métadonnées Forge, rôle) pour chaque livrable
    présent. Dédoublonné par index de candidat — le gagnant ne réapparaît
    pas via la shortlist."""
    items: list[tuple[Path, dict, str]] = []
    seen: set = set()

    winner, wfile = report.get("winner"), report.get("winner_file")
    if winner and wfile:
        p = base / wfile
        if p.exists():
            items.append((p, winner, "winner"))
            seen.add(winner.get("index"))

    shortlist = report.get("shortlist")
    if shortlist and shortlist.get("picks"):
        for pick in shortlist["picks"]:
            if pick.get("index") in seen:
                continue
            p = base / pick.get("file_out", "")
            if p.exists():
                items.append((p, pick, "shortlist"))
                seen.add(pick.get("index"))

    if include_all:
        for cand in report.get("leaderboard", []):
            if cand.get("index") in seen:
                continue
            p = base / cand.get("file", "")
            if p.exists():                       # présent seulement si Forge --keep-all
                items.append((p, cand, "leaderboard"))
                seen.add(cand.get("index"))

    return items


def ingest_forge_output(out_dir: str | Path, lib_path: str | Path, *,
                        tags: list[str] | None = None,
                        include_all: bool = False,
                        weights: dict[str, float] | None = None) -> dict:
    """Verse les livrables d'un run Forge dans la bibliothèque. Accepte un
    dossier de sortie Forge ou directement un `forge_report.json`."""
    out = Path(out_dir)
    report_path = out if out.suffix.lower() == ".json" else out / "forge_report.json"
    if not report_path.exists():
        raise ValueError(f"rapport Forge introuvable : {report_path} "
                         f"(lancez d'abord forge*.py)")
    base = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))

    items = _collect(report, base, include_all)
    lib = Library.load(lib_path)

    added = updated = failed = 0
    rows: list[tuple[Path, object, str]] = []
    for path, meta, role in items:
        entry_tags = list(tags or []) + ["forge", f"forge:{role}"]
        try:
            entry = analyze_entry(path, weights=weights,
                                  tonic=meta.get("tonic"), mode=meta.get("mode"),
                                  bpm=meta.get("bpm"), bars=meta.get("bars"),
                                  tags=entry_tags)
        except ValueError as exc:
            failed += 1
            rows.append((path, None, str(exc)))
            continue
        is_new = lib.add(entry)
        added += is_new
        updated += (not is_new)
        rows.append((path, entry, role))

    lib.save(lib_path)
    return {"added": added, "updated": updated, "failed": failed,
            "n_items": len(items), "rows": rows, "lib_path": str(lib_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verser les livrables d'un run Forge dans la bibliothèque "
                    "cherchable (émotion + 29 axes).")
    ap.add_argument("out_dir",
                    help="dossier de sortie Forge (ou un forge_report.json)")
    ap.add_argument("--lib", default="libretto_library.json",
                    help="fichier d'index (défaut libretto_library.json)")
    ap.add_argument("--tag", action="append", default=[],
                    help="étiquette libre supplémentaire (répétable)")
    ap.add_argument("--all", action="store_true", dest="include_all",
                    help="verser aussi tout le leaderboard (seulement les "
                         "brouillons conservés par Forge --keep-all)")
    ap.add_argument("--weights", metavar="JSON",
                    help="poids calibrés (sortie de `libretto calibrate`)")
    args = ap.parse_args(argv)

    weights = None
    if args.weights:
        from libretto.calibrate import load_weights
        try:
            weights = load_weights(args.weights)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"forge_library: poids invalides : {exc}", file=sys.stderr)
            return 1

    try:
        res = ingest_forge_output(args.out_dir, args.lib, tags=args.tag,
                                  include_all=args.include_all, weights=weights)
    except ValueError as exc:
        print(f"forge_library: {exc}", file=sys.stderr)
        return 1

    for path, entry, role in res["rows"]:
        if entry is None:
            print(f"  ✗ {Path(path).name} : {role}", file=sys.stderr)
            continue
        emo = entry.emotion
        print(f"  + {Path(path).name}  «{role}»  "
              f"[{entry.key or '?'} · {round(entry.bpm) if entry.bpm else '?'} BPM · "
              f"{entry.bars} mes.]  {', '.join(emo['descriptors'])}")
    print(f"bibliothèque : {res['added']} ajoutée(s), {res['updated']} "
          f"mise(s) à jour"
          + (f", {res['failed']} échec(s)" if res["failed"] else "")
          + f" sur {res['n_items']} livrable(s) → {res['lib_path']}",
          file=sys.stderr)
    if res["n_items"] == 0:
        print("forge_library: aucun livrable trouvé — le run Forge a-t-il "
              "produit un gagnant ? (gate de fiabilité)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
