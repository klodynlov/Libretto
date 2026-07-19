"""
Libretto — calibration contrastive des poids des 29 axes.

Problème : optimiser les poids demande un « ground truth », et personne n'a
un corpus MIDI annoté en qualité structurelle. Solution auto-supervisée :
le ground truth se fabrique. Chaque MIDI réel du corpus est un positif ;
ses versions **dégradées** (mesures mélangées, segments transposés,
vélocités aplaties, attaques décalées, mélodie brouillée) sont des négatifs
dont on sait par construction qu'ils sont structurellement pires. On cherche
alors les poids qui maximisent la marge score(original) − score(dégradé).

Insight clé : les scores des 29 axes ne dépendent PAS des poids (l'axe 29
synthétise les scores de groupes non pondérés). L'analyse MIDI — la partie
chère (~10 ms/fichier) — se précalcule donc UNE fois par fichier ; la
recherche de poids n'est ensuite que des produits scalaires en dimension 29,
des milliers d'évaluations par seconde. Pas besoin d'algorithme génétique :
un hill climbing sur le simplexe (somme = 1, plancher par axe) suffit,
régularisé vers les poids experts pour éviter la solution dégénérée
« tout le poids sur l'axe le plus discriminant ».

Tout est déterministe (graine unique) et 100 % stdlib.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

from .axes import AXES_META, SenseOfMusicalStructure
from .builder import _bar_starts, build_score
from .midi import MidiData, MidiNote, parse_midi

DRUM_CHANNEL = 9

# Ordre canonique des axes (1..29) → ids et poids experts.
AXIS_IDS: list[str] = [AXES_META[n][0] for n in sorted(AXES_META)]
EXPERT_WEIGHTS: list[float] = [AXES_META[n][2] for n in sorted(AXES_META)]


# ──────────────────────────────────────────────
# Dégradations : MidiData → MidiData structurellement pire
# ──────────────────────────────────────────────

def _clone(md: MidiData, notes: list[MidiNote]) -> MidiData:
    return MidiData(ppq=md.ppq, notes=notes, tempos=list(md.tempos),
                    time_sigs=list(md.time_sigs), markers=list(md.markers),
                    end_tick=md.end_tick)


def degrade_shuffle_bars(md: MidiData, rng: random.Random) -> MidiData:
    """Permute les mesures : mêmes notes, même histogramme global, mais
    forme, continuité harmonique et cadences détruites."""
    starts = _bar_starts(md)
    if len(starts) < 4:
        return _clone(md, list(md.notes))
    order = list(range(len(starts)))
    rng.shuffle(order)
    # position d'arrivée de chaque mesure source
    dest = [0] * len(starts)
    for new_pos, src in enumerate(order):
        dest[src] = new_pos
    from bisect import bisect_right
    notes = []
    for n in md.notes:
        b = min(len(starts) - 1, max(0, bisect_right(starts, n.start) - 1))
        shift = starts[dest[b]] - starts[b]
        notes.append(replace(n, start=n.start + shift, end=n.end + shift))
    return _clone(md, notes)


def degrade_transpose_segments(md: MidiData, rng: random.Random) -> MidiData:
    """Transpose 3-6 segments contigus d'intervalles aléatoires (±1..6 dt) :
    stabilité tonale, contraste et cadences détruits, rythme intact."""
    if not md.notes:
        return _clone(md, [])
    n_seg = rng.randint(3, 6)
    cuts = sorted(rng.randrange(md.end_tick + 1) for _ in range(n_seg - 1))
    edges = [0] + cuts + [md.end_tick + 1]
    deltas = [rng.choice([-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6])
              for _ in range(len(edges) - 1)]
    from bisect import bisect_right
    notes = []
    for n in md.notes:
        if n.channel == DRUM_CHANNEL:
            notes.append(replace(n))
            continue
        seg = max(0, bisect_right(edges, n.start) - 1)
        pitch = min(127, max(0, n.pitch + deltas[min(seg, len(deltas) - 1)]))
        notes.append(replace(n, pitch=pitch))
    return _clone(md, notes)


def degrade_flatten_dynamics(md: MidiData, rng: random.Random) -> MidiData:
    """Toutes les vélocités à la moyenne : arc énergétique, gamme dynamique
    et progression aplatis."""
    if not md.notes:
        return _clone(md, [])
    mean_vel = max(1, round(sum(n.velocity for n in md.notes) / len(md.notes)))
    return _clone(md, [replace(n, velocity=mean_vel) for n in md.notes])


def degrade_jitter_onsets(md: MidiData, rng: random.Random) -> MidiData:
    """Décale chaque attaque de ±0.2..0.6 temps : carrure, contretemps et
    complexité rythmique sortent de leurs zones optimales."""
    notes = []
    for n in md.notes:
        shift = round(rng.uniform(0.2, 0.6) * md.ppq) * rng.choice((-1, 1))
        start = max(0, n.start + shift)
        notes.append(replace(n, start=start, end=start + max(1, n.end - n.start)))
    return _clone(md, notes)


def degrade_scramble_melody(md: MidiData, rng: random.Random) -> MidiData:
    """Redistribue les hauteurs entre les notes (rythme conservé) : contour,
    motifs, thème et accords brouillés."""
    melodic = [n for n in md.notes if n.channel != DRUM_CHANNEL]
    pitches = [n.pitch for n in melodic]
    rng.shuffle(pitches)
    it = iter(pitches)
    notes = [replace(n, pitch=next(it)) if n.channel != DRUM_CHANNEL else replace(n)
             for n in md.notes]
    return _clone(md, notes)


DEGRADATIONS = {
    "shuffle_bars": degrade_shuffle_bars,
    "transpose_segments": degrade_transpose_segments,
    "flatten_dynamics": degrade_flatten_dynamics,
    "jitter_onsets": degrade_jitter_onsets,
    "scramble_melody": degrade_scramble_melody,
}


# ──────────────────────────────────────────────
# Précalcul des vecteurs d'axes (la seule partie chère)
# ──────────────────────────────────────────────

def axis_vector(md: MidiData) -> list[float] | None:
    """Vecteur des 29 scores d'axes (indépendant des poids), None si vide."""
    score = build_score(md)
    if not score.sections:
        return None
    sms = SenseOfMusicalStructure(score)
    sms.calculate()
    return [a.score for a in sms.axes]


def file_vectors(path: str | Path, seed: int, variants: int) -> tuple[list[float], list[list[float]]] | None:
    """(vecteur positif, vecteurs négatifs) pour un fichier, ou None si
    inexploitable. Déterministe : graine dérivée du nom + graine globale."""
    try:
        md = parse_midi(path)
    except (ValueError, OSError, IndexError):
        return None
    pos = axis_vector(md)
    if pos is None:
        return None
    negs = []
    for name, fn in sorted(DEGRADATIONS.items()):
        for k in range(variants):
            rng = random.Random(f"{seed}:{Path(path).name}:{name}:{k}")
            vec = axis_vector(fn(md, rng))
            if vec is not None:
                negs.append(vec)
    return pos, negs


def _worker(job: tuple[str, int, int]):
    path, seed, variants = job
    return path, file_vectors(path, seed, variants)


def corpus_vectors(paths: list[Path], seed: int, variants: int,
                   jobs: int = 1) -> dict[str, tuple[list[float], list[list[float]]]]:
    """Précalcule les vecteurs de tout le corpus. `jobs` > 1 parallélise via
    multiprocessing (l'analyse est CPU-bound ; la recherche de poids, elle,
    n'en a pas besoin : elle travaille sur ces vecteurs en cache)."""
    jobs_args = [(str(p), seed, variants) for p in paths]
    if jobs > 1 and len(paths) > 1:
        from multiprocessing import Pool
        with Pool(jobs) as pool:
            results = pool.map(_worker, jobs_args)
    else:
        results = [_worker(j) for j in jobs_args]
    return {path: vecs for path, vecs in results if vecs is not None}


# ──────────────────────────────────────────────
# Objectif contrastif + hill climbing sur le simplexe
# ──────────────────────────────────────────────

def _pairs(corpus: dict) -> list[tuple[list[float], list[float]]]:
    return [(pos, neg) for pos, negs in corpus.values() for neg in negs]


def evaluate(weights: list[float],
             pairs: list[tuple[list[float], list[float]]]) -> tuple[float, float]:
    """(accuracy de classement, marge moyenne) : un pair est bien classé si
    score(original) > score(dégradé)."""
    if not pairs:
        return 0.0, 0.0
    ok = 0
    margin_sum = 0.0
    for pos, neg in pairs:
        m = sum(w * (p - n) for w, p, n in zip(weights, pos, neg))
        margin_sum += m
        if m > 0:
            ok += 1
    return ok / len(pairs), margin_sum / len(pairs)


def objective(weights: list[float], pairs: list, prior: list[float],
              lam: float = 0.3) -> float:
    acc, margin = evaluate(weights, pairs)
    drift = sum(abs(w - w0) for w, w0 in zip(weights, prior))
    return acc + margin - lam * drift


def optimize_weights(pairs: list, prior: list[float] | None = None,
                     seed: int = 42, iters: int = 4000, floor: float = 0.005,
                     lam: float = 0.3) -> list[float]:
    """Hill climbing : chaque pas transfère un peu de masse d'un axe vers un
    autre (somme exactement conservée, plancher respecté), gardé si
    l'objectif monte. Sur vecteurs en cache : < 1 s pour 4000 itérations."""
    prior = list(prior if prior is not None else EXPERT_WEIGHTS)
    rng = random.Random(seed)
    w = list(prior)
    best = objective(w, pairs, prior, lam)
    n = len(w)
    for _ in range(iters):
        i, j = rng.randrange(n), rng.randrange(n)
        if i == j:
            continue
        delta = rng.choice((0.002, 0.005, 0.01, 0.02))
        if w[i] - delta < floor:
            continue
        w2 = list(w)
        w2[i] -= delta
        w2[j] += delta
        cand = objective(w2, pairs, prior, lam)
        if cand > best:
            w, best = w2, cand
    return w


def discrimination(corpus: dict) -> dict[str, float]:
    """Pouvoir discriminant par axe : moyenne(positifs) − moyenne(négatifs).
    Diagnostic direct : quels axes détectent réellement la dégradation."""
    pairs = _pairs(corpus)
    if not pairs:
        return {}
    diffs = [0.0] * len(AXIS_IDS)
    for pos, neg in pairs:
        for k in range(len(diffs)):
            diffs[k] += pos[k] - neg[k]
    return {AXIS_IDS[k]: round(diffs[k] / len(pairs), 4) for k in range(len(AXIS_IDS))}


# ──────────────────────────────────────────────
# Pipeline complet
# ──────────────────────────────────────────────

def run_calibration(corpus_dir: str | Path, seed: int = 42, variants: int = 2,
                    iters: int = 4000, jobs: int = 1, lam: float = 0.3) -> dict:
    """Calibre les poids sur tous les .mid d'un dossier. Retourne un rapport
    sérialisable (poids proposés + métriques avant/après + diagnostic)."""
    paths = sorted(p for p in Path(corpus_dir).rglob("*.mid*") if p.is_file())
    corpus = corpus_vectors(paths, seed, variants, jobs)
    pairs = _pairs(corpus)
    if not pairs:
        raise ValueError(f"aucun MIDI exploitable dans {corpus_dir}")
    acc0, mar0 = evaluate(EXPERT_WEIGHTS, pairs)
    w_star = optimize_weights(pairs, seed=seed, iters=iters, lam=lam)
    acc1, mar1 = evaluate(w_star, pairs)
    return {
        "corpus": str(corpus_dir),
        "n_files": len(corpus),
        "n_pairs": len(pairs),
        "seed": seed,
        "variants": variants,
        "before": {"accuracy": round(acc0, 4), "margin": round(mar0, 4)},
        "after": {"accuracy": round(acc1, 4), "margin": round(mar1, 4)},
        "weights": {aid: round(w, 4) for aid, w in zip(AXIS_IDS, w_star)},
        "discrimination": discrimination(corpus),
    }


def load_weights(path: str | Path) -> dict[str, float]:
    """Charge un fichier de poids (sortie de `calibrate` ou dict direct)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    weights = data.get("weights", data)
    unknown = set(weights) - set(AXIS_IDS)
    if unknown:
        raise ValueError(f"axes inconnus dans {path}: {sorted(unknown)}")
    return {aid: float(w) for aid, w in weights.items()}
