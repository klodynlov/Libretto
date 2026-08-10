"""
Libretto — builder : MidiData → Score symbolique.

Pipeline : grille de mesures (signatures) → chroma/densité/vélocité par
mesure → accord par mesure (gabarits maj/min/dim/7) → mélodie (voix
supérieure échantillonnée par temps) → sections (marqueurs MIDI si présents,
sinon segmentation par nouveauté sur le chroma) → étiquetage heuristique
(intro/verse/chorus/bridge/outro) → Score complet.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from heapq import heappop, heappush

from .midi import MidiData, MidiNote
from .model import Chord, Score, Section, pitch_from_midi

DRUM_CHANNEL = 9

CHORD_TEMPLATES: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7),
    "min": (0, 3, 7),
    "dim": (0, 3, 6),
    "dom7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
}

# Normalisation des labels de marqueurs (anglais + français).
MARKER_LABELS = {
    "intro": "intro", "prelude": "intro",
    "verse": "verse", "couplet": "verse",
    "chorus": "chorus", "refrain": "chorus",
    "bridge": "bridge", "pont": "bridge",
    "outro": "outro", "coda": "outro", "fin": "outro",
    "solo": "solo", "interlude": "interlude", "build": "build",
}

DYN_BANDS = [(32, "pp"), (48, "p"), (64, "mp"), (80, "mf"), (96, "f"), (112, "ff"), (128, "fff")]


def _dyn_label(mean_vel: float) -> str:
    for limit, label in DYN_BANDS:
        if mean_vel < limit:
            return label
    return "fff"


def _texture_label(avg_poly: float) -> str:
    if avg_poly < 1.2:
        return "monophonic solo"
    if avg_poly < 2.5:
        return "2v homophonic"
    if avg_poly < 3.5:
        return "3v"
    return "4v polyphonic"


def meter_context(num: int, den: int) -> tuple[float, int]:
    """(durée d'une pulsation en noires, subdivision) d'après le chiffrage.

    Un chiffrage à dénominateur 8 dont le numérateur est un multiple de 3
    (6/8, 9/8, 12/8) est **composé** : on bat la noire pointée (1.5 noire) et
    la subdivision est ternaire. 3/8 fait exception — trop court pour être
    battu à la noire pointée, il se bat à la croche.
    Partout ailleurs la pulsation est l'unité du dénominateur (4/4 → noire,
    2/2 → blanche, 3/4 → noire)."""
    if den == 8 and num % 3 == 0 and num >= 6:
        return 1.5, 3
    return 4.0 / den, 2


def _bar_starts(md: MidiData) -> list[int]:
    """Ticks de début de chaque mesure, en suivant les changements de
    signature."""
    sigs = sorted(md.time_sigs) or [(0, 4, 4)]
    if sigs[0][0] > 0:
        sigs.insert(0, (0, 4, 4))
    end = max(md.end_tick, 1)
    starts: list[int] = []
    tick = 0
    for idx, (sig_tick, num, den) in enumerate(sigs):
        seg_end = sigs[idx + 1][0] if idx + 1 < len(sigs) else end
        ticks_per_bar = max(1, round(md.ppq * 4 * num / den))
        tick = max(tick, sig_tick)
        while tick < seg_end:
            starts.append(tick)
            tick += ticks_per_bar
    return starts or [0]


def _cosine_dist(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu <= 0 or nv <= 0:
        return 1.0 if (nu > 0) != (nv > 0) else 0.0
    return 1.0 - dot / (nu * nv)


def _best_chord(weights: list[float]) -> Chord | None:
    total = sum(weights)
    if total <= 1e-9 or sum(1 for w in weights if w > 0) < 2:
        return None
    best: tuple[float, int, str] | None = None
    for quality, template in CHORD_TEMPLATES.items():
        for root in range(12):
            pcs = {(root + iv) % 12 for iv in template}
            inside = sum(weights[pc] for pc in pcs)
            outside = total - inside
            score = inside - 0.7 * outside + 0.5 * weights[root]
            if best is None or score > best[0]:
                best = (score, root, quality)
    _score, root, quality = best
    return Chord(pitch_from_midi(48 + root), list(CHORD_TEMPLATES[quality]), quality)


def _self_similarity(features: list[list[float]]) -> list[list[float]]:
    """Matrice d'auto-similarité : cosinus entre chaque paire de mesures.

    C'est la représentation standard en MIR (Foote, 2000). Son intérêt sur la
    nouveauté locale : elle porte la structure **globale** — une section qui
    revient trente mesures plus loin apparaît comme une diagonale parallèle à
    la diagonale principale, information qu'aucune comparaison de fenêtres
    voisines ne peut voir."""
    n = len(features)
    norms = [math.sqrt(sum(x * x for x in f)) or 1.0 for f in features]
    ssm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        fi, ni = features[i], norms[i]
        ssm[i][i] = 1.0
        for j in range(i + 1, n):
            dot = sum(a * b for a, b in zip(fi, features[j]))
            v = dot / (ni * norms[j])
            ssm[i][j] = ssm[j][i] = v
    return ssm


def _repetition_runs(ssm: list[list[float]], min_len: int = 4,
                     quantile: float = 0.94,
                     scale_ratio: int = 3) -> list[tuple[int, int, int]]:
    """Runs diagonaux de répétition (lag, début, longueur), à l'échelle des
    sections.

    Une section rejouée plus loin forme un chemin de forte similarité
    parallèle à la diagonale principale : `ssm[i][i+lag]` reste élevé sur
    toute sa durée. Le seuil est un quantile de la matrice elle-même : les
    valeurs absolues de similarité dépendent trop du matériau pour qu'une
    constante tienne d'un morceau à l'autre.

    **Porte d'échelle.** Toutes les diagonales ne témoignent pas d'un retour
    de section : une pièce dont les sections partagent des phrases de quatre
    mesures produit un treillis d'alignements courts (runs de 4-5 mesures, à
    tous les lags multiples de la phrase) dont chaque bord devenait une
    frontière — jusqu'à treize frontières inventées dans une seule pièce,
    espacées de la longueur de phrase. Les vrais retours, eux, durent une
    section entière : sur les mêmes pièces, leurs runs faisaient 12 à 32
    mesures. Le plus long run de la pièce établit donc l'échelle des
    sections, et un run ne survit que s'il atteint le tiers de cette
    échelle — assez lâche pour qu'un pont de 8 mesures survive à côté d'un
    couplet de 24, assez strict pour que les phrases meurent. Le tiers est
    réglé sur graine 7 (optimum intérieur du balayage 1/1.5..1/4) et validé
    sur graines 11/13 tenues à l'écart : frontières en trop 101 → 53 sur les
    trois corpus, F-mesure en hausse partout (+0.05, +0.09, +0.06)."""
    n = len(ssm)
    if n < 2 * min_len:
        return []
    flat = sorted(ssm[i][j] for i in range(n) for j in range(i + min_len, n))
    if not flat:
        return []
    # Plafond arithmétique du quantile : sur une petite matrice, le
    # quantile brut n'admet pas assez de cellules pour qu'un run de
    # `min_len` puisse exister. Un ternaire de 12 mesures (sections de 4)
    # n'a que 36 paires hors bande : le quantile 0.94 n'en admet que 3, et
    # le retour A-A — 4 cellules, la définition même de la forme — était
    # invisible quelle que soit sa netteté. Le budget est de 3 × min_len et
    # non min_len : les cellules admises se partagent entre TOUTES les
    # diagonales, et les alignements de phrase (lag 4, cellules à 0.98+)
    # mangeaient le budget du vrai retour — sur deux ternaires de la graine
    # 7, le run A-A perdait 1 à 2 cellules sur 4 et mourait. Facteur 3 :
    # optimum intérieur du balayage 2..5 (plateau à 3-5), validé sur les
    # graines 11/23/31 tenues à l'écart — F-mesure +0.02/+0.02/+0.00,
    # comptes de sections exacts +0/+1/+0, jamais négatif. Sur les grandes
    # matrices le plafond est inactif (n=24 : 210 paires, quantile 0.94
    # déjà plus strict). Plancher à la moitié des paires : un budget plus
    # grand que la matrice (loop de 8 mesures : 10 paires) rendrait le
    # quantile négatif — on n'admet jamais plus d'une paire sur deux.
    budget = min(3 * min_len, len(flat) // 2)
    q_eff = min(quantile, 1.0 - budget / len(flat))
    threshold = flat[min(len(flat) - 1, int(len(flat) * q_eff))]
    runs: list[tuple[int, int, int]] = []
    for lag in range(min_len, n - min_len + 1):
        run = 0
        for i in range(n - lag):
            if ssm[i][i + lag] >= threshold:
                run += 1
            else:
                if run >= min_len:
                    runs.append((lag, i - run, run))
                run = 0
        if run >= min_len:
            runs.append((lag, n - lag - run, run))
    if not runs:
        return []
    scale = max(length for _, _, length in runs)
    return [(lag, start, length) for lag, start, length in runs
            if length * scale_ratio >= scale]


def _repetition_edges(ssm: list[list[float]], min_len: int = 4,
                      quantile: float = 0.94,
                      scale_ratio: int = 3) -> set[int]:
    """Bords des segments répétés : là où un chemin diagonal commence et
    finit, il y a une frontière — y compris quand la nouveauté locale ne
    voit rien, cas typique d'un couplet qui revient à l'identique après un
    refrain. Voir _repetition_runs pour le seuil et la porte d'échelle."""
    n = len(ssm)
    edges: set[int] = set()
    for lag, start, length in _repetition_runs(ssm, min_len, quantile,
                                               scale_ratio):
        edges.update((start, start + length, start + lag, start + length + lag))
    return {b for b in edges if 0 < b < n}


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _detect_boundaries(features: list[list[float]], window: int = 4, min_len: int = 4,
                       k: float = 1.5) -> list[int]:
    """Frontières de sections par nouveauté : distance cosinus entre les
    moyennes des fenêtres avant/après chaque mesure.

    Deux sources de frontières, complémentaires. La **nouveauté** repère les
    ruptures ; les **bords de répétition**, lus sur la matrice
    d'auto-similarité, repèrent les retours — un couplet qui revient après un
    refrain ne crée aucune nouveauté (son matériau est déjà connu) mais forme
    une diagonale nette dans la matrice. C'est ce second terme qui porte tout
    le gain : F-mesure des frontières 0.55 → 0.73 sur trois corpus annotés.

    Le noyau en damier de Foote (2000) a été implémenté puis **retiré** : il
    remplaçait la nouveauté par fenêtres sans jamais faire mieux (0.52 contre
    0.55 en moyenne, et 0.45 contre 0.55 sur l'un des corpus). Il n'avait
    l'air gagnant que sur le corpus ayant servi à régler son seuil. Probable
    explication : sur des features par mesure — et non par trame audio, pour
    quoi la méthode a été conçue — un noyau de huit mesures fait exactement
    le travail des deux fenêtres voisines, en moins robuste.

    Le seuil est **robuste** (médiane + k·MAD), pas moyenne + k·σ. La raison
    est empirique : une seule mesure aberrante — typiquement la mesure de
    queue quasi vide que crée une note tenue qui déborde — produit une
    nouveauté de 1.0 là où les vraies frontières sont autour de 0.10. Elle
    tirait la moyenne ET l'écart-type assez haut pour faire passer le seuil
    au-dessus de toutes les frontières réelles : le morceau entier ressortait
    en une seule section. La médiane et le MAD ignorent l'aberration.

    Les `min_len` premières et dernières mesures sont exclues du calcul du
    seuil : leur fenêtre est tronquée, donc leur nouveauté est un artefact de
    bord, et elles ne peuvent de toute façon pas porter de frontière."""
    n = len(features)
    if n <= 2 * min_len:
        return [0]

    novelty = [0.0] * n
    for b in range(1, n):
        left = features[max(0, b - window):b]
        right = features[b:min(n, b + window)]
        mean_l = [sum(col) / len(left) for col in zip(*left)]
        mean_r = [sum(col) / len(right) for col in zip(*right)]
        novelty[b] = _cosine_dist(mean_l, mean_r)

    # Au-delà, la matrice n×n coûte plus qu'elle ne rapporte (une pièce de
    # 600 mesures est déjà très longue).
    runs = _repetition_runs(_self_similarity(features), min_len=min_len) \
        if n <= 600 else []
    repeats: set[int] = set()
    for lag, start, length in runs:
        repeats.update((start, start + length, start + lag,
                        start + length + lag))
    repeats = {b for b in repeats if 0 < b < n}

    candidates = list(range(min_len, n - min_len + 1))
    inner = [novelty[b] for b in candidates]
    med = _median(inner)
    mad = _median([abs(x - med) for x in inner])
    if mad <= 1e-9:
        # Nouveauté plate : soit la pièce est vraiment homogène, soit elle
        # est trop courte pour trancher. Dans les deux cas, pas de frontière
        # inventée.
        return [0]
    threshold = med + k * mad

    # Deux sources de frontières, complémentaires : la nouveauté repère les
    # ruptures, les bords de répétition repèrent les retours. Un couplet qui
    # revient après un refrain se voit mal en nouveauté (le matériau est déjà
    # connu) mais très bien en répétition.
    strong = [b for b in candidates
              if novelty[b] >= threshold and novelty[b] == max(novelty[max(0, b - 2):b + 3])]

    # Recalage du pic sur la transition exacte. La nouveauté FENÊTRÉE est
    # lissée sur `window` mesures et biaisée vers l'amont : une fin de
    # section (cadence, creux, remplissage) diffère du corps de sa section,
    # donc la courbe monte AVANT la frontière — mesuré sur trois corpus,
    # les frontières appariées sortaient 2 mesures trop tôt quatre fois
    # plus souvent que trop tard (22 contre 6). La nouveauté PAS-À-PAS
    # (distance entre deux mesures consécutives), elle, est maximale à la
    # transition même : chaque pic fenêtré est recalé sur son argmax local.
    # Les bords de répétition ne sont pas recalés — leurs positions sont
    # exactes par construction (débuts et fins de chemins diagonaux).
    step = [0.0] * n
    for b in range(1, n):
        step[b] = _cosine_dist(features[b - 1], features[b])
    lo_c, hi_c = candidates[0], candidates[-1]
    snapped = []
    for b in strong:
        zone = range(max(lo_c, b - 2), min(hi_c, b + 2) + 1)
        snapped.append(max(zone, key=lambda x: step[x]))
    strong = snapped
    nov_set = set(strong)

    # Les bords de répétition ont aussi le droit aux zones que `candidates`
    # interdit aux pics de nouveauté : une intro de 2 ou 3 mesures place la
    # première frontière SOUS min_len, où aucun pic n'est admis — mais si un
    # run démarre exactement là, le matériau répété commence là, preuve
    # exacte par construction. Symétrique en queue : un run qui s'arrête à
    # 2 mesures de la fin borne un outro court. Le plancher de 2 écarte les
    # sections d'une mesure, que rien dans le corpus ne justifie.
    head_zone = range(2, min_len)
    tail_zone = range(n - min_len + 1, n - 1)
    rep_set: set[int] = set()
    for b in sorted(repeats):
        if (b in candidates or b in head_zone or b in tail_zone) \
                and b not in strong:
            strong.append(b)
            rep_set.add(b)

    # Le filtre d'espacement arbitre les conflits par **classe de preuve**,
    # pas par position. Un bord de répétition est exact par construction
    # (début ou fin d'un chemin diagonal) ; un pic de nouveauté recalé reste
    # une estimation. Quand les deux tombent à moins de `min_len` l'un de
    # l'autre, le bord de répétition évince le pic — le glouton
    # gauche-à-droite pur laissait un pic recalé deux mesures trop tôt
    # bloquer la vraie frontière juste derrière lui (défaut resté invisible
    # tant que les bords parasites de phrase absorbaient le conflit
    # d'espacement à leur place).
    boundaries = [0]
    for b in sorted(set(strong)):
        if b - boundaries[-1] >= min_len:
            boundaries.append(b)
        elif b in rep_set and len(boundaries) == 1 and b >= 2:
            # Première frontière sous min_len : l'intro courte, admise
            # uniquement sur preuve de répétition (le run démarre là).
            boundaries.append(b)
        elif b in rep_set and boundaries[-1] in nov_set:
            prev = boundaries[-2] if len(boundaries) > 1 else None
            if prev is None or b - prev >= min_len:
                boundaries[-1] = b
    # Une note qui sonne au-delà de la dernière mesure pleine crée une queue
    # d'une poignée de mesures : on la fusionne avec la section précédente —
    # sauf preuve de répétition : un run qui s'arrête là borne un outro
    # court, ce n'est pas un sillage.
    if len(boundaries) > 1 and n - boundaries[-1] < min_len \
            and boundaries[-1] not in rep_set:
        boundaries.pop()

    return _consistent_boundaries(boundaries, runs)


def _consistent_boundaries(boundaries: list[int],
                           runs: list[tuple[int, int, int]]) -> list[int]:
    """Cohérence des frontières à travers les répétitions.

    Une frontière strictement à l'intérieur d'un run de répétition affirme
    que le même matériau se coupe ici — alors il doit aussi se couper à
    ±lag, dans l'autre copie du run. Jumelle absente (à ±1 mesure) dans
    tous les runs couvrants : la frontière est contredite par la structure
    même qui la contient, on la supprime. Le cas légitime AB|AB reste
    sain — la coupure A|B de la première copie a sa jumelle dans la
    seconde, chacune sauve l'autre. Typiquement : un refrain de 8 mesures
    coupé en deux par un pic de nouveauté à la couture de phrase (mesure
    16 d'un refrain 12-20 rejoué en 36-44) — la jumelle exigée en 40
    n'existe pas, la coupure tombe.

    Les jumelles se lisent dans les frontières d'AVANT filtrage : deux
    coupures qui se répondent à ±lag se confirment l'une l'autre, même si
    d'autres tombent autour d'elles.

    Mesuré sur graine 7 : comptes exacts 9 → 11/23, F-mesure
    0.824 → 0.838 ; validé sur 11/23/31 tenues à l'écart : comptes
    +1/+2/+0, F-mesure +0.01/+0.01/+0.01, jamais négatif."""
    if not runs or len(boundaries) < 2:
        return boundaries
    anchored = set(boundaries)
    kept = [boundaries[0]]
    for b in boundaries[1:]:
        partners = []
        for lag, start, length in runs:
            if start < b < start + length:
                partners.append(b + lag)
            elif start + lag < b < start + length + lag:
                partners.append(b - lag)
        if not partners or any(
                any(abs(b2 - p) <= 1 for b2 in anchored)
                for p in partners):
            kept.append(b)
    return kept


# Deux sections sont « le même matériau » quand leurs mesures se ressemblent
# une à une au-delà de ce seuil. Réglé sur la graine 7 (plateau 0.975-0.98,
# milieu du fossé observé : reprises exactes ≥ 0.994, matériaux distincts
# ≤ 0.972), validé sur les graines 11/23/31 tenues à l'écart.
SECTION_SIM_THRESHOLD = 0.975


def _aligned_section_sim(a: list[list[float]], b: list[list[float]]) -> float:
    """Similarité de deux sections par alignement mesure à mesure.

    Le cosinus des MOYENNES par section — l'ancienne mesure — écrase la
    séquence : deux sections au même registre et à la même énergie sortaient
    à 0.99 même bâties sur des matériaux différents, quand un couplet et son
    refrain bâtis sur la même grille sortaient eux aussi à 0.98 — tout se
    jouait dans la troisième décimale, et aucun seuil ne tenait. Comparer
    les mesures une à une — ce que la diagonale de la matrice
    d'auto-similarité fait déjà pour les frontières — écarte l'étau :
    reprises d'une même section ≥ 0.99, matériaux distincts ≤ 0.97, un
    couplet contre son refrain tombe à 0.67.

    Le préfixe commun est pondéré par L/max(la, lb) : sans cette pénalité,
    une intro de quatre mesures égale aux quatre premières mesures d'un
    couplet de huit en serait indiscernable."""
    L = min(len(a), len(b))
    if L == 0:
        return 0.0
    s = sum(1.0 - _cosine_dist(a[k], b[k]) for k in range(L)) / L
    return s * (L / max(len(a), len(b)))


def _assign_letters(section_bars: list[list[list[float]]],
                    sim_threshold: float | None = None) -> list[str]:
    """Regroupe les sections par matériau (A, B, C…) — agglomératif à
    liaison moyenne sur la similarité alignée, seuil absolu.

    L'ancien regroupement était glouton (chaque section comparée au premier
    représentant de chaque groupe) sur le cosinus des moyennes, avec un
    seuil adaptatif à la pièce — nécessaire parce que cette similarité-là
    écrasait tout vers 1.0 (cf. _aligned_section_sim). Sur la similarité
    alignée, l'échelle redevient absolue : « même section rejouée » a une
    signature (≥ 0.99) qui ne dépend plus du matériau, et le seuil adaptatif
    redevenait le maillon faible — il descendait sous les paires
    couplet/pont (0.92) dès que la pièce était homogène. Frontières vraies
    injectées, graine 7 : l'exactitude de partition passe de 4/23 (glouton
    adaptatif) à 17/23 (agglomératif absolu).

    Un morceau sans AUCUNE paire au-dessus du seuil ressort tout en
    singletons : c'est le verdict voulu pour un travers-composé, dont les
    sections ne reviennent jamais."""
    n = len(section_bars)
    if n < 2:
        return ["A"] * n
    if sim_threshold is None:
        sim_threshold = SECTION_SIM_THRESHOLD
    sim = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            sim[i][j] = sim[j][i] = _aligned_section_sim(section_bars[i],
                                                         section_bars[j])
    clusters: list[list[int]] = [[i] for i in range(n)]

    def link(a: list[int], b: list[int]) -> float:
        return sum(sim[i][j] for i in a for j in b) / (len(a) * len(b))

    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                s = link(clusters[x], clusters[y])
                if best is None or s > best[0]:
                    best = (s, x, y)
        s, x, y = best
        if s < sim_threshold:
            break
        clusters[x] = clusters[x] + clusters[y]
        del clusters[y]

    owner = [0] * n
    for members in clusters:
        for i in members:
            owner[i] = min(members)
    letters: list[str] = []
    mapping: dict[int, str] = {}
    for i in range(n):
        if owner[i] not in mapping:
            mapping[owner[i]] = chr(ord("A") + len(mapping))
        letters.append(mapping[owner[i]])
    return letters


def _name_sections(letters: list[str], energies: list[float]) -> list[str]:
    """Heuristique de nommage à partir des lettres de clusters et de
    l'énergie mesurée. Fallback : la lettre elle-même."""
    n = len(letters)
    labels = list(letters)
    if n < 2:
        return labels
    counts = Counter(letters)
    sorted_e = sorted(energies)
    median_e = sorted_e[n // 2]
    mean_energy = {
        letter: sum(e for lt, e in zip(letters, energies) if lt == letter) / c
        for letter, c in counts.items()
    }
    repeated = [lt for lt, c in counts.items() if c >= 2]
    chorus = max(repeated, key=lambda lt: mean_energy[lt]) if repeated else None
    verse = None
    others = [lt for lt in repeated if lt != chorus]
    if others:
        verse = max(others, key=lambda lt: counts[lt])
    # Chaque lettre singleton du milieu reçoit un nom DISTINCT : tout
    # nommer « bridge » fusionnait des matériaux que le clustering avait
    # séparés — un travers-composé A-B-C-D-E ressortait intro-bridge-
    # bridge-bridge-outro, et l'axe 3 (diversité) comptait 3 types au lieu
    # de 5. Le nom est une présentation ; il n'a pas le droit de détruire
    # la partition qu'il présente.
    singleton_names = iter(["bridge", "interlude", "solo"])
    singleton_by_letter: dict[str, str] = {}
    for i, letter in enumerate(letters):
        if letter == chorus:
            labels[i] = "chorus"
        elif letter == verse:
            labels[i] = "verse"
        elif counts[letter] == 1 and 0 < i < n - 1:
            labels[i] = singleton_by_letter.setdefault(
                letter, next(singleton_names, letter))
    if counts[letters[0]] == 1 and energies[0] <= median_e:
        labels[0] = "intro"
    if counts[letters[-1]] == 1 and energies[-1] <= median_e:
        labels[-1] = "outro"
    return labels


def build_score(md: MidiData) -> Score:
    notes = [n for n in md.notes if n.channel != DRUM_CHANNEL]
    all_notes = md.notes
    if not all_notes:
        return Score()

    ppq = md.ppq
    bar_starts = _bar_starts(md)
    n_bars = len(bar_starts)

    def bar_of(tick: int) -> int:
        return min(n_bars - 1, max(0, bisect_right(bar_starts, tick) - 1))

    # ── features par mesure ──
    chroma = [[0.0] * 12 for _ in range(n_bars)]
    density = [0] * n_bars
    vel_sum = [0.0] * n_bars
    vel_count = [0] * n_bars
    pitch_sum = [0.0] * n_bars
    melodic_count = [0] * n_bars
    for n in notes:
        b = bar_of(n.start)
        dur = max(1, n.end - n.start)
        chroma[b][n.pitch % 12] += dur * n.velocity / 127.0
        pitch_sum[b] += n.pitch
        melodic_count[b] += 1
    for n in all_notes:
        b = bar_of(n.start)
        density[b] += 1
        vel_sum[b] += n.velocity
        vel_count[b] += 1

    # ── accord par mesure ──
    chords_by_bar: list[Chord | None] = [_best_chord(chroma[b]) for b in range(n_bars)]

    # ── mélodie (voix supérieure) et polyphonie, échantillonnées par temps ──
    # Balayage par événements : l'ancien code relisait toutes les notes à
    # chaque temps — quadratique, et c'était le coût dominant sur les MIDI
    # denses (~600 ms pour 24 000 notes, là où tout le reste tient en 40 ms).
    # La clé du tas reproduit le choix de max(key=pitch) : hauteur maximale,
    # puis PREMIÈRE note dans l'ordre du fichier en cas d'égalité — un
    # sommet ex æquo doit désigner la même note qu'avant.
    total_beats = max(1, math.ceil(md.end_tick / ppq))
    melody_samples: list[tuple[int, MidiNote]] = []  # (beat, note)
    poly_by_beat: list[int] = []
    by_start = sorted(range(len(notes)), key=lambda i: notes[i].start)
    by_end = sorted(range(len(notes)), key=lambda i: notes[i].end)
    heap: list[tuple[int, int]] = []  # (-pitch, index d'origine), retrait paresseux
    pitch_count = [0] * 128
    distinct = 0
    si = ei = 0
    last_note: MidiNote | None = None
    for beat in range(total_beats):
        tick = beat * ppq
        while si < len(by_start) and notes[by_start[si]].start <= tick:
            i = by_start[si]
            heappush(heap, (-notes[i].pitch, i))
            if pitch_count[notes[i].pitch] == 0:
                distinct += 1
            pitch_count[notes[i].pitch] += 1
            si += 1
        while ei < len(by_end) and notes[by_end[ei]].end <= tick:
            p = notes[by_end[ei]].pitch
            pitch_count[p] -= 1
            if pitch_count[p] == 0:
                distinct -= 1
            ei += 1
        poly_by_beat.append(distinct)
        while heap and notes[heap[0][1]].end <= tick:
            heappop(heap)
        if heap:
            top = notes[heap[0][1]]
            if top is not last_note:
                melody_samples.append((beat, top))
                last_note = top

    # ── frontières de sections ──
    # Features de segmentation. Le chroma seul ne suffit pas : normalisé L2,
    # il efface l'intensité, si bien qu'un refrain fort et un couplet doux
    # bâtis sur la même grille d'accords deviennent indiscernables — c'est
    # exactement la frontière qu'un auditeur entend en premier. On lui adjoint
    # donc l'énergie (densité, vélocité) et le registre, à un poids qui les
    # rend comparables aux 12 dimensions harmoniques (de norme 1 après
    # normalisation).
    max_den = max(density) or 1
    max_vel_bar = max((vel_sum[b] / vel_count[b] for b in range(n_bars) if vel_count[b]),
                      default=0.0)
    # Geste mélodique par mesure : intervalle moyen de la surface (voix
    # haute par instant d'attaque) et durée moyenne des notes. Le chroma
    # moyenné est aveugle aux pièces qui changent d'harmonie à chaque
    # mesure (travers-composé) : le contraste inter-sections s'y noie dans
    # le contraste intra-section, alors que le passage de pas conjoints à
    # grands sauts, ou de croches à blanches, saute à l'oreille.
    notes_by_bar: list[list[MidiNote]] = [[] for _ in range(n_bars)]
    for n in notes:
        notes_by_bar[bar_of(n.start)].append(n)
    interval_mean = [0.0] * n_bars
    dur_mean = [0.0] * n_bars
    for b in range(n_bars):
        surface: dict[int, MidiNote] = {}
        for n in notes_by_bar[b]:
            if n.start not in surface or n.pitch > surface[n.start].pitch:
                surface[n.start] = n
        line = [surface[t].pitch for t in sorted(surface)]
        if len(line) >= 2:
            ivals = [abs(y - x) for x, y in zip(line, line[1:])]
            interval_mean[b] = min(1.0, sum(ivals) / len(ivals) / 12.0)
        if notes_by_bar[b]:
            bar_len = (bar_starts[b + 1] - bar_starts[b]) if b + 1 < n_bars \
                else max(1, md.end_tick - bar_starts[b])
            mean_d = sum(n.end - n.start for n in notes_by_bar[b]) / len(notes_by_bar[b])
            dur_mean[b] = min(1.0, mean_d / bar_len)
    ENERGY_W = 0.6
    GESTURE_W = 0.3
    features = []
    for b in range(n_bars):
        norm = math.sqrt(sum(x * x for x in chroma[b])) or 1.0
        feat = [x / norm for x in chroma[b]]
        feat.append(ENERGY_W * density[b] / max_den)
        mean_v = (vel_sum[b] / vel_count[b]) if vel_count[b] else 0.0
        feat.append(ENERGY_W * (mean_v / max_vel_bar if max_vel_bar else 0.0))
        # Registre moyen des notes RÉELLEMENT mélodiques : diviser par
        # `density` (percussions comprises) diluait la hauteur en proportion
        # du nombre de coups de batterie, un artefact d'orchestration.
        feat.append(ENERGY_W * (pitch_sum[b] / melodic_count[b] / 127.0
                                if melodic_count[b] else 0.0))
        feat.append(GESTURE_W * interval_mean[b])
        feat.append(GESTURE_W * dur_mean[b])
        features.append(feat)

    # Les mesures de queue vides ne sont pas de la musique, seulement le
    # sillage d'une note tenue : la segmentation les ignore (leur nouveauté
    # maximale fausserait le seuil), et le clustering des sections aussi —
    # elles allongeaient la dernière section de mesures nulles, ce que la
    # pénalité de longueur de _aligned_section_sim payait à tort.
    last_sounding = max((b for b in range(n_bars) if density[b] > 0), default=0)
    marker_bars = sorted({bar_of(t) for t, _ in md.markers})
    if len(marker_bars) >= 2:
        boundaries = sorted(set([0] + marker_bars))
        marker_by_bar = {bar_of(t): text for t, text in md.markers}
        raw_labels = []
        for b in boundaries:
            text = marker_by_bar.get(b, "").strip().lower()
            key = next((v for k, v in MARKER_LABELS.items() if k in text), None)
            raw_labels.append(key or text or "section")
    else:
        boundaries = _detect_boundaries(features[:last_sounding + 1])
        raw_labels = None

    boundaries = sorted(set(boundaries))
    edges = boundaries + [n_bars]

    # ── construction des sections ──
    tempo_events = sorted(md.tempos) or [(0, 120.0)]

    def tempo_at(tick: int) -> float:
        current = tempo_events[0][1]
        for t, bpm in tempo_events:
            if t <= tick:
                current = bpm
            else:
                break
        return current

    # Contexte métrique : nécessaire dès la construction des sections pour
    # exprimer les attaques en phase de pulsation (voir Score.pulse_beats).
    first_sig = (sorted(md.time_sigs) or [(0, 4, 4)])[0]
    pulse_beats, subdivision = meter_context(first_sig[1], first_sig[2])

    # Polyphonie par mesure : moyenne des temps couverts par la mesure.
    poly_by_bar: list[float] = []
    for b in range(n_bars):
        beat_a = math.floor(bar_starts[b] / ppq)
        beat_b = (math.floor(bar_starts[b + 1] / ppq) if b + 1 < n_bars
                  else len(poly_by_beat))
        window = [p for p in poly_by_beat[beat_a:max(beat_b, beat_a + 1)] if p > 0]
        poly_by_bar.append(sum(window) / len(window) if window else 0.0)

    sections: list[Section] = []
    section_bar_feats: list[list[list[float]]] = []
    energies: list[float] = []
    max_vel_piece = max((n.velocity for n in all_notes), default=1)
    # Répartition des notes par section en un passage (bisect sur les ticks
    # de départ), au lieu de refiltrer tout le fichier à chaque section.
    # L'ordre d'origine des notes est conservé dans chaque seau — les
    # onset_beats en dépendent.
    sec_start_ticks = [bar_starts[edges[idx]] for idx in range(len(edges) - 1)]
    notes_by_section: list[list[MidiNote]] = [[] for _ in sec_start_ticks]
    for n in all_notes:
        k = bisect_right(sec_start_ticks, n.start) - 1
        if k >= 0:
            notes_by_section[k].append(n)
    for idx in range(len(edges) - 1):
        b0, b1 = edges[idx], edges[idx + 1]
        if b1 <= b0:
            continue
        start_tick = bar_starts[b0]
        end_tick = bar_starts[b1] if b1 < n_bars else md.end_tick + 1
        sec_notes = notes_by_section[idx]
        harmony = [chords_by_bar[b] for b in range(b0, b1) if chords_by_bar[b] is not None]
        beat0 = math.ceil(start_tick / ppq)
        beat1 = math.ceil(end_tick / ppq)
        melody = [pitch_from_midi(note.pitch)
                  for beat, note in melody_samples if beat0 <= beat < beat1]
        polys = [p for p in poly_by_beat[beat0:min(beat1, len(poly_by_beat))] if p > 0]
        mean_vel = (sum(n.velocity for n in sec_notes) / len(sec_notes)) if sec_notes else 0.0
        n_sec_bars = b1 - b0
        onset_beats = ([round(n.start / ppq, 4) for n in sec_notes if n.channel != DRUM_CHANNEL]
                       or [round(n.start / ppq, 4) for n in sec_notes])
        section = Section(
            id=f"s{idx:02d}",
            start_bar=b0 + 1,
            end_bar=b1 + 1,
            label="section",
            harmony=harmony,
            melody_pitches=melody,
            tempo=round(tempo_at(start_tick)),
            mean_velocity=round(mean_vel, 2),
            avg_polyphony=round(sum(polys) / len(polys), 2) if polys else 0.0,
            note_density=round(len(sec_notes) / n_sec_bars, 2) if n_sec_bars else 0.0,
            onset_beats=onset_beats,
            onset_phases=[round((b / pulse_beats) % 1.0, 4) for b in onset_beats],
            poly_by_bar=poly_by_bar[b0:b1],
            # `chroma` est déjà pondéré durée × vélocité et exclut les
            # percussions : c'est exactement l'histogramme dont l'estimation
            # tonale a besoin, il ne restait qu'à le conserver.
            pc_weights=[round(sum(chroma[b][pc] for b in range(b0, b1)), 4)
                        for pc in range(12)],
        )
        sections.append(section)
        section_bar_feats.append([features[b]
                                  for b in range(b0, min(b1, last_sounding + 1))])
        energies.append(0.6 * mean_vel / max_vel_piece
                        + 0.4 * min(1.0, (len(sec_notes) / max(1, n_sec_bars)) / 12.0))

    if not sections:
        return Score()

    # ── labels ──
    letters = _assign_letters(section_bar_feats)
    if raw_labels is not None:
        for section, label in zip(sections, raw_labels):
            section.label = label
    else:
        for section, label in zip(sections, _name_sections(letters, energies)):
            section.label = label
    for section, letter in zip(sections, letters):
        section.id = f"{letter}_{section.id}"

    # ── cartes globales ──
    dynamics = [(s.start_bar, _dyn_label(s.mean_velocity)) for s in sections if s.mean_velocity > 0]
    texture_map = [(s.start_bar, _texture_label(s.avg_polyphony)) for s in sections
                   if s.avg_polyphony > 0]
    tempo_map = []
    seen_bars = set()
    for tick, bpm in tempo_events:
        b = bar_of(tick) + 1
        if b not in seen_bars:
            tempo_map.append((b, round(bpm)))
            seen_bars.add(b)

    # tonalité globale estimée (informatif)
    from .axes import estimate_key  # import tardif : évite un cycle
    global_hist = [0.0] * 12
    for row in chroma:
        for pc in range(12):
            global_hist[pc] += row[pc]
    key_root, _mode, _corr, _margin = estimate_key(global_hist)

    return Score(
        sections=sections,
        key_signature=pitch_from_midi(60 + key_root),
        time_signature_num=first_sig[1],
        time_signature_den=first_sig[2],
        tempo_map=tempo_map,
        dynamics=dynamics,
        texture_map=texture_map,
        pulse_beats=pulse_beats,
        subdivision=subdivision,
    )
