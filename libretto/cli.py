"""
Libretto — CLI.

  libretto analyze chanson.mid                   rapport texte
  libretto analyze chanson.mid --html rapport.html --json rapport.json
  libretto analyze chanson.mid --min-score 0.5   gate CI (exit 2 si en dessous)
  libretto analyze chanson.mid --weights w.json  poids calibrés
  libretto demo                                  analyse de la partition de démo
  libretto calibrate corpus/ --out w.json        calibration contrastive des poids
  libretto calibrate corpus/ --min-auc 0.45      gate : échoue si un axe s'inverse
  libretto library add seq.mid --lib lib.json    indexer une séquence (émotion + axes)
  libretto library search "mélancolique 8 mesures ~90 bpm" --lib lib.json
  libretto library search "planant" --lib lib.json --reaper   pousse le meilleur dans REAPER
"""

from __future__ import annotations

import argparse
import json
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
    # La fiabilité est vérifiée AVANT le score : un score qui passe le seuil
    # sans matière derrière n'est pas une réussite, c'est un chiffre en l'air.
    # Code 3 distinct, pour que la CI sache lequel des deux a lâché.
    if args.min_confidence is not None and sms.confidence() < args.min_confidence:
        print(f"GATE: fiabilité {sms.confidence():.3f} < seuil {args.min_confidence} "
              f"({sms.confidence_level()}) — {sms.diagnosis()}", file=sys.stderr)
        return 3
    if args.min_score is not None and sms.get_score() < args.min_score:
        print(f"GATE: score {sms.get_score():.3f} < seuil {args.min_score}", file=sys.stderr)
        return 2
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", metavar="OUT", help="écrire le rapport JSON")
    p.add_argument("--html", metavar="OUT", help="écrire le rapport HTML")
    p.add_argument("--min-score", type=float, default=None,
                   help="gate : exit 2 si le score global est inférieur")
    p.add_argument("--min-confidence", type=float, default=None,
                   help="gate : exit 3 si la fiabilité est inférieure (le score "
                        "ne repose pas sur assez de matière)")
    p.add_argument("--quiet", action="store_true", help="pas de rapport texte sur stdout")


def _library(args: argparse.Namespace) -> int:
    from .library import (Library, analyze_entry, parse_query, search,
                          tonic_to_pc)

    if args.lib_command == "add":
        weights = None
        if args.weights:
            from .calibrate import load_weights
            try:
                weights = load_weights(args.weights)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"libretto: poids invalides : {exc}", file=sys.stderr)
                return 1
        tonic = None
        if args.tonic:
            try:
                tonic = tonic_to_pc(args.tonic)
            except ValueError as exc:
                print(f"libretto: {exc}", file=sys.stderr)
                return 1
        lib = Library.load(args.lib)
        added, updated, failed = 0, 0, 0
        for raw in args.paths:
            path = Path(raw)
            if not path.exists():
                print(f"libretto: fichier introuvable : {path}", file=sys.stderr)
                failed += 1
                continue
            try:
                entry = analyze_entry(path, weights=weights, tonic=tonic,
                                      mode=args.mode, bpm=args.bpm, bars=args.bars,
                                      tags=args.tag)
            except ValueError as exc:
                print(f"libretto: {path.name}: {exc}", file=sys.stderr)
                failed += 1
                continue
            is_new = lib.add(entry)
            added += is_new
            updated += (not is_new)
            emo = entry.emotion
            print(f"  {'+ ' if is_new else '~ '}{path.name}  "
                  f"[{entry.key or '?'} · {round(entry.bpm) if entry.bpm else '?'} BPM · "
                  f"{entry.bars} mes.]  "
                  f"{', '.join(emo['descriptors'])}  "
                  f"(V {emo['valence']:.2f} · É {emo['energy']:.2f} · T {emo['tension']:.2f})")
        lib.save(args.lib)
        print(f"bibliothèque : {len(lib.entries)} séquences "
              f"({added} ajoutée(s), {updated} mise(s) à jour"
              + (f", {failed} échec(s)" if failed else "") + f") → {args.lib}",
              file=sys.stderr)
        return 1 if (failed and not added and not updated) else 0

    if args.lib_command == "search":
        lib = Library.load(args.lib)
        if not lib.entries:
            print(f"libretto: bibliothèque vide ou introuvable : {args.lib}",
                  file=sys.stderr)
            return 1
        q = parse_query(args.query, bpm_tol=args.bpm_tol, bars_tol=args.bars_tol)
        hits = search(lib.entries, args.query, limit=args.limit,
                      bpm_tol=args.bpm_tol, bars_tol=args.bars_tol)
        print(f"requête : {q.describe()}", file=sys.stderr)
        if not hits:
            print("aucune séquence ne satisfait les contraintes "
                  "(élargir --bpm-tol / --bars-tol ?)", file=sys.stderr)
            return 0
        for rank, h in enumerate(hits, 1):
            e = h.entry
            dist = f"  d={h.distance:.3f}" if h.distance is not None else ""
            mark = " ←" if (args.reaper and rank == args.pick) else ""
            print(f"{rank}. {e.path}{dist}{mark}")
            print(f"     {', '.join(h.reasons)}  ·  fiabilité {e.confidence_level} "
                  f"·  SMS {e.global_score:.2f}")
        if args.reaper:
            if args.pick > len(hits):
                print(f"libretto: --pick {args.pick} hors de portée "
                      f"({len(hits)} résultat(s))", file=sys.stderr)
                return 1
            chosen = hits[args.pick - 1].entry
            src = Path(chosen.path)
            if not src.exists():
                print(f"libretto: fichier introuvable pour l'envoi : {src} "
                      f"(déplacé depuis l'indexation ?)", file=sys.stderr)
                return 1
            from .midi import parse_midi
            from .reaper import BridgeError, push_mididata
            # Une piste nommée par l'intention trouvée : on retrouve la séquence
            # dans le projet sans deviner à quel «Libretto N» elle correspond.
            label = ", ".join(chosen.emotion["descriptors"][:2]) or src.stem
            try:
                res = push_mididata(parse_midi(src), track_names=[label],
                                    play=not args.no_play)
            except (ValueError, BridgeError) as exc:
                print(f"libretto: {exc}", file=sys.stderr)
                return 1
            print(f"→ REAPER {res['reaper']} : «{src.name}» poussé "
                  f"({res['total_notes']} notes, {len(res['tracks'])} piste(s))"
                  + (", lecture lancée" if res["playing"] else ""),
                  file=sys.stderr)
        if args.json:
            payload = [{"path": h.entry.path, "distance": h.distance,
                        "emotion": h.entry.emotion, "bpm": h.entry.bpm,
                        "bars": h.entry.bars, "key": h.entry.key,
                        "global_score": h.entry.global_score,
                        "confidence": h.entry.confidence,
                        "confidence_level": h.entry.confidence_level}
                       for h in hits]
            Path(args.json).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"JSON  → {args.json}", file=sys.stderr)
        return 0

    if args.lib_command == "list":
        lib = Library.load(args.lib)
        if not lib.entries:
            print(f"bibliothèque vide ou introuvable : {args.lib}", file=sys.stderr)
            return 0
        for e in lib.entries:
            emo = e.emotion
            tags = f"  #{' #'.join(e.tags)}" if e.tags else ""
            print(f"{Path(e.path).name}  [{e.key or '?'} · "
                  f"{round(e.bpm) if e.bpm else '?'} BPM · {e.bars} mes.]  "
                  f"{', '.join(emo['descriptors'])}{tags}")
        print(f"{len(lib.entries)} séquences", file=sys.stderr)
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="libretto",
        description="Libretto — Sense of Musical Structure (29 axes structurels).")
    parser.add_argument("--version", action="version", version=f"libretto {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyser un fichier MIDI")
    p_analyze.add_argument("path", help="fichier .mid/.midi (SMF format 0 ou 1)")
    p_analyze.add_argument("--weights", metavar="JSON",
                           help="poids calibrés (sortie de `libretto calibrate`)")
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

    p_cal = sub.add_parser(
        "calibrate",
        help="calibrer les poids des axes par contraste original/dégradé")
    p_cal.add_argument("corpus", help="dossier de fichiers .mid (positifs)")
    p_cal.add_argument("--out", metavar="JSON", default="weights.json",
                       help="fichier de poids en sortie (défaut weights.json)")
    p_cal.add_argument("--seed", type=int, default=42)
    p_cal.add_argument("--variants", type=int, default=2,
                       help="négatifs par dégradation et par fichier (défaut 2)")
    p_cal.add_argument("--iters", type=int, default=4000,
                       help="itérations de hill climbing (défaut 4000)")
    p_cal.add_argument("--jobs", type=int, default=1,
                       help="processus parallèles pour le précalcul du corpus")
    p_cal.add_argument("--lam", type=float, default=0.3,
                       help="régularisation vers les poids experts (défaut 0.3)")
    p_cal.add_argument("--folds", type=int, default=5,
                       help="plis de validation croisée par fichier (défaut 5)")
    p_cal.add_argument("--min-auc", type=float, default=None,
                       help="gate : exit 2 si un axe passe sous cette AUC "
                            "(0.5 = l'axe récompense la dégradation)")
    p_cal.add_argument("--min-test-accuracy", type=float, default=None,
                       help="gate : exit 2 si l'accuracy de validation croisée "
                            "est inférieure")
    p_cal.add_argument("--all-degradations", action="store_true",
                       help="calibrer aussi contre les dégradations que l'écoute "
                            "a réfutées (par défaut : seulement les quatre "
                            "validées par l'oreille)")

    p_ann = sub.add_parser(
        "annotate",
        help="écoute comparée A/B en aveugle : recueille des jugements humains")
    p_ann.add_argument("corpus",
                       help="dossier de fichiers .mid (paires original/dégradé), "
                            "ou fichier .json de duels entre deux morceaux "
                            "différents (voir examples/forge_duels.py)")
    p_ann.add_argument("--out", metavar="JSON", default="jugements.json",
                       help="fichier de jugements (repris s'il existe)")
    p_ann.add_argument("--host", default="127.0.0.1")
    p_ann.add_argument("--port", type=int, default=None, help="défaut 8788")
    p_ann.add_argument("--seed", type=int, default=1)
    p_ann.add_argument("--per-file", type=int, default=2,
                       help="dégradations comparées par fichier (défaut 2)")
    p_ann.add_argument("--render", choices=["synth", "instrument"],
                       default="synth",
                       help="synth = Web Audio (v2-timbre) ; instrument = "
                            "FluidSynth + SoundFont, vraies couches de "
                            "vélocité (v3)")
    p_ann.add_argument("--only", metavar="NOMS",
                       help="restreindre à certaines dégradations, séparées par "
                            "des virgules — pour une session ciblée sur celles "
                            "qui restent à trancher")

    p_agr = sub.add_parser(
        "agreement",
        help="dépouille les jugements : les dégradations s'entendent-elles, "
             "et le moteur est-il d'accord ? Deux fichiers = accord "
             "inter-annotateurs (κ de Cohen, verdict groupé)")
    p_agr.add_argument("judgements", nargs="+",
                       help="fichier(s) produit(s) par `annotate` — un seul : "
                            "dépouille classique ; deux : comparaison "
                            "inter-annotateurs sur le même lot")
    p_agr.add_argument("--corpus", help="dossier des .mid, pour comparer au moteur")
    p_agr.add_argument("--seed", type=int, default=1,
                       help="doit être celle utilisée pour l'annotation")
    p_agr.add_argument("--json", metavar="OUT", help="écrire le rapport JSON")

    p_lib = sub.add_parser(
        "library",
        help="bibliothèque de séquences cherchable par intention émotionnelle")
    lib_sub = p_lib.add_subparsers(dest="lib_command", required=True)

    p_lib_add = lib_sub.add_parser("add", help="indexer un ou plusieurs MIDI")
    p_lib_add.add_argument("paths", nargs="+", help="fichiers .mid/.midi")
    p_lib_add.add_argument("--lib", default="libretto_library.json",
                           help="fichier d'index (défaut libretto_library.json)")
    p_lib_add.add_argument("--weights", metavar="JSON",
                           help="poids calibrés (sortie de `libretto calibrate`)")
    p_lib_add.add_argument("--tonic", help="imposer la tonique (ex. D, F#, Bb) "
                                           "au lieu de l'estimer")
    p_lib_add.add_argument("--mode", choices=["maj", "min", "dorien", "mixolydien"],
                           help="imposer le mode au lieu de l'estimer")
    p_lib_add.add_argument("--bpm", type=float, help="imposer le tempo")
    p_lib_add.add_argument("--bars", type=int, help="imposer la longueur en mesures")
    p_lib_add.add_argument("--tag", action="append", default=[],
                           help="étiquette libre (répétable)")

    p_lib_search = lib_sub.add_parser("search", help="chercher par intention")
    p_lib_search.add_argument("query",
                              help="requête libre : « mélancolique 8 mesures "
                                   "~90 bpm Dm »")
    p_lib_search.add_argument("--lib", default="libretto_library.json",
                              help="fichier d'index")
    p_lib_search.add_argument("-n", "--limit", type=int, default=5,
                              help="nombre de résultats (défaut 5)")
    p_lib_search.add_argument("--bpm-tol", type=float, default=15.0,
                              help="tolérance de tempo en BPM (défaut 15)")
    p_lib_search.add_argument("--bars-tol", type=int, default=2,
                              help="tolérance de longueur en mesures (défaut 2)")
    p_lib_search.add_argument("--reaper", action="store_true",
                              help="pousser le meilleur résultat dans REAPER "
                                   "(pont Klody :9000) et jouer")
    p_lib_search.add_argument("--pick", type=int, default=1, metavar="N",
                              help="rang du résultat à envoyer avec --reaper "
                                   "(défaut 1 = le meilleur)")
    p_lib_search.add_argument("--no-play", action="store_true",
                              help="avec --reaper : pousser sans lancer la lecture")
    p_lib_search.add_argument("--json", metavar="OUT", help="écrire les résultats en JSON")

    p_lib_list = lib_sub.add_parser("list", help="lister le contenu de la bibliothèque")
    p_lib_list.add_argument("--lib", default="libretto_library.json",
                            help="fichier d'index")

    args = parser.parse_args(argv)

    if args.command == "library":
        return _library(args)

    if args.command == "annotate":
        from .annotate import main as annotate_main
        only = [x.strip() for x in args.only.split(",")] if args.only else None
        return annotate_main(args.corpus, args.out, args.host, args.port,
                             args.seed, args.per_file, only, args.render)

    if args.command == "agreement":
        from .agreement import (analyse, format_inter_report, format_report,
                                inter_annotator)
        if len(args.judgements) > 2:
            print("libretto: deux fichiers de jugements au plus", file=sys.stderr)
            return 1
        try:
            if len(args.judgements) == 2:
                report = inter_annotator(*args.judgements)
                print(format_inter_report(report))
            else:
                report = analyse(args.judgements[0], args.corpus, args.seed)
                print(format_report(report))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"libretto: {exc}", file=sys.stderr)
            return 1
        if args.json:
            Path(args.json).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"JSON  → {args.json}", file=sys.stderr)
        return 0

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

    if args.command == "calibrate":
        from .calibrate import run_calibration
        try:
            report = run_calibration(args.corpus, seed=args.seed,
                                     variants=args.variants, iters=args.iters,
                                     jobs=args.jobs, lam=args.lam,
                                     folds=args.folds,
                                     audible_only=not args.all_degradations)
        except ValueError as exc:
            print(f"libretto: {exc}", file=sys.stderr)
            return 1
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        b, a = report["before"], report["after"]
        print(f"corpus : {report['n_files']} fichiers, {report['n_pairs']} paires "
              f"original/dégradé ({report['skipped_noop']} négatifs no-op exclus)")
        print(f"entraînement : accuracy {b['accuracy']:.3f} → {a['accuracy']:.3f}   "
              f"marge {b['margin']:.3f} → {a['margin']:.3f}")
        val = report["validation"]
        if val.get("n_folds"):
            print(f"validation {val['n_folds']}-fold (par fichier) : accuracy test "
                  f"{val['test_accuracy_mean']:.3f} ± {val['test_accuracy_std']:.3f}   "
                  f"experts {val['expert_test_accuracy_mean']:.3f}   "
                  f"gain {val['gain_vs_expert']:+.3f}   "
                  f"surapprentissage {val['overfit_gap']:+.3f}")
        auc = sorted(report["axis_auc"].items(), key=lambda kv: kv[1])
        worst = [f"{aid} ({v:.2f})" for aid, v in auc if v < 0.5]
        if worst:
            print("axes sous AUC 0.5 (récompensent la dégradation) : " + ", ".join(worst))
        print("axes les plus discriminants : "
              + ", ".join(f"{aid} ({v:.2f})" for aid, v in auc[::-1][:5]))
        for w in report["warnings"]:
            print(f"⚠ {w}", file=sys.stderr)
        print(f"poids  → {args.out}", file=sys.stderr)
        rc = 0
        if args.min_auc is not None:
            below = [(aid, v) for aid, v in auc if v < args.min_auc]
            if below:
                print("GATE: axes sous AUC "
                      f"{args.min_auc} : "
                      + ", ".join(f"{aid} ({v:.3f})" for aid, v in below),
                      file=sys.stderr)
                rc = 2
        if args.min_test_accuracy is not None:
            got = val.get("test_accuracy_mean")
            if got is None:
                print("GATE: pas de validation croisée disponible (corpus trop petit)",
                      file=sys.stderr)
                rc = 2
            elif got < args.min_test_accuracy:
                print(f"GATE: accuracy de validation {got:.3f} < "
                      f"seuil {args.min_test_accuracy}", file=sys.stderr)
                rc = 2
        return rc

    if args.command == "demo":
        return _run(SenseOfMusicalStructure(demo_score()), args, "partition de démonstration")

    path = Path(args.path)
    if not path.exists():
        print(f"libretto: fichier introuvable : {path}", file=sys.stderr)
        return 1
    weights = None
    if args.weights:
        from .calibrate import load_weights
        try:
            weights = load_weights(args.weights)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"libretto: poids invalides : {exc}", file=sys.stderr)
            return 1
    try:
        score = build_score(parse_midi(path))
    except ValueError as exc:
        print(f"libretto: {exc}", file=sys.stderr)
        return 1
    if not score.sections:
        print(f"libretto: aucune note exploitable dans {path}", file=sys.stderr)
        return 1
    return _run(SenseOfMusicalStructure(score, weights=weights), args, path.name)


if __name__ == "__main__":
    sys.exit(main())
