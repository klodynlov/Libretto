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

# Distance L∞ sous laquelle un vecteur dégradé est un quasi no-op : la paire
# a une marge ≈ 0 par construction et compterait à tort comme mal classée.
NOOP_EPS = 0.01


def _applicable(name: str, md: MidiData) -> bool:
    """Pré-test bon marché : False si la dégradation ne peut rien changer
    sur ce fichier (inutile de payer l'analyse d'un négatif no-op)."""
    if name == "flatten_dynamics":
        vels = [n.velocity for n in md.notes]
        if not vels:
            return False
        mean = sum(vels) / len(vels)
        std = (sum((v - mean) ** 2 for v in vels) / len(vels)) ** 0.5
        return std >= 1.0
    if name == "shuffle_bars":
        return len(_bar_starts(md)) >= 4
    return True


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


def file_vectors(path: str | Path, seed: int, variants: int) -> tuple[list[float], list[tuple[str, list[float]]], int] | None:
    """(vecteur positif, vecteurs négatifs, nb de no-ops exclus) pour un
    fichier, ou None si inexploitable. Un négatif est exclu quand la
    dégradation est inapplicable (pré-test) ou laisse le vecteur d'axes
    quasi identique au positif (L∞ < NOOP_EPS) — ex. flatten_dynamics sur
    des vélocités déjà uniformes, shuffle_bars sur < 4 mesures.
    Déterministe : graine dérivée du nom + graine globale."""
    try:
        md = parse_midi(path)
    except (ValueError, OSError, IndexError):
        return None
    pos = axis_vector(md)
    if pos is None:
        return None
    negs = []
    skipped = 0
    for name, fn in sorted(DEGRADATIONS.items()):
        if not _applicable(name, md):
            skipped += variants
            continue
        for k in range(variants):
            rng = random.Random(f"{seed}:{Path(path).name}:{name}:{k}")
            vec = axis_vector(fn(md, rng))
            if vec is None:
                continue
            if max(abs(p - v) for p, v in zip(pos, vec)) < NOOP_EPS:
                skipped += 1
                continue
            # Le nom de la dégradation est conservé : c'est lui qui permet de
            # savoir non seulement qu'un axe échoue, mais *contre quoi*.
            negs.append((name, vec))
    return pos, negs, skipped


def _worker(job: tuple[str, int, int]):
    path, seed, variants = job
    return path, file_vectors(path, seed, variants)


def corpus_vectors(paths: list[Path], seed: int, variants: int,
                   jobs: int = 1) -> dict[str, tuple[list[float], list[list[float]], int]]:
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

def _pairs(corpus: dict, keys: list[str] | None = None) -> list[tuple[list[float], list[float]]]:
    """Paires (positif, négatif). `keys` restreint à un sous-ensemble de
    fichiers — c'est la granularité de split correcte (voir cross_validate)."""
    selected = corpus if keys is None else {k: corpus[k] for k in keys}
    return [(pos, neg) for pos, negs, _ in selected.values() for _name, neg in negs]


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


def axis_auc(corpus: dict) -> dict[str, float]:
    """AUC appariée par axe : P(positif > négatif) + ½·P(égalité).

    Plus honnête que `discrimination` (une moyenne de différences est écrasée
    par quelques paires extrêmes) et directement interprétable :
      0.5  → l'axe est aveugle à la dégradation (ou toujours à égalité) ;
      >0.5 → l'axe détecte la structure ;
      <0.5 → **l'axe récompense le chaos** — il vote pour le négatif.
    Un axe sous 0.5 est un bug de conception, pas un problème de poids : lui
    donner un poids nul le neutralise, ça ne le répare pas."""
    pairs = _pairs(corpus)
    if not pairs:
        return {}
    wins = [0.0] * len(AXIS_IDS)
    for pos, neg in pairs:
        for k in range(len(wins)):
            if pos[k] > neg[k]:
                wins[k] += 1.0
            elif pos[k] == neg[k]:
                wins[k] += 0.5
    return {AXIS_IDS[k]: round(wins[k] / len(pairs), 4) for k in range(len(AXIS_IDS))}


def axis_auc_by_degradation(corpus: dict) -> dict[str, dict[str, float]]:
    """AUC appariée par axe ET par type de dégradation.

    L'AUC agrégée mélange des questions sans rapport : `21_tempo_variation`
    n'a aucune raison de repérer un brouillage de hauteurs, et son 0.5 global
    est le bon résultat, pas un échec. Ventilée par dégradation, la lecture
    devient franche — 0.5 face à une dégradation qui ne touche pas la
    grandeur mesurée est correct ; **sous 0.5 face à une dégradation qui la
    touche de plein fouet, l'axe est à l'envers**."""
    buckets: dict[str, list[tuple[list[float], list[float]]]] = {}
    for pos, negs, _sk in corpus.values():
        for name, neg in negs:
            buckets.setdefault(name, []).append((pos, neg))
    out: dict[str, dict[str, float]] = {}
    for name, pairs in sorted(buckets.items()):
        wins = [0.0] * len(AXIS_IDS)
        for pos, neg in pairs:
            for k in range(len(wins)):
                if pos[k] > neg[k]:
                    wins[k] += 1.0
                elif pos[k] == neg[k]:
                    wins[k] += 0.5
        out[name] = {AXIS_IDS[k]: round(wins[k] / len(pairs), 4)
                     for k in range(len(AXIS_IDS))}
    return out


# ──────────────────────────────────────────────
# Validation : split par FICHIER, k-fold
# ──────────────────────────────────────────────

def split_files(keys: list[str], k: int, seed: int) -> list[list[str]]:
    """Découpe les fichiers en k plis disjoints (déterministe).

    Le split se fait au niveau du FICHIER, jamais de la paire : les ~10
    négatifs d'un même fichier partagent son vecteur positif, donc découper
    par paire mettrait le même positif des deux côtés — fuite garantie et
    accuracy de test gonflée."""
    shuffled = sorted(keys)
    random.Random(seed).shuffle(shuffled)
    k = max(2, min(k, len(shuffled)))
    return [shuffled[i::k] for i in range(k)]


def cross_validate(corpus: dict, k: int = 5, seed: int = 42, iters: int = 4000,
                   lam: float = 0.3, floor: float = 0.005) -> dict:
    """Validation croisée k-fold par fichier : sur chaque pli, on optimise les
    poids sur les (k−1) autres et on mesure sur le pli tenu à l'écart.

    C'est la seule métrique qui dise si les poids généralisent. L'accuracy
    d'entraînement, elle, monte toujours — avec 29 poids libres et quelques
    centaines de paires, elle mesure surtout la capacité à mémoriser."""
    folds = split_files(list(corpus), k, seed)
    if len(folds) < 2:
        return {"n_folds": 0, "note": "corpus trop petit pour une validation croisée"}
    rows = []
    for i, test_keys in enumerate(folds):
        train_keys = [key for j, f in enumerate(folds) if j != i for key in f]
        train_pairs = _pairs(corpus, train_keys)
        test_pairs = _pairs(corpus, test_keys)
        if not train_pairs or not test_pairs:
            continue
        w = optimize_weights(train_pairs, seed=seed + i, iters=iters,
                             floor=floor, lam=lam)
        acc_tr, mar_tr = evaluate(w, train_pairs)
        acc_te, mar_te = evaluate(w, test_pairs)
        base_te, base_mar_te = evaluate(EXPERT_WEIGHTS, test_pairs)
        rows.append({"fold": i, "n_train_files": len(train_keys),
                     "n_test_files": len(test_keys), "n_test_pairs": len(test_pairs),
                     "train_accuracy": round(acc_tr, 4), "train_margin": round(mar_tr, 4),
                     "test_accuracy": round(acc_te, 4), "test_margin": round(mar_te, 4),
                     "expert_test_accuracy": round(base_te, 4),
                     "expert_test_margin": round(base_mar_te, 4)})
    if not rows:
        return {"n_folds": 0, "note": "aucun pli exploitable"}

    def stat(field: str) -> tuple[float, float]:
        vals = [r[field] for r in rows]
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        return round(mean, 4), round(sd, 4)

    acc_te_m, acc_te_s = stat("test_accuracy")
    acc_tr_m, _ = stat("train_accuracy")
    mar_te_m, mar_te_s = stat("test_margin")
    exp_te_m, _ = stat("expert_test_accuracy")
    return {
        "n_folds": len(rows),
        "train_accuracy_mean": acc_tr_m,
        "test_accuracy_mean": acc_te_m,
        "test_accuracy_std": acc_te_s,
        "test_margin_mean": mar_te_m,
        "test_margin_std": mar_te_s,
        "expert_test_accuracy_mean": exp_te_m,
        # Écart train − test : mesure directe du surapprentissage.
        "overfit_gap": round(acc_tr_m - acc_te_m, 4),
        # Gain réel de la calibration, hors mémorisation.
        "gain_vs_expert": round(acc_te_m - exp_te_m, 4),
        "folds": rows,
    }


# ──────────────────────────────────────────────
# Pipeline complet
# ──────────────────────────────────────────────

# En dessous, 29 poids libres face à si peu de fichiers : les poids
# mémorisent le corpus au lieu d'apprendre quoi que ce soit.
MIN_FILES_TRUSTWORTHY = 20


def run_calibration(corpus_dir: str | Path, seed: int = 42, variants: int = 2,
                    iters: int = 4000, jobs: int = 1, lam: float = 0.3,
                    folds: int = 5) -> dict:
    """Calibre les poids sur tous les .mid d'un dossier. Retourne un rapport
    sérialisable (poids proposés + validation croisée + diagnostic par axe).

    Les poids livrés sont entraînés sur TOUT le corpus (c'est le meilleur
    estimateur disponible), mais la performance annoncée est celle de la
    validation croisée `validation.test_accuracy_mean` — pas `after.accuracy`,
    qui est mesurée sur les données d'entraînement et monte toujours."""
    paths = sorted(p for p in Path(corpus_dir).rglob("*.mid*") if p.is_file())
    corpus = corpus_vectors(paths, seed, variants, jobs)
    pairs = _pairs(corpus)
    if not pairs:
        raise ValueError(f"aucune paire contrastive exploitable dans {corpus_dir}")
    acc0, mar0 = evaluate(EXPERT_WEIGHTS, pairs)
    w_star = optimize_weights(pairs, seed=seed, iters=iters, lam=lam)
    acc1, mar1 = evaluate(w_star, pairs)
    auc = axis_auc(corpus)
    warnings = []
    if len(corpus) < MIN_FILES_TRUSTWORTHY:
        warnings.append(
            f"corpus de {len(corpus)} fichiers (< {MIN_FILES_TRUSTWORTHY}) : "
            "les poids sont surappris, `after.accuracy` n'est pas une mesure "
            "de généralisation")
    blind = sorted(aid for aid, a in auc.items() if a < 0.5)
    if blind:
        warnings.append(
            f"{len(blind)} axe(s) sous AUC 0.5 (récompensent la dégradation) : "
            + ", ".join(blind))
    return {
        "corpus": str(corpus_dir),
        "n_files": len(corpus),
        "n_pairs": len(pairs),
        "skipped_noop": sum(sk for _, _, sk in corpus.values()),
        "seed": seed,
        "variants": variants,
        # Mesuré sur l'entraînement : à ne PAS citer comme performance.
        "before": {"accuracy": round(acc0, 4), "margin": round(mar0, 4)},
        "after": {"accuracy": round(acc1, 4), "margin": round(mar1, 4)},
        "validation": cross_validate(corpus, k=folds, seed=seed, iters=iters, lam=lam),
        "weights": {aid: round(w, 4) for aid, w in zip(AXIS_IDS, w_star)},
        "discrimination": discrimination(corpus),
        "axis_auc": auc,
        "axis_auc_by_degradation": axis_auc_by_degradation(corpus),
        "warnings": warnings,
    }


def load_weights(path: str | Path) -> dict[str, float]:
    """Charge un fichier de poids (sortie de `calibrate` ou dict direct)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    weights = data.get("weights", data)
    unknown = set(weights) - set(AXIS_IDS)
    if unknown:
        raise ValueError(f"axes inconnus dans {path}: {sorted(unknown)}")
    return {aid: float(w) for aid, w in weights.items()}
