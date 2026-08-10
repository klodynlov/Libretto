"""
markov_gen — un générateur APPRIS, pur stdlib, entraîné sur un corpus MIDI.

Ce que c'est, et en quoi ça diffère de make_corpus
--------------------------------------------------
`make_corpus.py` est procédural : ses progressions, ses gammes, ses formes
sont écrites à la main. Utile pour la calibration, mais ce n'est pas un
*modèle* — il ne connaît que ce qu'on y a inscrit. `markov_gen` fait
l'inverse : il n'a AUCUNE table d'accords ni de gammes, il **apprend** son
matériau d'un dossier de fichiers `.mid`, puis en tire des séquences neuves.
C'est la brique génératrice « réelle » que forge.py appelle de ses vœux,
mais qui tient dans la bibliothèque standard et tourne sans réseau ni GPU —
là où `forge_musiclang` (transformer) et `forge_acestep` (audio) demandent
des poids lourds et des dépendances hors contrat.

Le modèle, honnêtement
----------------------
Chaîne de Markov, factorisée et à repli (back-off). Trois soins la rendent
plus musicale qu'une chaîne naïve, sans jamais coder de théorie :

1. **Normalisation tonale.** Chaque fichier du corpus est d'abord ramené à la
   tonique 0 (`estimate_key`, l'estimateur Krumhansl-Kessler du cœur). On
   apprend donc des *intervalles en demi-tons* — le mode (majeur, mineur,
   dorien…) est encodé par les intervalles qui reviennent, pas déclaré. À la
   génération, toutes les voix sont retransposées par UNE seule tonique : les
   pistes restent dans la même tonalité sans qu'on ait écrit la moindre
   progression.
2. **Voicings appris.** Pour chaque attaque d'une piste, on retient la ligne
   supérieure (skyline) ET l'empilement réel des notes en dessous d'elle. À
   la régénération, on ré-empile ces voicings *observés* sous la note générée
   — l'harmonie verticale vient du corpus, pas d'un catalogue de triades.
3. **Rythme appris.** On apprend l'intervalle inter-attaques (IOI) et la
   durée des notes, bucketés sur une grille de subdivisions — silences et
   chevauchements compris.

Ce qu'il NE fait pas — le juge est là pour ça
---------------------------------------------
Comme tout modèle local (et comme le dit la docstring de forge_musiclang), la
chaîne produit une cohérence de proche en proche mais **pas de forme longue**
(couplets/refrains, arc émotionnel) ni d'harmonie fonctionnelle garantie —
précisément ce que les 29 axes de Libretto mesurent. Attendez des scores de
forme modestes : ce n'est pas un défaut du branchement, c'est Forge qui juge
un matériau sans architecture. Les pistes de percussion (canal 9) sont
exclues de l'apprentissage : ce modèle parle hauteurs.

Usage
-----
En bibliothèque : `train_from_paths(paths)` → modèle ; `generate_one(model,
rng, bars, tonic)` → (pistes, bpm) pour `write_midi`. En pratique, on passe
par `forge_markov.py`, qui entraîne, génère N candidats et laisse Forge trier.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.axes import estimate_key  # noqa: E402
from libretto.midi import MidiData, parse_midi  # noqa: E402

# Grille de subdivisions (en noires) : durées et intervalles inter-attaques y
# sont accrochés. Couvre la double-croche à la ronde, binaire et pointé.
GRID: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
ORDER = 2               # ordre de la chaîne sur les intervalles mélodiques
PITCH_LO, PITCH_HI = 40, 88   # bornes de la ligne supérieure générée
DRUMS = 9


def _nearest(x: float, grid: tuple[float, ...]) -> float:
    return min(grid, key=lambda g: abs(g - x))


def _pitch_histogram(md: MidiData) -> list[float]:
    """Classes de hauteur pondérées par la durée, canal 9 exclu — la matière
    de `estimate_key`."""
    h = [0.0] * 12
    for n in md.notes:
        if n.channel == DRUMS:
            continue
        h[n.pitch % 12] += max(1, n.end - n.start)
    return h


def _tonic_shift(md: MidiData) -> int:
    """Demi-tons à retrancher pour ramener la tonique estimée à 0, choisis
    dans [-6, 5] pour ne pas déplacer le registre de plus d'un demi-octave."""
    tonic, _mode, _corr, _margin = estimate_key(_pitch_histogram(md))
    shift = -tonic
    if shift < -6:
        shift += 12
    return shift


@dataclass
class TrackModel:
    """Tout ce qu'on apprend d'une piste (par index de piste dans le corpus)."""
    start_pitches: Counter = field(default_factory=Counter)
    # ordre o -> {contexte (o intervalles) -> Counter(intervalle suivant)}
    iv_orders: list = field(default_factory=lambda: [Counter(), {}, {}])
    ioi_start: Counter = field(default_factory=Counter)
    ioi_trans: dict = field(default_factory=dict)   # ioi -> Counter(ioi suivant)
    dur_by_ioi: dict = field(default_factory=dict)  # ioi -> Counter(durée)
    voicings: Counter = field(default_factory=Counter)  # offsets sous la skyline
    n_onsets: int = 0


@dataclass
class Model:
    tracks: dict = field(default_factory=dict)      # index de piste -> TrackModel
    bpms: Counter = field(default_factory=Counter)
    n_files: int = 0


def _skyline(notes, ppq: int):
    """Séquence d'attaques d'une piste : (temps, hauteur supérieure, durée,
    voicing). Les notes de même attaque forment un accord ; on garde la note
    du dessus et les offsets des autres en dessous d'elle."""
    groups: dict[int, list] = {}
    for n in notes:
        groups.setdefault(n.start, []).append(n)
    out = []
    for start in sorted(groups):
        stack = groups[start]
        top = max(x.pitch for x in stack)
        voicing = tuple(sorted({top - x.pitch for x in stack}))
        dur = max(1, max(x.end for x in stack) - start) / ppq
        out.append((start / ppq, top, dur, voicing))
    return out


def _learn_track(tm: TrackModel, events) -> None:
    """Verse une séquence d'attaques (skyline) dans le modèle de piste."""
    if len(events) < 4:               # trop court pour apprendre une transition
        return
    tm.start_pitches[events[0][1]] += 1
    prev_ioi = None
    ctx: list[int] = []
    for k in range(len(events)):
        _t, pitch, dur, voicing = events[k]
        tm.voicings[voicing] += 1
        tm.n_onsets += 1
        if k + 1 < len(events):
            nxt_pitch = events[k + 1][1]
            interval = max(-24, min(24, nxt_pitch - pitch))
            ioi = _nearest(max(0.25, events[k + 1][0] - events[k][0]), GRID)
            # intervalle mélodique, à tous les ordres 0..ORDER (repli)
            for o in range(0, ORDER + 1):
                if len(ctx) >= o:
                    key = tuple(ctx[len(ctx) - o:]) if o else ()
                    table = tm.iv_orders[o]
                    if o == 0:
                        table[interval] += 1
                    else:
                        table.setdefault(key, Counter())[interval] += 1
            ctx.append(interval)
            # rythme
            if prev_ioi is None:
                tm.ioi_start[ioi] += 1
            else:
                tm.ioi_trans.setdefault(prev_ioi, Counter())[ioi] += 1
            tm.dur_by_ioi.setdefault(ioi, Counter())[_nearest(dur, GRID)] += 1
            prev_ioi = ioi


def train(mds: list[MidiData]) -> Model:
    """Entraîne le modèle sur des MidiData déjà analysés."""
    model = Model()
    for md in mds:
        if not md.notes:
            continue
        model.n_files += 1
        model.bpms[round((md.tempos or [(0, 120.0)])[0][1])] += 1
        shift = _tonic_shift(md)
        by_track: dict[int, list] = {}
        for n in md.notes:
            if n.channel == DRUMS:            # ce modèle parle hauteurs
                continue
            by_track.setdefault(n.track, []).append(n)
        for tidx, notes in by_track.items():
            # normalisation tonale : toute la piste est ramenée en tonique 0
            shifted = [type(n)(n.start, n.end, n.pitch + shift, n.velocity,
                               n.channel, n.track) for n in notes]
            events = _skyline(shifted, md.ppq)
            tm = model.tracks.setdefault(tidx, TrackModel())
            _learn_track(tm, events)
    return model


def train_from_paths(paths: list[str | Path]) -> tuple[Model, int]:
    """Entraîne à partir de chemins .mid ; renvoie (modèle, nb de fichiers
    illisibles ignorés)."""
    mds, skipped = [], 0
    for p in paths:
        try:
            mds.append(parse_midi(p))
        except (ValueError, OSError):
            skipped += 1
    return train(mds), skipped


# ──────────────────────────────────────────────
# Génération
# ──────────────────────────────────────────────

def _sample(counter: Counter, rng):
    items = list(counter.items())
    return rng.choices([k for k, _ in items], weights=[w for _, w in items])[0]


def _sample_interval(tm: TrackModel, ctx: list[int], rng) -> int:
    """Intervalle suivant, à repli : ordre le plus élevé dont le contexte a
    déjà été vu, jusqu'au marginal, puis pas conjoint par défaut."""
    for o in range(min(len(ctx), ORDER), 0, -1):
        key = tuple(ctx[len(ctx) - o:])
        table = tm.iv_orders[o]
        if key in table and table[key]:
            return _sample(table[key], rng)
    if tm.iv_orders[0]:
        return _sample(tm.iv_orders[0], rng)
    return 0


def _generate_track(tm: TrackModel, rng, total_beats: float, tonic: int):
    """Une piste régénérée : liste de notes (start_beat, dur, pitch, vel,
    channel-placeholder). Le canal réel est posé par l'appelant."""
    if tm.n_onsets < 4 or not tm.start_pitches or not tm.ioi_start:
        return []
    notes = []
    top = _sample(tm.start_pitches, rng)
    ioi = _sample(tm.ioi_start, rng)
    ctx: list[int] = []
    beat = 0.0
    while beat < total_beats:
        top = max(PITCH_LO, min(PITCH_HI, top))
        voicing = _sample(tm.voicings, rng) if tm.voicings else (0,)
        dur_counter = tm.dur_by_ioi.get(ioi)
        dur = _sample(dur_counter, rng) if dur_counter else ioi
        vel = 84
        for off in voicing:
            pitch = top - off + tonic
            while pitch < 24:
                pitch += 12
            notes.append((beat, max(0.1, dur * 0.95), pitch, vel, 0))
        interval = _sample_interval(tm, ctx, rng)
        ctx.append(interval)
        top += interval
        beat += ioi
        nxt = tm.ioi_trans.get(ioi)
        ioi = _sample(nxt, rng) if nxt else _sample(tm.ioi_start, rng)
    return notes


def generate_one(model: Model, rng, bars: int = 24,
                 tonic: int | None = None) -> tuple[list, float]:
    """Génère un morceau : (pistes pour write_midi, bpm). Une piste par index
    de piste appris ; canaux 0,1,2… ; canal 4 réservé au « pad » au-delà de 4
    pistes, comme make_corpus, pour rester lisible par le rendu instrumental."""
    total_beats = bars * 4.0
    if tonic is None:
        tonic = rng.randrange(12)
    tracks = []
    for pos, tidx in enumerate(sorted(model.tracks)):
        raw = _generate_track(model.tracks[tidx], rng, total_beats, tonic)
        if not raw:
            continue
        channel = pos if pos < 8 else 5
        tracks.append([(b, d, p, v, channel) for (b, d, p, v, _c) in raw])
    bpm = float(_sample(model.bpms, rng)) if model.bpms else 110.0
    return tracks, bpm
