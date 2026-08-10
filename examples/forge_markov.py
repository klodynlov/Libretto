"""
Forge × markov_gen — un générateur APPRIS branché sur le juge, tout stdlib.

`forge.py` sélectionne le candidat le mieux construit parmi N ébauches ; sa
brique génératrice par défaut (`make_corpus`) est procédurale, écrite à la
main. Ce script la remplace par **markov_gen** — une chaîne de Markov
*entraînée sur un corpus MIDI* (voir `markov_gen.py`). Contrairement à
`forge_musiclang` (transformer, poids Hugging Face) et `forge_acestep`
(audio, Demucs + basic-pitch), il ne demande aucune dépendance ni réseau :
c'est la même chaîne « modèle → MIDI → Libretto → sélection », mais qui
tourne — et se teste — partout.

    ┌────────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
    │ markov_gen │ → │  → MIDI  │ → │  LIBRETTO  │ → │ sélection│
    │ (appris)   │   │  (.mid)  │   │ SMS + fiab.│   │ du meilleur
    └────────────┘   └──────────┘   └────────────┘   └──────────┘
         ▲
    corpus .mid (MAESTRO, vos morceaux, ou make_corpus)

Le contrat ne bouge pas : on génère N candidats déterministes (graine ×
index, exactement comme forge_musiclang), on les écrit dans
`sortie/candidates/`, et `forge_from_dir` fait le reste — gate de fiabilité,
classement fiabilité-d'abord, `--axes`, `--shortlist`.

À quoi s'attendre, honnêtement
------------------------------
La chaîne apprend l'idiome LOCAL du corpus (intervalles, rythme, voicings
verticaux réels) mais pas la forme longue : attendez des scores de forme
plus faibles que sur make_corpus, et un gate `--min-confidence` qui peut
recaler des candidats. C'est le juge qui fait son travail sur un matériau
sans architecture — une information, pas un bug (cf. `markov_gen`).

Usage
-----
    python3 examples/forge_markov.py CORPUS/ sortie/ [n=12] [seed=1]
        [--bars 24] [--order 2] [--tonic PC]
        [--min-confidence 0.55] [--min-score 0.0] [--axes] [--shortlist K]

CORPUS/ : dossier de fichiers .mid dont le modèle apprend (récursif).
Sortie : `sortie/candidates/*.mid` (conservés), `sortie/forge_winner.mid`,
`sortie/forge_report.json` — le même contrat que forge.py. Code retour 2 si
aucun candidat fiable, 4 si le corpus est vide ou illisible.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.midi import write_midi  # noqa: E402

import markov_gen  # noqa: E402
from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, forge_from_dir)


def _corpus_paths(corpus_dir: Path) -> list[Path]:
    return sorted(p for p in corpus_dir.rglob("*")
                  if p.suffix.lower() in (".mid", ".midi"))


def generate_candidates(model: markov_gen.Model, out_dir: Path, n: int,
                        seed: int, bars: int, tonic: int | None) -> list[Path]:
    """Génère n candidats MIDI, déterministes par (graine, index) — le tirage
    k ne dépend ni de l'ordre ni des voisins, exactement comme forge.py."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        rng = random.Random(seed * 10_000 + i)
        tracks, bpm = markov_gen.generate_one(model, rng, bars=bars, tonic=tonic)
        path = out_dir / f"candidate_{i:03d}.mid"
        if not tracks:
            continue                        # modèle trop pauvre pour cette graine
        write_midi(path, tracks, ppq=480, bpm=bpm, time_sig=(4, 4))
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_markov",
        description="Forge × markov_gen — entraîne une chaîne de Markov sur un "
                    "corpus MIDI, génère N candidats, puis sélection Libretto "
                    "fiabilité-d'abord. 100 % stdlib.")
    parser.add_argument("corpus", help="dossier de fichiers .mid pour l'entraînement")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=12,
                        help="nombre de candidats (défaut 12)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--bars", type=int, default=24,
                        help="longueur générée en mesures 4/4 (défaut 24)")
    parser.add_argument("--order", type=int, default=markov_gen.ORDER,
                        help=f"ordre de la chaîne sur les intervalles "
                             f"(défaut {markov_gen.ORDER})")
    parser.add_argument("--tonic", type=int, default=None, metavar="PC",
                        help="imposer la tonique 0-11 (défaut : tirée au hasard "
                             "par candidat)")
    parser.add_argument("--min-confidence", type=float, default=0.55,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=0, metavar="K",
                        help="K candidats sous contrainte de diversité")
    args = parser.parse_args(argv)

    markov_gen.ORDER = max(1, args.order)

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"forge_markov : corpus introuvable : {corpus}", file=sys.stderr)
        return 4
    paths = _corpus_paths(corpus)
    if not paths:
        print(f"forge_markov : aucun .mid dans {corpus}", file=sys.stderr)
        return 4

    model, skipped = markov_gen.train_from_paths(paths)
    if not model.tracks or model.n_files == 0:
        print(f"forge_markov : corpus illisible ({skipped} fichiers ignorés, "
              f"aucune matière apprise)", file=sys.stderr)
        return 4
    print(f"appris sur {model.n_files} fichier(s)"
          + (f" ({skipped} ignoré(s))" if skipped else "")
          + f" : {len(model.tracks)} piste(s), "
          f"{sum(t.n_onsets for t in model.tracks.values())} attaques",
          file=sys.stderr)

    out = Path(args.out_dir)
    cand = generate_candidates(model, out / "candidates", args.n, args.seed,
                               args.bars, args.tonic)
    if not cand:
        print("forge_markov : le modèle n'a produit aucun candidat "
              "(corpus trop pauvre — plus de fichiers, ou --bars plus grand ?)",
              file=sys.stderr)
        return 2
    print(f"généré {len(cand)} candidat(s) → {out / 'candidates'}", file=sys.stderr)

    report = forge_from_dir(out / "candidates", out,
                            min_confidence=args.min_confidence,
                            min_score=args.min_score,
                            axes_report=args.axes, shortlist=args.shortlist)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
