"""
Forge × MusicLang — un VRAI modèle génératif branché sur le juge.

Ce que c'est
------------
`forge.py` sélectionne le candidat le mieux construit parmi N ébauches ; sa
brique génératrice par défaut (`make_corpus`) est un bouche-trou procédural
assumé. Ce script la remplace par **MusicLang** (`musiclang/musiclang-v2`),
un transformer symbolique open source qui génère du MIDI multi-pistes — la
première incarnation du schéma annoncé dans forge.py :

    ┌───────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
    │ MusicLang │ → │  → MIDI  │ → │  LIBRETTO  │ → │ sélection│
    │ (modèle)  │   │  (.mid)  │   │ SMS + fiab.│   │ du meilleur
    └───────────┘   └──────────┘   └────────────┘   └──────────┘

Le contrat ne bouge pas : Forge ne parle que MIDI. On génère N candidats
(déterministes : `rng_seed` dérivé de graine × index), on les écrit dans
`sortie/candidates/`, et on laisse `forge_from_dir` faire exactement ce
qu'il fait pour toute autre source — gate de fiabilité, classement
fiabilité-d'abord, `--axes`, `--shortlist`. La « forme » des candidats est
la signature de sections jugée par Libretto (le modèle ne déclare pas la
sienne).

Dépendances — OPTIONNELLES, hors contrat stdlib
-----------------------------------------------
Le cœur de Libretto reste 100 % stdlib. CE script, comme `fetch_maestro.py`
pour les données, vit à la frontière et demande :

    pip install musiclang-predict "numpy<2"

(le pin numpy<2 contourne une incompatibilité binaire pandas/numpy 2 dans
les dépendances de musiclang au moment d'écrire ces lignes). Au premier
lancement, les poids du modèle (~quelques centaines de Mo) sont téléchargés
depuis Hugging Face — il faut donc un accès réseau à huggingface.co.
Comptez ensuite de l'ordre de 30 s à 2 min PAR CANDIDAT sur CPU selon
`--nb-tokens` : un vrai modèle se paie, c'est pour ça que les défauts sont
n=8 et non n=24.

Usage
-----
    python3 examples/forge_musiclang.py sortie/ [n=8] [seed=1]
        [--nb-tokens 1024] [--temperature 0.9] [--tempo 110]
        [--min-confidence 0.55] [--min-score 0.0]
        [--axes] [--shortlist K]

Sortie : `sortie/candidates/*.mid` (les N générations, conservées),
`sortie/forge_winner.mid`, `sortie/forge_report.json` — le même contrat que
forge.py. Code retour 2 si aucun candidat fiable, 3 si dépendance manquante.

À quoi s'attendre, honnêtement
------------------------------
MusicLang génère des textures harmoniques cohérentes mais pas des chansons
avec couplets/refrains — précisément le trou que Libretto mesure. Attendez
des fiabilités plus basses et des scores de forme plus faibles que sur
make_corpus : ce n'est pas un échec du branchement, c'est le juge qui fait
son travail sur du matériau qui n'a pas de forme longue. Le gate
`--min-confidence` peut recaler beaucoup de candidats ; c'est une
information, pas un bug.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, forge_from_dir)

MODEL_ID = "musiclang/musiclang-v2"


def generate_candidates(out_dir: Path, n: int, seed: int, nb_tokens: int,
                        temperature: float, tempo: int) -> list[Path]:
    """Génère n candidats MIDI avec MusicLang, déterministes par
    (graine, index). Import local : le module doit rester importable sans
    la dépendance (les tests du dépôt n'installent pas musiclang)."""
    from musiclang_predict import MusicLangPredictor

    print(f"chargement de {MODEL_ID} (premier lancement : téléchargement "
          f"des poids depuis Hugging Face)…", file=sys.stderr)
    predictor = MusicLangPredictor(MODEL_ID)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        # Une graine par candidat, dérivée comme dans forge.py : le tirage
        # k ne dépend ni de l'ordre ni des voisins.
        rng_seed = seed * 10_000 + i
        t0 = time.perf_counter()
        score = predictor.predict(nb_tokens=nb_tokens, temperature=temperature,
                                  topp=1.0, rng_seed=rng_seed)
        path = out_dir / f"candidate_{i:03d}.mid"
        score.to_midi(str(path), tempo=tempo, time_signature=(4, 4))
        print(f"  candidat {i + 1}/{n} généré en "
              f"{time.perf_counter() - t0:.1f}s → {path.name}", file=sys.stderr)
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_musiclang",
        description="Forge × MusicLang — génère N candidats avec un vrai "
                    "modèle symbolique, puis sélection Libretto "
                    "fiabilité-d'abord.")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=8,
                        help="nombre de candidats (défaut 8 — un vrai modèle "
                             "se paie en temps CPU)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--nb-tokens", type=int, default=1024,
                        help="longueur de génération par candidat (défaut 1024)")
    parser.add_argument("--temperature", type=float, default=0.9,
                        help="température d'échantillonnage (défaut 0.9)")
    parser.add_argument("--tempo", type=int, default=110,
                        help="tempo d'écriture MIDI (défaut 110)")
    parser.add_argument("--min-confidence", type=float, default=0.55,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=0, metavar="K",
                        help="K candidats sous contrainte de diversité")
    args = parser.parse_args(argv)

    try:
        import musiclang_predict  # noqa: F401
    except ImportError as exc:
        print(f"forge_musiclang : dépendance manquante ({exc}).\n"
              f"  pip install musiclang-predict \"numpy<2\"\n"
              f"Le cœur de Libretto reste 100 % stdlib : cette dépendance "
              f"n'est requise que par ce pont.", file=sys.stderr)
        return 3

    out = Path(args.out_dir)
    cand_dir = out / "candidates"
    generate_candidates(cand_dir, args.n, args.seed,
                        args.nb_tokens, args.temperature, args.tempo)

    report = forge_from_dir(cand_dir, out,
                            min_confidence=args.min_confidence,
                            min_score=args.min_score,
                            axes_report=args.axes, shortlist=args.shortlist)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
