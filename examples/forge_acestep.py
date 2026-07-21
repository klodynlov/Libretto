"""
Forge × ACE-Step — trier les prises d'un modèle AUDIO local, d'un trait.

Ce script est le RACCOURCI qui enchaîne les deux étapes de la chaîne audio :

    1. `audio2midi.py`  — conversion par stems (Demucs + basic-pitch),
                          le coût lourd : ~1-3 min par prise sur CPU ;
    2. `forge_from_dir` — gate de fiabilité, classement fiabilité-d'abord,
                          `--axes`, `--shortlist` : quelques millisecondes.

Si vous comptez rejouer la sélection (autres gates, autres modes, sweep),
payez la conversion UNE fois avec `audio2midi.py` puis itérez avec
`forge --from-dir` — les détails de la chaîne (stems, canaux, tempo) sont
documentés là-bas :

    python3 examples/audio2midi.py prises/ midi/
    python3 examples/forge.py sortie/ --from-dir midi/ --axes --shortlist 5

⚠ LE PIÈGE DU TRANSCRIT — à lire avant de faire confiance aux chiffres
----------------------------------------------------------------------
Mesuré sur un aller-retour contrôlé (voir
`examples/transcription_roundtrip.py`, le harnais rejouable) : la
transcription fait chuter le score SMS (~−0.18 en moyenne sur de l'audio
*propre* — un mix réel fera pire), ne préserve le classement que
partiellement (Spearman +0.1 à +0.6 : le gagnant vrai peut ressortir 5ᵉ),
et — le piège — **la fiabilité ne détecte rien** : elle peut même MONTER
après transcription, parce que basic-pitch produit beaucoup de notes et que
la confiance mesure la quantité de matière, pas sa provenance. Le gate
`--min-confidence` ne protège donc PAS d'une mauvaise transcription.

Conséquence pratique : utilisez ce pipeline en TRIAGE GROSSIER, pas en juge
fin. Écarter le tiers du bas est fiable ; couronner le n°1 ne l'est pas.
D'où le défaut `--shortlist 5` : Forge réduit ce que vos oreilles doivent
écouter, il ne les remplace pas.

Dépendances — OPTIONNELLES, hors contrat stdlib
-----------------------------------------------
    pip install demucs basic-pitch "numpy<2"

Usage
-----
    python3 examples/forge_acestep.py prises/ sortie/
        [--model htdemucs] [--no-drums] [--keep-work]
        [--min-confidence 0.55] [--min-score 0.0]
        [--axes] [--shortlist 5]

`prises/` contient les fichiers audio des N prises (.wav/.mp3/.flac/.ogg).
Sortie : `sortie/candidates/*.mid` (les transcriptions, conservées),
`sortie/forge_winner.mid`, `sortie/forge_short_XX.mid`,
`sortie/forge_report.json`. Code retour 2 si aucun candidat fiable, 3 si
dépendance manquante.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio2midi import PIP_HINT, check_deps, convert_dir  # noqa: E402

from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, forge_from_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_acestep",
        description="Forge × ACE-Step — audio2midi (Demucs + basic-pitch "
                    "par stems) puis sélection Libretto, d'un trait. "
                    "TRIAGE GROSSIER : voir « le piège du transcrit » dans "
                    "la docstring.")
    parser.add_argument("takes_dir", help="dossier des prises audio")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("--model", default="htdemucs",
                        help="modèle Demucs (défaut htdemucs)")
    parser.add_argument("--no-drums", action="store_true",
                        help="ne pas transcrire les onsets de batterie (canal 9)")
    parser.add_argument("--keep-work", action="store_true",
                        help="conserver les stems séparés dans sortie/stems "
                             "(défaut : effacés)")
    parser.add_argument("--min-confidence", type=float, default=0.55,
                        help="fiabilité minimale pour concourir (défaut 0.55 — "
                             "rappel : elle ne détecte PAS les artefacts de "
                             "transcription)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=5, metavar="K",
                        help="K candidats sous contrainte de diversité "
                             "(défaut 5 : sur du transcrit, on livre une "
                             "shortlist aux oreilles, pas un verdict)")
    args = parser.parse_args(argv)

    missing = check_deps()
    if missing:
        print(f"forge_acestep : dépendance manquante ({missing}).\n{PIP_HINT}",
              file=sys.stderr)
        return 3

    out = Path(args.out_dir)
    converted = convert_dir(
        args.takes_dir, out / "candidates", model=args.model,
        with_drums=not args.no_drums,
        work_dir=(out / "stems") if args.keep_work else None)
    if not converted:
        print(f"forge_acestep : aucune prise audio dans {args.takes_dir}",
              file=sys.stderr)
        return 2

    report = forge_from_dir(out / "candidates", out,
                            min_confidence=args.min_confidence,
                            min_score=args.min_score,
                            axes_report=args.axes, shortlist=args.shortlist)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    print("\n⚠ Rappel : classement sur MIDI TRANSCRIT — triage grossier, "
          "pas verdict fin. La fiabilité affichée ne voit pas les artefacts "
          "de transcription (docstring, « le piège du transcrit »). "
          "Écoutez la shortlist.")
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
