"""
Constitue un corpus d'interprétations humaines réelles depuis MAESTRO v3.

Pourquoi ce script existe
-------------------------
Quatre sessions d'écoute ont clos une question : le corpus GÉNÉRÉ ne produit
pas de dynamique musicale. Ses vélocités sont des motifs mécaniques dont la
destruction est au pire neutre, au mieux perçue comme une amélioration
(l'aléa s'entend comme une humanisation). Les axes 26/28 ne sont pas
réfutés — ils sont intestables sur cette matière. La seule voie restante :
des interprétations où la dynamique est une INTENTION. MAESTRO en fournit
~1 200, capturées au Disklavier lors de l'International Piano-e-Competition
— chaque vélocité est le geste réel d'un pianiste, pas une rampe de
générateur.

Source : https://magenta.tensorflow.org/datasets/maestro (CC BY-NC-SA 4.0,
Hawthorne et al. 2019). Le zip MIDI (~58 Mo) est mis en cache dans
`.cache/`, jamais commité ; le corpus extrait non plus (reproductible :
même graine, mêmes extraits).

Choix méthodologiques, fixés AVANT toute écoute
-----------------------------------------------
- **Extraits, pas morceaux entiers** : une interprétation dure 10-30 min,
  une paire d'écoute demande ~50 s. La fenêtre démarre à 30 % de la durée
  (passé l'exposition, avant la coda), glisse de +12 s si elle tombe sur un
  creux (< 60 notes) — règle déterministe, AUCUNE sélection sur la variance
  de vélocité : choisir les passages « les plus dynamiques » fabriquerait
  le résultat qu'on prétend tester.
- **Un extrait par interprétation, compositeurs en tourniquet** : le lot
  couvre le plus de compositeurs possible plutôt que 24 Chopin.
- **La pédale voyage avec l'extrait** : l'état CC64/66/67 au début de
  fenêtre est réémis au tick 0 (voir `slice_mididata`), et les dégradations
  ne touchent jamais aux contrôleurs — sinon l'oreille jugerait la pédale,
  pas la dynamique.

Usage :
    python3 examples/fetch_maestro.py            # télécharge + 24 extraits
    python3 examples/fetch_maestro.py --n 24 --dur 50 --seed 1
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import (  # noqa: E402
    parse_midi_bytes,
    slice_mididata,
    tick_to_seconds,
    write_mididata,
)

MAESTRO_URL = ("https://storage.googleapis.com/magentadata/datasets/"
               "maestro/v3.0.0/maestro-v3.0.0-midi.zip")
MIN_NOTES = 60          # en dessous, la fenêtre est un creux : on glisse
SLIDE_S = 12.0          # pas de glissement
MAX_SLIDES = 3          # puis on renonce à cette interprétation


def _slug(text: str, max_len: int = 20) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = "".join(c if c.isalnum() else "-" for c in ascii_.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:max_len].strip("-") or "x"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done % (5 << 20) < (1 << 20):
                    print(f"  … {done / 1e6:.0f} / {total / 1e6:.0f} Mo",
                          flush=True)
    tmp.rename(dest)
    print(f"téléchargé : {dest} ({dest.stat().st_size / 1e6:.1f} Mo)")


def pick_spread(rows: list[dict], n: int, rng: random.Random,
                min_duration: float) -> list[dict]:
    """n interprétations, compositeurs en tourniquet, ordre déterministe."""
    eligible = [r for r in rows if float(r["duration"]) >= min_duration]
    by_composer: dict[str, list[dict]] = {}
    for r in eligible:
        by_composer.setdefault(r["canonical_composer"], []).append(r)
    for group in by_composer.values():
        group.sort(key=lambda r: r["midi_filename"])
        rng.shuffle(group)
    order = sorted(by_composer)
    rng.shuffle(order)
    picked: list[dict] = []
    while len(picked) < n and any(by_composer.values()):
        for composer in order:
            if by_composer[composer]:
                picked.append(by_composer[composer].pop())
                if len(picked) == n:
                    break
    return picked


def extract_one(zf: zipfile.ZipFile, row: dict, root: str,
                dur_s: float) -> tuple:
    """(MidiData de l'extrait, start_s) ou (None, raison)."""
    raw = zf.read(f"{root}{row['midi_filename']}")
    md = parse_midi_bytes(raw, origin=row["midi_filename"])
    total_s = tick_to_seconds(md, md.end_tick)
    if total_s < dur_s + 20:
        return None, "trop court"
    start = min(0.30 * total_s, total_s - dur_s - 10)
    for _ in range(MAX_SLIDES + 1):
        excerpt = slice_mididata(md, start, dur_s)
        if len(excerpt.notes) >= MIN_NOTES:
            return excerpt, start
        start += SLIDE_S
        if start + dur_s > total_s - 5:
            break
    return None, "que des creux"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--out", default="corpus_humain", type=Path)
    ap.add_argument("--zip", default=Path(".cache/maestro-v3.0.0-midi.zip"),
                    type=Path, dest="zip_path")
    ap.add_argument("--n", default=24, type=int)
    ap.add_argument("--dur", default=50.0, type=float,
                    help="durée d'extrait en secondes")
    ap.add_argument("--seed", default=1, type=int)
    args = ap.parse_args()

    if not args.zip_path.exists():
        print(f"téléchargement de {MAESTRO_URL} (~58 Mo)…")
        download(MAESTRO_URL, args.zip_path)

    rng = random.Random(args.seed)
    with zipfile.ZipFile(args.zip_path) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        root = csv_name.rsplit("/", 1)[0] + "/" if "/" in csv_name else ""
        rows = list(csv.DictReader(io.TextIOWrapper(zf.open(csv_name),
                                                    encoding="utf-8")))
        # marge : extrait + glissements possibles
        candidates = pick_spread(rows, args.n * 2, rng,
                                 min_duration=args.dur + 20)

        args.out.mkdir(parents=True, exist_ok=True)
        manifest, kept = [], 0
        for row in candidates:
            if kept == args.n:
                break
            excerpt, info = extract_one(zf, row, root, args.dur)
            if excerpt is None:
                print(f"  écarté ({info}) : {row['midi_filename']}")
                continue
            vels = [n.velocity for n in excerpt.notes]
            mean = sum(vels) / len(vels)
            std = (sum((v - mean) ** 2 for v in vels) / len(vels)) ** 0.5
            name = (f"{row['year']}_{_slug(row['canonical_composer'])}_"
                    f"{kept:02d}.mid")
            write_mididata(args.out / name, excerpt)
            manifest.append({
                "file": name,
                "composer": row["canonical_composer"],
                "title": row["canonical_title"],
                "source": row["midi_filename"],
                "start_s": round(info, 1),
                "dur_s": args.dur,
                "notes": len(excerpt.notes),
                "cc_events": len(excerpt.controls),
                "vel_mean": round(mean, 1),
                "vel_std": round(std, 1),
            })
            kept += 1

    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    composers = {m["composer"] for m in manifest}
    stds = [m["vel_std"] for m in manifest]
    print(f"\ncorpus : {kept} extraits de {args.dur:.0f} s, "
          f"{len(composers)} compositeurs, graine {args.seed}")
    print(f"vélocités : écart-type moyen {sum(stds) / len(stds):.1f} "
          f"(min {min(stds):.1f}) — la matière que le générateur n'avait pas")
    low = [m["file"] for m in manifest if m["vel_std"] < 8]
    if low:
        print(f"⚠ dispersion faible (< 8) sur : {', '.join(low)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
