"""
Forge embellish — partir d'UN .mid, en tirer des variantes, garder la mieux
construite. 100 % stdlib.

L'idée
------
Les autres ponts de Forge partent de rien (make_corpus, MusicLang) ou
d'audio (ACE-Step). Celui-ci part d'une séquence que VOUS avez écrite —
un export Gadget, un couplet, une maquette — et l'embellit : il applique N
recettes de transformation symbolique (humanisation, arc dynamique,
doublages d'octave, arpèges), fait juger chaque variante par Libretto, et
sélectionne la mieux construite. Le générateur, ici, c'est votre fichier
plus une grammaire de retouches.

    ┌──────────┐   ┌───────────────┐   ┌────────────┐   ┌──────────┐
    │ votre    │ → │ N variantes   │ → │  LIBRETTO  │ → │ sélection│
    │ .mid     │   │ (transformées)│   │ SMS + fiab.│   │ + shortlist
    └──────────┘   └───────────────┘   └────────────┘   └──────────┘

Ce que ça fait, et ce que ça ne fait pas
----------------------------------------
Libretto ne compose pas : il juge. L'embellisseur ne « comprend » pas votre
musique — il propose des retouches PLAUSIBLES et laisse le juge trancher.
Trois conséquences honnêtes :

1. **L'original concourt** (variante 000, intacte). S'il gagne, c'est un
   résultat, pas un échec : votre séquence est déjà bien construite, ou ces
   retouches-là ne la servent pas. Vous le saurez au lieu de le supposer.
2. Les retouches touchent la STRUCTURE que Libretto mesure (dynamique, arc,
   texture, polyphonie) — pas le son, pas le choix des synthés, pas le
   groove. Un gain de score est un gain de charpente, à confirmer à
   l'oreille.
3. C'est du MIDI natif, pas du transcrit : la fiabilité est digne de foi
   ici (contrairement au pont audio), et le classement se lit.

Les retouches (chacune déterministe, bornée, testée)
----------------------------------------------------
- **humanize** : disperse vélocités et micro-timing — tue le côté machine ;
- **arc** : impose une courbe dynamique (montée vers un sommet ~⅔, retour) —
  nourrit l'arc émotionnel (axe 28) et la gamme dynamique (axe 26) ;
- **octaves** : double une part des notes à l'octave — polyphonie (27),
  texture (25), étendue (18) ;
- **arpège** : égrène les accords tenus (attaques décalées) — mouvement
  rythmique et texture.

Chaque variante combine un sous-ensemble de ces retouches selon une recette
déterministe (graine × index) ; les percussions (canal 9) sont humanisées
mais jamais transposées ni arpégées.

Usage
-----
    python3 examples/forge_embellish.py source.mid sortie/ [n=12] [seed=1]
        [--min-confidence 0.55] [--min-score 0.0]
        [--axes] [--shortlist K] [--keep-all]

Sortie : `sortie/candidates/variation_XXX.mid` (000 = votre original),
`sortie/forge_winner.mid`, `sortie/forge_report.json`, et un verdict clair :
le gagnant a-t-il battu l'original, et avec quelle recette ?
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.midi import MidiData, MidiNote, parse_midi, write_mididata  # noqa: E402

from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, forge_from_dir)

DRUM_CHANNEL = 9

# Recettes appliquées aux variantes 1..N (la 0 est l'original intact). On
# cycle dessus pour garantir la couverture ; les paramètres, eux, varient
# par graine. Chaque recette liste les retouches à composer, dans l'ordre.
RECIPES: list[tuple[str, tuple[str, ...]]] = [
    ("humanise",       ("humanize",)),
    ("arc",            ("humanize", "arc")),
    ("arc+octaves",    ("humanize", "arc", "octaves")),
    ("arc+arpège",     ("humanize", "arc", "arp")),
    ("octaves",        ("humanize", "octaves")),
    ("tout",           ("humanize", "arc", "octaves", "arp")),
]


# ── Retouches : chacune (notes, …, rng) → nouvelles notes, pure et bornée ──

def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def humanize(notes: list[MidiNote], ppq: int, rng: random.Random) -> list[MidiNote]:
    """Disperse vélocité (±) et position (±), durée préservée ≥ 1 tick.
    S'applique à tout, percussions comprises : un batteur non plus ne frappe
    pas deux fois à la vélocité exacte."""
    vel_amt = rng.randint(4, 12)
    time_amt = max(1, ppq // 32)
    out = []
    for n in notes:
        dur = max(1, n.end - n.start)
        start = max(0, n.start + rng.randint(-time_amt, time_amt))
        vel = _clamp(n.velocity + rng.randint(-vel_amt, vel_amt), 1, 127)
        out.append(MidiNote(start, start + dur, n.pitch, vel, n.channel, n.track))
    return out


def shape_dynamics(notes: list[MidiNote], end_tick: int,
                   rng: random.Random) -> list[MidiNote]:
    """Impose un arc dynamique : les vélocités montent vers un sommet situé
    vers les deux tiers, puis redescendent. Multiplie la gamme dynamique et
    dessine l'arc émotionnel — les deux axes les plus sensibles au « vivant »."""
    if end_tick <= 0:
        return list(notes)
    peak = rng.uniform(0.55, 0.72)
    depth = rng.uniform(0.20, 0.45)
    out = []
    for n in notes:
        p = min(1.0, max(0.0, n.start / end_tick))
        rise = p / peak if p <= peak else (1.0 - p) / max(1e-6, 1.0 - peak)
        factor = (1.0 - depth) + rise * (2.0 * depth)  # 1-depth aux bords, 1+depth au sommet
        vel = _clamp(round(n.velocity * factor), 1, 127)
        out.append(MidiNote(n.start, n.end, n.pitch, vel, n.channel, n.track))
    return out


def double_octaves(notes: list[MidiNote], rng: random.Random) -> list[MidiNote]:
    """Double une fraction des notes mélodiques à l'octave (haut ou bas),
    vélocité atténuée. Ajoute des voix sans inventer d'harmonie : la copie
    est toujours consonante avec l'originale. Percussions exclues."""
    fraction = rng.uniform(0.20, 0.50)
    direction = rng.choice((-12, 12))
    out = list(notes)
    for n in notes:
        if n.channel == DRUM_CHANNEL:
            continue
        target = n.pitch + direction
        if 0 <= target <= 127 and rng.random() < fraction:
            vel = _clamp(round(n.velocity * 0.8), 1, 127)
            out.append(MidiNote(n.start, n.end, target, vel, n.channel, n.track))
    return out


def arpeggiate(notes: list[MidiNote], ppq: int,
               rng: random.Random) -> list[MidiNote]:
    """Égrène les accords tenus : dans un groupe de notes simultanées (même
    canal, même attaque, ≥ 3 notes), décale les attaques successives d'une
    subdivision, en montant. Les tenues gardent leur fin — c'est un accord
    roulé, pas une rupture. Percussions exclues."""
    step = max(1, ppq // 4)
    groups: dict[tuple[int, int], list[int]] = {}
    for i, n in enumerate(notes):
        if n.channel == DRUM_CHANNEL:
            continue
        groups.setdefault((n.channel, n.start), []).append(i)

    out = [MidiNote(n.start, n.end, n.pitch, n.velocity, n.channel, n.track)
           for n in notes]
    for idxs in groups.values():
        if len(idxs) < 3:
            continue
        for rank, i in enumerate(sorted(idxs, key=lambda j: notes[j].pitch)):
            n = out[i]
            new_start = min(n.start + rank * step, n.end - 1)
            out[i] = MidiNote(new_start, n.end, n.pitch, n.velocity,
                              n.channel, n.track)
    return out


# ── Assemblage d'une variante ────────────────────────────────

def _clone(md: MidiData, notes: list[MidiNote]) -> MidiData:
    """Nouveau MidiData : mêmes méta (tempo, signatures, marqueurs, CC), notes
    remplacées, end_tick recalé sur la dernière note (l'humanisation peut la
    pousser)."""
    end_tick = max((n.end for n in notes), default=md.end_tick)
    return MidiData(ppq=md.ppq, notes=notes, tempos=list(md.tempos),
                    time_sigs=list(md.time_sigs), markers=list(md.markers),
                    controls=list(md.controls), end_tick=end_tick)


def build_variation(md: MidiData, moves: tuple[str, ...],
                    rng: random.Random) -> MidiData:
    """Applique une recette (suite de retouches) à un MidiData. Ordre
    important : humanize d'abord (perturbe le timing), arc ensuite (lit les
    positions), octaves/arpège en dernier (ajoutent/décalent des notes)."""
    notes = list(md.notes)
    for move in moves:
        if move == "humanize":
            notes = humanize(notes, md.ppq, rng)
        elif move == "arc":
            notes = shape_dynamics(notes, md.end_tick, rng)
        elif move == "octaves":
            notes = double_octaves(notes, rng)
        elif move == "arp":
            notes = arpeggiate(notes, md.ppq, rng)
    return _clone(md, notes)


def embellish(source: str | Path, out_dir: str | Path, n: int = 12,
              seed: int = 1, min_confidence: float = 0.55, min_score: float = 0.0,
              axes_report: bool = False, shortlist: int = 0) -> dict:
    """Génère n variantes de `source` (la 000 est l'original intact), les
    fait juger et sélectionner par Forge. Renvoie le rapport forge_from_dir,
    enrichi de `embellish` : recette par variante, et comparaison
    gagnant/original."""
    src_md = parse_midi(source)
    out = Path(out_dir)
    cand = out / "candidates"
    cand.mkdir(parents=True, exist_ok=True)

    recipes_by_file: dict[str, str] = {}
    # Variante 000 : l'original, copié tel quel — le témoin.
    write_mididata(cand / "variation_000.mid", src_md)
    recipes_by_file["variation_000.mid"] = "original"
    for i in range(1, n):
        name, moves = RECIPES[(i - 1) % len(RECIPES)]
        rng = random.Random(f"embellish:{Path(source).stem}:{seed}:{i}")
        var = build_variation(src_md, moves, rng)
        fname = f"variation_{i:03d}.mid"
        write_mididata(cand / fname, var)
        recipes_by_file[fname] = name

    report = forge_from_dir(cand, out, min_confidence=min_confidence,
                            min_score=min_score, axes_report=axes_report,
                            shortlist=shortlist)

    # Verdict : le gagnant a-t-il battu l'original ?
    board = {c["file"]: c for c in report["leaderboard"]}
    original = board.get("variation_000.mid")
    winner = report["winner"]
    verdict = None
    if winner is not None:
        for c in report["leaderboard"]:
            c["recipe"] = recipes_by_file.get(c["file"], "?")
        verdict = {
            "winner_file": winner["file"],
            "winner_recipe": recipes_by_file.get(winner["file"], "?"),
            "winner_score": winner["score"],
            "original_score": original["score"] if original else None,
            "original_eligible": original is not None,
            "original_won": winner["file"] == "variation_000.mid",
            "improvement": (round(winner["score"] - original["score"], 4)
                            if original else None),
        }
    report["embellish"] = {"recipes": recipes_by_file, "verdict": verdict}
    return report


def _print_verdict(report: dict) -> None:
    v = report.get("embellish", {}).get("verdict")
    if not v:
        print("\nEmbellissement : aucune variante fiable — rien à conclure.")
        return
    if v["original_won"]:
        print("\n🎯 Verdict : l'ORIGINAL gagne. Vos retouches n'ont pas amélioré "
              "la structure —\n   la séquence est déjà bien construite sur ce que "
              "Libretto mesure, ou ces\n   retouches-là ne la servent pas. "
              "(Résultat, pas échec.)")
    elif not v["original_eligible"]:
        print(f"\n🎯 Verdict : gagnant « {v['winner_recipe']} » "
              f"(score {v['winner_score']:.3f}).\n   L'original n'était pas "
              f"éligible (fiabilité insuffisante) : à lire avec prudence.")
    else:
        print(f"\n🎯 Verdict : la variante « {v['winner_recipe']} » bat l'original "
              f"de +{v['improvement']:.3f}\n   ({v['winner_score']:.3f} contre "
              f"{v['original_score']:.3f}). Gain de CHARPENTE — à confirmer à "
              f"l'oreille :\n   Libretto ne juge ni le son ni le groove.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_embellish",
        description="Forge embellish — tire N variantes d'un .mid "
                    "(humanisation, arc, octaves, arpèges) et garde la mieux "
                    "construite. L'original concourt.")
    parser.add_argument("source", help="fichier .mid/.midi de départ")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=12,
                        help="nombre de variantes, original compris (défaut 12)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--min-confidence", type=float, default=0.55,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=0, metavar="K",
                        help="K variantes sous contrainte de diversité")
    args = parser.parse_args(argv)

    if not Path(args.source).is_file():
        print(f"forge_embellish : fichier introuvable : {args.source}",
              file=sys.stderr)
        return 2

    report = embellish(args.source, args.out_dir, n=args.n, seed=args.seed,
                       min_confidence=args.min_confidence,
                       min_score=args.min_score,
                       axes_report=args.axes, shortlist=args.shortlist)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    _print_verdict(report)
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
