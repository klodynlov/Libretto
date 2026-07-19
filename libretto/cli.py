"""
Libretto — CLI.

  libretto analyze chanson.mid                   rapport texte
  libretto analyze chanson.mid --html rapport.html --json rapport.json
  libretto analyze chanson.mid --min-score 0.5   gate CI (exit 2 si en dessous)
  libretto demo                                  analyse de la partition de démo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .axes import SenseOfMusicalStructure
from .builder import build_score
from .demo import demo_score
from .midi import parse_midi
from .report import render_html, render_json, render_text


def _run(sms: SenseOfMusicalStructure, args: argparse.Namespace, source: str) -> int:
    sms.calculate()
    if not args.quiet:
        print(render_text(sms))
    if args.json:
        Path(args.json).write_text(render_json(sms, source), encoding="utf-8")
        print(f"JSON  → {args.json}", file=sys.stderr)
    if args.html:
        Path(args.html).write_text(render_html(sms, source), encoding="utf-8")
        print(f"HTML  → {args.html}", file=sys.stderr)
    if args.min_score is not None and sms.get_score() < args.min_score:
        print(f"GATE: score {sms.get_score():.3f} < seuil {args.min_score}", file=sys.stderr)
        return 2
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", metavar="OUT", help="écrire le rapport JSON")
    p.add_argument("--html", metavar="OUT", help="écrire le rapport HTML")
    p.add_argument("--min-score", type=float, default=None,
                   help="gate : exit 2 si le score global est inférieur")
    p.add_argument("--quiet", action="store_true", help="pas de rapport texte sur stdout")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="libretto",
        description="Libretto — Sense of Musical Structure (29 axes structurels).")
    parser.add_argument("--version", action="version", version=f"libretto {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyser un fichier MIDI")
    p_analyze.add_argument("path", help="fichier .mid/.midi (SMF format 0 ou 1)")
    _add_common(p_analyze)

    p_demo = sub.add_parser("demo", help="analyser la partition de démonstration intégrée")
    _add_common(p_demo)

    p_serve = sub.add_parser("serve", help="interface web locale (drag & drop + Reaper)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=None,
                         help="défaut 8787, bascule auto si occupé ; 0 = port automatique")

    p_reaper = sub.add_parser("reaper", help="pousser un MIDI dans REAPER (pont Klody :9000) et jouer")
    p_reaper.add_argument("path", help="fichier .mid/.midi")
    p_reaper.add_argument("--no-play", action="store_true", help="pousser sans lancer la lecture")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import main as serve_main
        return serve_main(args.host, args.port)

    if args.command == "reaper":
        from .reaper import BridgeError, push_mididata
        path = Path(args.path)
        if not path.exists():
            print(f"libretto: fichier introuvable : {path}", file=sys.stderr)
            return 1
        try:
            result = push_mididata(parse_midi(path), play=not args.no_play)
        except (ValueError, BridgeError) as exc:
            print(f"libretto: {exc}", file=sys.stderr)
            return 1
        print(f"REAPER {result['reaper']} : {result['total_notes']} notes sur "
              f"{len(result['tracks'])} pistes, {result['markers']} marqueurs"
              + (", lecture lancée" if result["playing"] else ""))
        return 0

    if args.command == "demo":
        return _run(SenseOfMusicalStructure(demo_score()), args, "partition de démonstration")

    path = Path(args.path)
    if not path.exists():
        print(f"libretto: fichier introuvable : {path}", file=sys.stderr)
        return 1
    try:
        score = build_score(parse_midi(path))
    except ValueError as exc:
        print(f"libretto: {exc}", file=sys.stderr)
        return 1
    if not score.sections:
        print(f"libretto: aucune note exploitable dans {path}", file=sys.stderr)
        return 1
    return _run(SenseOfMusicalStructure(score), args, path.name)


if __name__ == "__main__":
    sys.exit(main())
