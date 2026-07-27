"""
Libretto — moteur SMS v2 : 29 axes structurels.

Corrections vs v1 :
- tous les scores sont bornés [0, 1] (clamp central dans StructuralAxis) ;
- toute l'arithmétique d'intervalles se fait en classes de hauteur (mod 12),
  insensible à l'octave et aux enharmonies ;
- tonalité estimée par corrélation avec les profils de Krumhansl-Kessler ;
- motifs mélodiques détectés sur les séquences d'intervalles (invariants
  par transposition), plus sur les hauteurs absolues ;
- pondérations centralisées dans AXES_META, somme exacte = 1.0 ;
- l'axe 24 (doublon de l'axe 2 en v1) mesure désormais la carrure
  hypermétrique (sections en multiples de 4 mesures) ;
- l'axe 29 est une vraie synthèse (cohérence inter-groupes) au lieu de
  recalculer les axes 7/8 avec un plancher arbitraire.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .model import Chord, Pitch, Score, Section, PC_NAMES

# ──────────────────────────────────────────────
# Helpers numériques
# ──────────────────────────────────────────────

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def band(x: float, a: float, b: float, c: float, d: float) -> float:
    """Score trapézoïdal : 0 avant a, montée a→b, plateau 1 sur [b, c],
    descente c→d, 0 après. Remplace les heuristiques ad hoc `1 - |x-cible|/λ`
    de la v1 (dont plusieurs sortaient de [0, 1])."""
    if x < a or x > d:
        return 0.0
    if x < b:
        return (x - a) / (b - a) if b > a else 1.0
    if x <= c:
        return 1.0
    return (d - x) / (d - c) if d > c else 1.0


def pearson(xs: list[float], ys: list[float]) -> float:
    """Corrélation de Pearson, bornée [-1, 1]. La v1 utilisait la pente de
    régression non bornée, d'où des scores > 1."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def data_confidence(n: float, minimum: float, comfortable: float) -> float:
    """Confiance tirée d'une quantité de matière : 0 sous `minimum`, 1 à
    partir de `comfortable`, montée linéaire entre les deux.

    Les seuils sont ceux en dessous desquels la grandeur mesurée n'existe
    pas — on ne parle pas de « symétrie formelle » avec deux sections, ni de
    « développement motivique » sur six notes. La v0.2 renvoyait 0.0 dans ces
    cas, indiscernable d'un vrai zéro bien mesuré."""
    if comfortable <= minimum:
        return 1.0 if n >= comfortable else 0.0
    return clamp01((n - minimum) / (comfortable - minimum))


# Confiance d'un axe qui n'a plus que son repli à offrir (textures au lieu
# d'onsets, nuances au lieu de vélocités) : le score reste informatif, la
# source est indirecte.
PROXY_CONFIDENCE = 0.3


def _entropy_norm(counts: Counter, max_classes: int) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) < 2:
        return 0.0
    h = -sum((c / total) * math.log(c / total) for c in counts.values())
    return clamp01(h / math.log(max(2, max_classes)))


# ──────────────────────────────────────────────
# Tonalité : profils de Krumhansl-Kessler
# ──────────────────────────────────────────────

KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _swap(profile: list[float], a: int, b: int) -> list[float]:
    out = list(profile)
    out[a], out[b] = out[b], out[a]
    return out


# Profils modaux, construits — non mesurés sur un corpus d'écoute comme
# l'ont été ceux de Krumhansl et Kessler. Chaque mode est son parent dont le
# **degré caractéristique** est échangé avec son voisin chromatique : le
# dorien est un mineur qui monte sa sixte, le mixolydien un majeur qui
# descend sa septième. Le reste du profil parent est laissé intact, faute de
# quoi on inventerait des poids que rien n'étaye.
#
# Ils existent parce que KK, qui ne connaît que deux profils, lit une pièce
# mixolydienne à la sous-dominante — 3/22 sur le corpus de contrôle, et
# l'erreur est à chaque fois la même (voir README, *Tonalité*). Ce sont des
# candidats à valider, pas un correctif : `estimate_key` ne les utilise que
# si on les lui passe.
KK_DORIAN = _swap(KK_MINOR, 8, 9)        # ♭6 ↔ ♮6
KK_MIXOLYDIAN = _swap(KK_MAJOR, 10, 11)  # ♭7 ↔ ♮7

# Vocabulaire de Krumhansl-Kessler seul — ce qui était le défaut jusqu'à la
# bascule. Gardé nommé : c'est le témoin de toutes les mesures antérieures.
KEY_PROFILES = {"maj": KK_MAJOR, "min": KK_MINOR}
# Vocabulaire par défaut depuis la bascule. Dorien et mixolydien seulement :
# ce sont les deux modes dont `make_corpus` écrit la vérité terrain, donc les
# deux qu'on sait valider. En ajouter d'autres reviendrait à livrer non
# mesuré ce que ce module reproche à KK.
MODAL_PROFILES = {**KEY_PROFILES, "dorien": KK_DORIAN, "mixolydien": KK_MIXOLYDIAN}

# Mode -> parent majeur/mineur, pour les consommateurs qui raisonnent en
# gammes (axes 9 et 14) et pour les étiquettes de packs, qui ne distinguent
# qu'entre « m » et le reste.
PARENT_MODE = {"maj": "maj", "min": "min", "dorien": "min", "mixolydien": "maj"}

MAJOR_SCALE = {0, 2, 4, 5, 7, 9, 11}
MINOR_SCALE = {0, 2, 3, 5, 7, 8, 10, 11}  # mineur naturel + sensible


def estimate_key(hist: list[float],
                 profiles: dict[str, list[float]] | None = None
                 ) -> tuple[int, str, float, float]:
    """(classe de hauteur de la tonique, mode, corrélation, marge sur la 2e
    tonique candidate). hist = poids par classe de hauteur (12).

    `profiles` fixe le vocabulaire de modes ; par défaut `MODAL_PROFILES`,
    c'est-à-dire Krumhansl-Kessler **plus** le dorien et le mixolydien.
    Passer `KEY_PROFILES` restreint aux deux profils d'origine — c'est le
    témoin de toutes les mesures antérieures à la bascule.

    Le mode retourné peut donc valoir « dorien » ou « mixolydien » : tout
    consommateur qui en déduit une gamme doit passer par `PARENT_MODE`.

    L'ordre du dictionnaire départage les ex aequo (tri stable) : le
    résultat reste déterministe, mais dépend de cet ordre.
    """
    profiles = profiles or MODAL_PROFILES
    if sum(hist) <= 0:
        return 0, next(iter(profiles)), 0.0, 0.0
    results = []
    for mode, profile in profiles.items():
        for root in range(12):
            rotated = [profile[(pc - root) % 12] for pc in range(12)]
            results.append((pearson(rotated, hist), root, mode))
    results.sort(key=lambda r: r[0], reverse=True)
    best_corr, best_root, best_mode = results[0]
    margin = 0.0
    for corr, root, _mode in results[1:]:
        if root != best_root:
            margin = best_corr - corr
            break
    return best_root, best_mode, best_corr, margin


def fifths_distance(pc1: int, pc2: int) -> int:
    """Distance sur le cycle des quintes (0-6). C→G = 1, C→F# = 6."""
    p1, p2 = (pc1 * 7) % 12, (pc2 * 7) % 12
    d = abs(p1 - p2)
    return min(d, 12 - d)


# ──────────────────────────────────────────────
# Axe structurel
# ──────────────────────────────────────────────

@dataclass
class StructuralAxis:
    id: str
    name: str
    weight: float
    score: float = 0.0
    details: dict = field(default_factory=dict)
    # Fiabilité du score, dans [0, 1] : quelle matière l'axe a réellement eue
    # à mesurer. 1.0 = mesuré sur des données abondantes ; 0.0 = rien à
    # mesurer, le score est un défaut et ne veut rien dire. Un score sans
    # confiance est un chiffre qui ment poliment.
    confidence: float = 1.0

    def __post_init__(self):
        # Clamp central : plus aucun axe ne peut sortir de [0, 1].
        self.score = clamp01(self.score)
        self.confidence = clamp01(self.confidence)


# id, nom, poids, groupe. Somme des poids = 1.0 (vérifiée par les tests).
#
# Pondération : histoire complète en deux temps (voir resultats_ecoute.md).
#
# Session 3 (corpus GÉNÉRÉ) : les négatifs dynamiques échouaient au test de
# perception sous trois rendus — le générateur produit des rampes de vélocité
# mécaniques, pas de la dynamique musicale. Poids 26/28 réduits (0.010/0.030),
# masse redistribuée vers 12/16/17/20.
#
# Session 5 (corpus d'INTERPRÉTATIONS RÉELLES, MAESTRO, lot complet de 55) :
# la prémisse de la réduction — « aucun corrélat perceptif mesurable » — est
# morte. Sur de la dynamique jouée, l'oreille détecte les DEUX dégradations
# dynamiques (flatten 71 % IC95 0.51-0.85, scramble 73 % IC95 0.52-0.87), et
# l'axe 26 discrimine à AUC 0.93 sous les deux (l'axe 28 : 0.75/0.58). La
# révision de la session 3 est donc ANNULÉE à l'identique — le verdict
# n'était pas « la dynamique ne compte pas » mais « le corpus généré n'en
# contient pas » :
#   26_dynamic_range   0.030   28_emotional_arc   0.050   (restaurés)
#   12_harmonic_rhythm 0.030   16_motivic_dev     0.050   (retour au prior)
#   17_theme_recog     0.040   20_syncopation     0.025
AXES_META: dict[int, tuple[str, str, float, str]] = {
    1:  ("01_section_count",              "Densité formelle",             0.030, "A"),
    2:  ("02_section_balance",            "Équilibre des durées",         0.040, "A"),
    3:  ("03_section_label_diversity",    "Diversité des sections",       0.030, "A"),
    4:  ("04_symmetry",                   "Symétrie formelle",            0.040, "A"),
    5:  ("05_section_progression",        "Progression énergétique",      0.030, "A"),
    6:  ("06_repeat_ratio",               "Ratio de répétition",          0.030, "A"),
    7:  ("07_section_transition",         "Qualité des transitions",      0.040, "A"),
    8:  ("08_key_stability",              "Stabilité tonale",             0.050, "B"),
    9:  ("09_progression_complexity",     "Complexité harmonique",        0.040, "B"),
    10: ("10_cadence_presence",           "Présence de cadences",         0.040, "B"),
    11: ("11_modulation_count",           "Modulations",                  0.030, "B"),
    12: ("12_harmonic_rhythm",            "Rythme harmonique",            0.030, "B"),
    13: ("13_tonal_contrast",             "Contraste tonal",              0.030, "B"),
    14: ("14_bass_melodic",               "Mélodisme de la basse",        0.020, "B"),
    15: ("15_melodic_contour",            "Contour mélodique",            0.040, "C"),
    16: ("16_motivic_development",        "Développement motivique",      0.050, "C"),
    17: ("17_theme_recognition",          "Reconnaissabilité des thèmes", 0.040, "C"),
    18: ("18_melodic_range",              "Étendue mélodique",            0.025, "C"),
    19: ("19_intervallic_character",      "Caractère intervalique",       0.030, "C"),
    20: ("20_melodic_rhythm_syncopation", "Syncopes mélodiques",          0.025, "C"),
    21: ("21_tempo_variation",            "Variations de tempo",          0.030, "D"),
    22: ("22_tempo_consistency",          "Cohérence du tempo",           0.025, "D"),
    23: ("23_rhythmic_complexity",        "Complexité rythmique",         0.030, "D"),
    24: ("24_hypermetric_regularity",     "Carrure hypermétrique",        0.025, "D"),
    25: ("25_texture_variety",            "Variété texturale",            0.040, "E"),
    26: ("26_dynamic_range",              "Gamme dynamique",              0.030, "E"),
    27: ("27_voice_count",                "Polyphonie",                   0.030, "E"),
    28: ("28_emotional_arc",              "Arc émotionnel",               0.050, "F"),
    29: ("29_global_cohesion",            "Cohésion globale",             0.050, "F"),
}

# De quelle matière chaque axe a besoin, et en quelle quantité :
# {axe: (ressource, minimum, confortable)}. Sous `minimum` la grandeur
# mesurée n'existe pas — on ne parle pas de symétrie formelle avec deux
# sections, ni de développement motivique sur six notes ; le score retombe
# alors sur un défaut qui ressemblait à s'y méprendre à une mesure.
#
# Les seuils viennent des gardes déjà présentes dans le code de chaque axe
# (« if len(iv) < 6: return 0.0 »), rendues explicites et graduées : la
# confiance monte progressivement au lieu de basculer d'un coup.
AXIS_DATA_NEEDS: dict[int, tuple[str, float, float]] = {
    1:  ("sections", 1, 3),
    2:  ("sections", 2, 4),
    3:  ("sections", 2, 4),
    4:  ("sections", 3, 6),
    5:  ("sections", 2, 4),
    6:  ("sections", 3, 6),
    7:  ("sections", 2, 4),
    8:  ("harmony", 4, 16),
    9:  ("harmony", 4, 16),
    10: ("harmony", 4, 16),
    11: ("sections", 2, 4),
    12: ("harmony", 4, 16),
    13: ("sections", 2, 4),
    14: ("harmony", 3, 12),
    15: ("melody", 4, 24),
    16: ("melody", 8, 40),
    17: ("melody", 10, 48),
    18: ("melody", 3, 16),
    19: ("melody", 4, 24),
    20: ("onsets", 8, 48),
    21: ("tempo", 2, 4),
    22: ("tempo", 2, 4),
    23: ("onsets", 8, 48),
    24: ("sections", 2, 4),
    25: ("polyphony", 2, 4),
    26: ("velocity", 2, 4),
    27: ("polyphony", 1, 3),
    28: ("sections", 3, 5),
    # 29 est une synthèse : sa confiance est celle des axes qu'il résume.
}

GROUP_NAMES = {
    "A": "Forme & architecture",
    "B": "Harmonie & tonalité",
    "C": "Mélodie & thème",
    "D": "Rythme & tempo",
    "E": "Texture & orchestration",
    "F": "Cohérence globale",
}

DYN_LEVELS = {"ppp": 1, "pp": 2, "p": 3, "mp": 4, "mf": 5, "f": 6, "ff": 7, "fff": 8}

# Fallback quand aucune donnée mesurée (vélocité/densité) n'est disponible.
LABEL_ENERGY = {
    "intro": 1, "prelude": 1, "exposition": 2,
    "verse": 3, "build": 4, "chorus": 5, "refrain": 5,
    "bridge": 4, "interlude": 2, "solo": 6,
    "coda": 3, "outro": 2, "finale": 7,
}


# ──────────────────────────────────────────────
# Moteur SMS
# ──────────────────────────────────────────────

class SenseOfMusicalStructure:
    """Calculateur des 29 axes structurels du SMS.

    `weights` : surcharge optionnelle {axis_id: poids} (sortie de
    `libretto.calibrate`). Les scores d'axes n'en dépendent pas — seuls
    l'agrégat global et les scores de groupes changent."""

    def __init__(self, score: Score, weights: dict[str, float] | None = None):
        self.score = score
        self.axes: list[StructuralAxis] = []
        self._weights = weights or {}

    # ── fabrique d'axe (id/nom/poids centralisés) ──

    def _make(self, num: int, score: float, details: dict | None = None,
              conf: float = 1.0) -> StructuralAxis:
        axis_id, name, weight, _group = AXES_META[num]
        weight = self._weights.get(axis_id, weight)
        return StructuralAxis(axis_id, name, weight, score, details or {}, conf)

    # ── features partagées ──

    def _melody(self) -> list[Pitch]:
        return self.score.all_melody

    def _melody_intervals(self) -> list[int]:
        m = self._melody()
        return [m[i + 1].midi - m[i].midi for i in range(len(m) - 1)]

    def _chord_chain(self) -> list[Chord]:
        return self.score.all_chords

    def _pc_hist(self, sections: list[Section]) -> list[float]:
        """Poids des classes de hauteur, pour l'estimation tonale.

        Les **notes brutes** (durée × vélocité, percussions exclues) quand le
        builder les a conservées, la reconstruction accords + voix supérieure
        sinon — un Score écrit à la main n'a pas de notes.

        Cette préférence n'est pas un raffinement : l'histogramme reconstruit
        pondère la fondamentale de chaque accord détecté ×2 et ses notes ×0.5,
        c'est-à-dire qu'il fait passer par une détection d'accords à un accord
        par mesure une information que les notes portaient déjà. Confronté à
        une tonalité connue, il trouve la tonique dans 57 % des cas contre
        78 % pour les notes brutes (README, *Tonalité*). Il ajoutait de
        l'interprétation là où il ne fallait que compter.
        """
        h = [0.0] * 12
        if any(s.pc_weights for s in sections):
            for s in sections:
                for pc, poids in enumerate(s.pc_weights):
                    h[pc] += poids
            return h
        for s in sections:
            for c in s.harmony:
                h[c.root_pc] += 2.0
                for pc in c.chord_pcs:
                    h[pc] += 0.5
            for p in s.melody_pitches:
                h[p.pc] += 1.0
        return h

    def _grid_alignment(self) -> float:
        """Fraction d'attaques tombant sur la grille de subdivision. Mesure
        la lisibilité métrique : un rythme complexe reste sur la grille,
        c'est ce qui le rend jouable ; du bruit n'y est jamais."""
        sub = max(2, self.score.subdivision)
        phases = [p for s in self.score.sections for p in s.onset_phases]
        if not phases:
            return 1.0
        tol = 0.5 / sub * 0.35
        hits = sum(1 for p in phases
                   if min(abs(p - k / sub) for k in range(sub + 1)) <= tol)
        return band(hits / len(phases), 0.2, 0.7, 1.01, 1.01)

    def _syncopation(self) -> tuple[float, int]:
        """(ratio de syncopes, nb d'attaques). Une attaque est syncopée quand
        elle tombe hors de la pulsation **et** que la pulsation qu'elle occupe
        n'a reçu aucune attaque sur son temps fort — c'est-à-dire quand elle
        remplace le temps au lieu de le compléter.

        Compter simplement les attaques « hors pulsation » ne mesure pas la
        syncope mais la subdivision : une mélodie en croches régulières a
        mécaniquement la moitié (binaire) ou les deux tiers (ternaire) de ses
        attaques hors pulsation sans rien avoir de syncopé.

        Une attaque ne compte que si elle tombe **sur la grille** de
        subdivision. Sans cette condition, décaler les attaques au hasard
        fabriquerait des syncopes à volonté — c'est ce qui arrive quand on
        joue faux, pas quand on syncope."""
        pulse = self.score.pulse_beats if self.score.pulse_beats > 0 else 1.0
        sub = max(2, self.score.subdivision)
        beats = [b for s in self.score.sections for b in s.onset_beats]
        if not beats:
            return 0.0, 0
        tol = 0.5 / sub * 0.25        # quart d'un intervalle de subdivision
        on_pulse: set[int] = set()
        weak: list[int] = []
        for b in beats:
            pos = b / pulse
            phase = pos % 1.0
            if min(abs(phase - k / sub) for k in range(sub + 1)) > tol:
                continue              # hors grille : jeu imprécis, pas syncope
            if min(phase, 1.0 - phase) <= tol:
                on_pulse.add(int(round(pos)))
            else:
                weak.append(math.floor(pos))
        syncopated = sum(1 for idx in weak if idx not in on_pulse)
        return syncopated / len(beats), len(beats)

    def _section_dyn_levels(self) -> list[float | None]:
        """Niveau dynamique par section, avec report de la dernière nuance
        (la v1 retombait sur mf par défaut entre deux indications)."""
        marks = sorted((bar, DYN_LEVELS.get(d, 5)) for bar, d in self.score.dynamics)
        levels: list[float | None] = []
        for s in self.score.sections:
            current = None
            for bar, lv in marks:
                if bar <= s.start_bar:
                    current = lv
                else:
                    break
            inside = [lv for bar, lv in marks if s.start_bar < bar < s.end_bar]
            if inside:
                pool = inside if current is None else [current] + inside
                current = sum(pool) / len(pool)
            levels.append(current)
        return levels

    def _energies(self) -> list[float]:
        """Profil d'énergie par section, mesuré (tempo + vélocité/nuances +
        densité), normalisé sur la pièce."""
        secs = self.score.sections
        if not secs:
            return []
        tempos = [s.tempo for s in secs]
        tmin, tmax = min(tempos), max(tempos)
        max_vel = max((s.mean_velocity for s in secs), default=0.0)
        max_den = max((s.note_density for s in secs), default=0.0)
        dyn_levels = self._section_dyn_levels()
        energies = []
        for s, dyn in zip(secs, dyn_levels):
            comps: list[tuple[float, float]] = []
            comps.append((0.3, (s.tempo - tmin) / (tmax - tmin) if tmax > tmin else 0.5))
            if max_vel > 0:
                comps.append((0.4, s.mean_velocity / max_vel))
            elif dyn is not None:
                comps.append((0.4, dyn / 8.0))
            if max_den > 0:
                comps.append((0.3, s.note_density / max_den))
            wsum = sum(w for w, _ in comps)
            energies.append(sum(w * v for w, v in comps) / wsum)
        return energies

    # ── GROUPE A : FORME & ARCHITECTURE (axes 1-7) ──

    def _axe_01_section_count(self) -> StructuralAxis:
        """Nombre de sections. v1 : normalisation linéaire → 10 sections
        notées 1.0 alors que la docstring annonçait « idéal 3-7 »."""
        n = len(self.score.sections)
        return self._make(1, band(n, 1, 3, 7, 12), {"nb_sections": n, "optimal": "3-7"})

    def _axe_02_section_balance(self) -> StructuralAxis:
        """Équilibre des durées : pénalise la DÉGÉNÉRESCENCE, pas la variété.

        v2 notait `1 − CV`, un gradient dont l'optimum est l'uniformité
        parfaite : [4, 6, 9, 6] battait [4, 6, 15], alors que deux idées
        courtes puis un long développement est une forme, pas un défaut.
        Sur les paires dynamiques du corpus d'interprétations réelles,
        l'axe votait pour la dégradation (AUC 0.39-0.48) : aplatir ou
        brouiller les vélocités abaisse le plancher du seuil adaptatif de
        nouveauté, ce qui scinde les sections longues — et l'uniformité
        ainsi créée était RÉCOMPENSÉE (session 5, `resultats_ecoute.md`).

        Les formes réelles vivent sur un plateau : couplet 16 / pont 8 /
        coda 4 donne CV ≈ 0.54, un AABA régulier CV = 0 — équivalents
        musicalement, équivalents ici. Seule la dégénérescence chute :
        [1, 1, 30] (CV 1.28) est une segmentation en miettes, pas une
        architecture. Le plateau rend aussi l'axe robuste au tremblement
        des frontières : un déplacement d'une mesure ne change plus rien.

        Une seule section : rien à équilibrer — valeur neutre 0.5, et la
        confiance (AXIS_DATA_NEEDS : minimum 2 sections) dit déjà que
        l'axe n'a pas de matière. L'ancien 0.0 faisait perdre le fichier
        quoi qu'il arrive, ce qui ne mesurait rien."""
        durations = [s.n_bars for s in self.score.sections]
        if len(durations) < 2:
            return self._make(2, 0.5, {"nb_sections": len(durations)})
        mean_dur = sum(durations) / len(durations)
        if mean_dur <= 0:
            return self._make(2, 0.5, {"durees": durations})
        cv = _std(durations) / mean_dur
        return self._make(2, band(cv, -1.0, 0.0, 0.65, 1.5),
                          {"cv_durees": round(cv, 3), "durees": durations})

    def _axe_03_section_label_diversity(self) -> StructuralAxis:
        """Diversité = nombre de **matériaux distincts**, pas leur proportion.

        v2 notait le ratio `distincts / total`, qui dépend autant du
        dénominateur que de la diversité réelle : une pièce hachée en douze
        sections dont huit types voit son ratio BAISSER vers le plateau
        optimal, alors qu'elle est moins bien formée qu'un AABA. La
        sur-segmentation était donc récompensée (AUC 0.43).

        Une pièce tient sur 2 à 5 matériaux — couplet, refrain, pont, et
        parfois intro et coda. En deçà elle est monotone ; au-delà elle n'a
        plus de forme reconnaissable, seulement une suite d'épisodes."""
        labels = [s.label for s in self.score.sections]
        if not labels:
            return self._make(3, 0.0)
        n_types = len(set(labels))
        return self._make(3, band(n_types, 1, 2, 5, 9),
                          {"labels": labels, "nb_types": n_types})

    def _axe_04_symmetry(self) -> StructuralAxis:
        """Symétrie de l'architecture : palindrome des matériaux, retour du
        matériau initial, et équilibre des durées entre les deux moitiés.

        v2 ne mesurait que le palindrome exact des étiquettes. Un
        couplet-refrain — intro, couplet, refrain, couplet, refrain, pont,
        refrain, coda — n'a aucune paire symétrique et obtenait donc 0, alors
        que c'est la forme la plus répandue de la musique populaire. Un axe
        nul sur la majorité des morceaux ne discrimine rien et flotte au
        niveau du bruit. L'équilibre des durées ajoute une composante
        d'architecture qui ne dépend pas d'une égalité exacte d'étiquettes,
        donc robuste à la finesse du regroupement par matériau.

        v3 corrige deux défauts mesurés (AUC 0.39-0.49 selon la
        dégradation : l'axe votait pour le dégradé).

        **Sans contraste, pas de symétrie.** Une pièce d'un seul matériau
        est un palindrome parfait — `AAAAA` se lit pareil dans les deux
        sens — et obtenait la note maximale sur les deux composantes de
        forme. Le cas d'école du corpus : `chorus chorus bridge outro`
        (une vraie architecture) scorait 0.293, sa version aplatie
        `chorus chorus chorus chorus chorus` scorait 0.947. Une pièce sans
        contraste n'est pas symétrique, elle est informe : en dessous de
        deux matériaux distincts, les deux composantes qui lisent les
        étiquettes (palindrome, forme fermée) valent 0. C'est la même
        maladie que les huit axes redressés à l'audit — une propriété
        formelle dégénérée prise pour une qualité. L'équilibre des durées,
        lui, est conservé : il ne dépend d'aucune étiquette, et l'annuler
        punirait un morceau pour une erreur de regroupement en amont — le
        labelling ne retrouve la forme annotée que dans un cas sur deux.

        **L'équilibre des moitiés est un plateau, pas un gradient.** Noter
        `1 − déséquilibre` fait de l'égalité parfaite l'unique optimum, si
        bien qu'une dégradation qui uniformise les durées était
        récompensée (le défaut réparé sur l'axe 02, même racine). Une
        forme tolère qu'une moitié pèse un tiers de plus que l'autre ;
        elle ne tolère pas qu'une moitié avale tout."""
        secs = self.score.sections
        labels = [s.label for s in secs]
        if len(labels) < 3:
            return self._make(4, 0.0, {"labels": labels})
        n_types = len(set(labels))
        pairs = 0
        if n_types < 2:
            # Aucun contraste : `AAAAA` se lit pareil dans les deux sens,
            # mais ce n'est pas une symétrie — c'est l'absence de forme.
            palindrome = arch = 0.0
        else:
            half = len(labels) // 2
            pairs = sum(1 for i in range(half) if labels[i] == labels[-(i + 1)])
            raw = pairs / half if half else 0.0
            # Palindrome AU-DESSUS DU HASARD. Une séquence sur-segmentée aux
            # étiquettes répétitives aligne des paires symétriques par
            # accident — plus de sections, plus de chances — et l'axe
            # récompensait ce hasard chez les versions dégradées. On
            # soustrait donc la symétrie qu'offre gratuitement la
            # distribution des étiquettes : P(deux positions au hasard
            # portent la même) = Σ n_c(n_c−1) / (n(n−1)). ABBA dépasse son
            # hasard (1.0 contre 1/3) ; un AABA ne fait que l'atteindre —
            # sa vraie qualité, la forme fermée, est portée par `arch`.
            n = len(labels)
            counts = Counter(labels)
            expected = (sum(c * (c - 1) for c in counts.values())
                        / (n * (n - 1))) if n > 1 else 1.0
            palindrome = (clamp01((raw - expected) / (1.0 - expected))
                          if expected < 1.0 else 0.0)
            arch = 1.0 if labels[0] == labels[-1] else 0.0

        durations = [s.n_bars for s in secs]
        total = sum(durations)
        if total > 0:
            cut = len(durations) // 2
            first = sum(durations[:cut])
            second = sum(durations[len(durations) - cut:])
            skew = abs(first - second) / max(first + second, 1)
            balance = band(skew, -1.0, 0.0, 0.30, 0.75)
        else:
            balance = 0.0
        score = 0.5 * palindrome + 0.2 * arch + 0.3 * balance
        return self._make(4, score,
                          {"labels": labels, "nb_types": n_types,
                           "paires_symetriques": pairs,
                           "equilibre_moities": round(balance, 3)})

    def _axe_05_section_progression(self) -> StructuralAxis:
        """Progression énergétique. v1 : énergie = dictionnaire figé sur les
        labels, et « corrélation » = pente non bornée (scores > 1 possibles).
        v2 : énergie mesurée (fallback labels), corrélation de Pearson."""
        secs = self.score.sections
        if len(secs) < 2:
            return self._make(5, 0.0)
        energies = self._energies()
        source = "mesuree"
        if _std(energies) < 1e-3:
            energies = [float(LABEL_ENERGY.get(s.label, 3)) for s in secs]
            source = "labels"
        r = pearson(list(range(len(energies))), energies)
        return self._make(5, 0.5 + 0.5 * r,
                          {"correlation": round(r, 3), "source": source,
                           "energies": [round(e, 2) for e in energies]})

    def _axe_06_repeat_ratio(self) -> StructuralAxis:
        labels = [s.label for s in self.score.sections]
        if not labels:
            return self._make(6, 0.0)
        counts = Counter(labels)
        repeated = sum(c - 1 for c in counts.values() if c > 1)
        ratio = repeated / len(labels)
        # Plateau élargi à [0.15, 0.55]. Les formes canoniques donnent : AABA
        # et AABB 0.50, couplet-refrain court 0.50, rondo 0.40, ABA 0.33,
        # couplet-refrain long 0.375. La bande v0.2 s'arrêtait à 0.45 et
        # pénalisait donc trois des formes les plus répandues — un AABA
        # tombait dans la pente descendante.
        return self._make(6, band(ratio, 0.02, 0.15, 0.55, 0.85),
                          {"ratio": round(ratio, 3), "comptes": dict(counts)})

    def _axe_07_section_transition_quality(self) -> StructuralAxis:
        """Qualité des enchaînements : continuité harmonique et de tempo,
        avec un **relief** énergétique entre sections.

        v2 notait le pas d'énergie `1 − |Δ|`, donc récompensait l'absence de
        contraste : aplatir toutes les vélocités d'un morceau lui faisait
        gagner des points (AUC 0.23 face à flatten_dynamics — l'axe le plus
        inversé de la matrice). Or une transition réussie n'est pas une
        transition qu'on n'entend pas : un refrain doit se lever après un
        couplet. Ce qu'il faut écarter, c'est le saut *brutal* — d'où une
        bande, avec un optimum sur un écart franc mais tenu."""
        secs = self.score.sections
        if len(secs) < 2:
            return self._make(7, 0.0)
        energies = self._energies()
        scores = []
        for i in range(len(secs) - 1):
            s1, s2 = secs[i], secs[i + 1]
            tempo_cont = 1.0 - clamp01(abs(s1.tempo - s2.tempo) / max(s1.tempo, 1))
            pcs1 = {c.root_pc for c in s1.harmony}
            pcs2 = {c.root_pc for c in s2.harmony}
            if pcs1 and pcs2:
                jaccard = len(pcs1 & pcs2) / len(pcs1 | pcs2)
            else:
                jaccard = 0.5
            energy_step = band(abs(energies[i + 1] - energies[i]), -0.02, 0.08, 0.45, 0.9)
            scores.append(0.35 * tempo_cont + 0.40 * jaccard + 0.25 * energy_step)
        avg = sum(scores) / len(scores)
        return self._make(7, avg, {"nb_transitions": len(scores), "moyenne": round(avg, 3)})

    # ── GROUPE B : HARMONIE & TONALITÉ (axes 8-14) ──

    def _axe_08_key_stability(self) -> StructuralAxis:
        """Stabilité tonale via Krumhansl-Kessler. v1 : simple ratio de la
        fondamentale la plus fréquente (une pièce en I-IV-V bien tonale
        pouvait scorer bas)."""
        hist = self._pc_hist(self.score.sections)
        if sum(hist) <= 0:
            return self._make(8, 0.0)
        root, mode, corr, margin = estimate_key(hist)
        with_content = [s for s in self.score.sections if s.harmony or s.melody_pitches]
        if with_content:
            agree = sum(1 for s in with_content
                        if estimate_key(self._pc_hist([s]))[0] == root)
            agreement = agree / len(with_content)
        else:
            agreement = 0.0
        score = 0.6 * clamp01(corr) + 0.4 * agreement
        return self._make(8, score, {"tonique": PC_NAMES[root], "mode": mode,
                                     "correlation_kk": round(corr, 3),
                                     "marge": round(margin, 3),
                                     "accord_sections": round(agreement, 3)})

    def _axe_09_chord_progression_complexity(self) -> StructuralAxis:
        """Complexité **fonctionnelle** : richesse des degrés employés à
        l'intérieur d'une tonalité tenue.

        v2 : « variété des qualités + chromatisme », deux quantités que
        n'importe quel brouillage maximise. Une suite d'accords tirés au sort
        présente toutes les qualités et un chromatisme saturé — elle scorait
        donc au-dessus d'un vrai I-vi-ii-V (AUC 0.42, l'axe votait pour le
        chaos). v3 : la complexité se mesure en **degrés distincts de la
        tonalité**, et le chromatisme n'est un enrichissement que tant que la
        pièce reste diatonique dans l'ensemble — sinon c'est du bruit."""
        chords = self._chord_chain()
        if not chords:
            return self._make(9, 0.0)
        hist = self._pc_hist(self.score.sections)
        root, mode, _corr, _m = estimate_key(hist)
        scale = MAJOR_SCALE if PARENT_MODE[mode] == "maj" else MINOR_SCALE
        scale_pcs = {(root + d) % 12 for d in scale}

        degrees = Counter((c.root_pc - root) % 12 for c in chords
                          if c.root_pc in scale_pcs)
        # 3 à 7 degrés distincts : la zone de toutes les musiques tonales,
        # du blues à trois accords au standard de jazz.
        degree_variety = band(len(degrees), 0, 3, 7, 9)
        diatonic_ratio = sum(degrees.values()) / len(chords)
        # Ancrage : au-dessous de ~60 % d'accords dans la tonalité, il n'y a
        # plus de tonalité à enrichir.
        anchor = band(diatonic_ratio, 0.25, 0.65, 1.01, 1.01)
        qualities = Counter(c.quality for c in chords)
        quality_variety = band(len(qualities), 1, 2, 5, 9)
        score = 0.40 * degree_variety + 0.40 * anchor + 0.20 * quality_variety
        return self._make(9, score,
                          {"degres_distincts": len(degrees),
                           "ratio_diatonique": round(diatonic_ratio, 3),
                           "types": dict(qualities)})

    def _axe_10_cadence_presence(self) -> StructuralAxis:
        """Cadences par mouvement de fondamentales en classes de hauteur.
        v1 : différence MIDI brute == 7, donc V→I n'était détecté que si la
        dominante était voicée une quinte AU-DESSUS (G4→C4 oui, G3→C4 non) —
        et avec le mapping diatonique faux, même G4→C4 ne valait pas 7."""
        secs = self.score.sections
        chain: list[Chord] = []
        section_ends: set[int] = set()
        for s in secs:
            chain.extend(s.harmony)
            if s.harmony:
                section_ends.add(len(chain) - 1)
        if len(chain) < 2:
            return self._make(10, 0.0)
        counts = {"authentique": 0, "plagale": 0, "rompue": 0}
        weighted = 0.0
        for i in range(len(chain) - 1):
            iv = (chain[i + 1].root_pc - chain[i].root_pc) % 12
            kind = None
            if iv == 5:
                kind = "authentique"  # V → I (quarte asc. == quinte desc.)
            elif iv == 7:
                kind = "plagale"      # IV → I
            elif iv == 2 and chain[i + 1].quality.startswith("min"):
                kind = "rompue"       # V → vi
            if kind:
                counts[kind] += 1
                # Une cadence en fin de section « compte double » : c'est là
                # qu'elle structure la forme.
                weighted += 2.0 if (i in section_ends or i + 1 in section_ends) else 1.0
        score = weighted / (1.5 * max(1, len(secs)))
        return self._make(10, score, {"cadences": counts, "poids": round(weighted, 1)})

    def _axe_11_modulation_count(self) -> StructuralAxis:
        """v1 : score linéaire croissant jusqu'à 6 modulations (plus = mieux).
        v2 : 1-3 modulations = optimal, 0 = correct, > 6 = instable.

        v3 : compter ne suffit pas — transposer des segments au hasard
        fabrique exactement le nombre de « modulations » que l'axe
        récompensait (AUC 0.41). Une modulation est un geste de forme : elle
        va vers une tonalité **voisine** sur le cycle des quintes, et la
        pièce **revient** chez elle. Un errement qui ne revient jamais et
        saute à un triton n'est pas une modulation, c'est une rupture."""
        keys = []
        for s in self.score.sections:
            if s.harmony or s.melody_pitches:
                root, mode, _c, _m = estimate_key(self._pc_hist([s]))
                keys.append((root, mode))
        if len(keys) < 2:
            return self._make(11, 0.0, {"nb_modulations": 0})
        steps = [(keys[i][0], keys[i + 1][0]) for i in range(len(keys) - 1)
                 if keys[i][0] != keys[i + 1][0]]
        modulations = len(steps)
        count_score = band(modulations, -1, 0.5, 3, 7)
        if steps:
            # 1 pour une tonalité voisine (±1 quinte), 0 pour le triton.
            closeness = sum(1.0 - fifths_distance(a, b) / 6.0 for a, b in steps) / len(steps)
        else:
            closeness = 1.0
        home = estimate_key(self._pc_hist(self.score.sections))[0]
        returns_home = 1.0 if keys[-1][0] == home else 0.0
        anchor = sum(1 for r, _m in keys if r == home) / len(keys)
        quality = 0.45 * closeness + 0.35 * anchor + 0.20 * returns_home
        return self._make(11, 0.45 * count_score + 0.55 * quality,
                          {"nb_modulations": modulations,
                           "proximite_quintes": round(closeness, 3),
                           "ancrage_tonique": round(anchor, 3),
                           "retour_tonique": bool(returns_home),
                           "centres": [f"{PC_NAMES[r]}{'' if m == 'maj' else 'm'}" for r, m in keys]})

    def _axe_12_harmonic_rhythm(self) -> StructuralAxis:
        """Rythme harmonique = **régularité** de la pulsation des accords,
        pas taux de changement.

        v2 mesurait le seul ratio de changements, avec une bande qui retombait
        à 0 au-delà de 95 %. Or « un accord par mesure, différent à chaque
        mesure » — la grille la plus banale de la pop — donne un ratio de 1.0
        et se voyait donc noter 0, tandis qu'un brouillage laissant par hasard
        deux accords consécutifs identiques retombait dans la bande et scorait
        1.0. D'où l'inversion complète (AUC 0.25, le pire des 29 axes).

        v3 : ce qui distingue un vrai rythme harmonique, c'est que les
        changements tombent à intervalles **réguliers** (toutes les mesures,
        toutes les 2, toutes les 4). Le hasard, lui, produit des intervalles
        dispersés. On mesure donc la concentration de la distribution des
        écarts entre changements, et le taux ne sert plus qu'à écarter les
        deux extrêmes (accord unique tenu, ou changement permanent)."""
        chords = self._chord_chain()
        if len(chords) < 4:
            return self._make(12, 0.0, {"nb_accords": len(chords)})
        changes = [i for i in range(1, len(chords))
                   if chords[i].root_pc != chords[i - 1].root_pc]
        ratio = len(changes) / (len(chords) - 1)
        if not changes:
            return self._make(12, 0.0, {"ratio_changement": 0.0,
                                        "note": "accord unique tenu"})
        gaps = [changes[0]] + [changes[i] - changes[i - 1] for i in range(1, len(changes))]
        # Concentration = 1 − entropie normalisée : 1.0 si tous les écarts
        # sont identiques (parfaitement périodique), 0 s'ils sont uniformes.
        regularity = 1.0 - _entropy_norm(Counter(gaps), 8)
        rate = band(ratio, 0.02, 0.12, 1.0, 1.01)
        return self._make(12, 0.45 * rate + 0.55 * regularity,
                          {"ratio_changement": round(ratio, 3),
                           "regularite": round(regularity, 3),
                           "ecart_dominant": Counter(gaps).most_common(1)[0][0]})

    def _axe_13_tonal_contrast(self) -> StructuralAxis:
        """Contraste tonal = **excursion depuis un centre**, pas errance.

        v2 prenait la distance moyenne entre sections consécutives sur le
        cycle des quintes, avec un optimum autour de 0.45. Des sections
        transposées au hasard ont une distance moyenne d'environ 0.5 : le
        brouillage tombait en plein dans la zone idéale et scorait 1.0, quand
        une pièce cohérente restée dans sa tonalité scorait 0 (AUC 0.36).

        v3 sépare les deux choses qu'il faut pour parler de contraste : un
        **ancrage** (la majorité des sections partagent la tonalité de la
        pièce) et une **excursion** (au moins une section s'en écarte
        franchement). Une pièce dont chaque section est ailleurs n'a pas du
        contraste : elle n'a plus de centre par rapport auquel contraster."""
        secs = [s for s in self.score.sections if s.harmony or s.melody_pitches]
        if len(secs) < 2:
            return self._make(13, 0.0)
        roots = [estimate_key(self._pc_hist([s]))[0] for s in secs]
        home = estimate_key(self._pc_hist(self.score.sections))[0]
        dists = [fifths_distance(r, home) / 6.0 for r in roots]
        anchor_ratio = sum(1 for d in dists if d <= 1e-9) / len(dists)
        excursion = max(dists)
        anchor = band(anchor_ratio, 0.15, 0.5, 1.01, 1.01)
        reach = band(excursion, -0.05, 0.15, 0.55, 0.9)
        # Multiplicatif, pas additif : une somme laissait une pièce sans
        # centre tonal encaisser tout le terme d'excursion, si bien que des
        # sections transposées au hasard — qui s'éloignent beaucoup, faute de
        # tonalité — battaient une pièce cohérente. Sans ancrage, il n'y a
        # rien par rapport à quoi contraster : le produit doit s'effondrer.
        return self._make(13, anchor * (0.55 + 0.45 * reach),
                          {"ancrage": round(anchor_ratio, 3),
                           "excursion_max": round(excursion, 3),
                           "tonique_globale": PC_NAMES[home]})

    def _axe_14_bass_line_melodic(self) -> StructuralAxis:
        """v3 : le mouvement seul ne suffisait pas. Des fondamentales tirées
        au sort bougent tout le temps (ratio de mouvement = 1.0) et tombent
        par chance sur une seconde ou une quarte dans ~27 % des cas — assez
        pour battre une vraie basse qui répète parfois sa fondamentale
        (AUC 0.35). On exige en plus que les fondamentales **appartiennent à
        la tonalité** : c'est ce qui sépare une ligne de basse d'une suite de
        notes."""
        chords = self._chord_chain()
        roots = [c.root_pc for c in chords]
        if len(roots) < 3:
            return self._make(14, 0.0)
        moves = [min((roots[i + 1] - roots[i]) % 12, (roots[i] - roots[i + 1]) % 12)
                 for i in range(len(roots) - 1)]
        nonzero = [m for m in moves if m > 0]
        move_ratio = len(nonzero) / len(moves)
        good = sum(1 for m in nonzero if m in (1, 2, 5)) / len(nonzero) if nonzero else 0.0
        key_root, mode, _c, _m = estimate_key(self._pc_hist(self.score.sections))
        scale = MAJOR_SCALE if PARENT_MODE[mode] == "maj" else MINOR_SCALE
        scale_pcs = {(key_root + d) % 12 for d in scale}
        in_key = sum(1 for r in roots if r in scale_pcs) / len(roots)
        # Le plateau monte jusqu'à 1.01 : une basse qui change de note à
        # chaque accord est la norme, pas un excès. La bande v2 s'arrêtait à
        # 0.9, si bien qu'un mouvement constant (ratio 1.0) tombait dans la
        # pente descendante et scorait 0.09 — un brouillage qui laissait par
        # hasard deux fondamentales consécutives égales repassait, lui, sur
        # le plateau. D'où l'inversion (AUC 0.23 face à shuffle_bars).
        score = (0.35 * band(move_ratio, 0.15, 0.45, 1.01, 1.01)
                 + 0.30 * good
                 + 0.35 * band(in_key, 0.3, 0.75, 1.01, 1.01))
        return self._make(14, score, {"ratio_mouvement": round(move_ratio, 3),
                                      "ratio_degres_quintes": round(good, 3),
                                      "ratio_dans_tonalite": round(in_key, 3)})

    # ── GROUPE C : MÉLODIE & THÈME (axes 15-20) ──

    def _axe_15_melodic_contour(self) -> StructuralAxis:
        iv = self._melody_intervals()
        if len(iv) < 3:
            return self._make(15, 0.0, {"nb_notes": len(self._melody())})
        signs = [i for i in iv if i != 0]
        flips = sum(1 for a, b in zip(signs, signs[1:]) if (a > 0) != (b > 0))
        wave = band(flips / max(1, len(signs) - 1), 0.1, 0.25, 0.6, 0.9)
        mean_abs = sum(abs(i) for i in iv) / len(iv)
        smooth = band(mean_abs, 0.5, 1.5, 3.5, 7.0)
        return self._make(15, 0.5 * smooth + 0.5 * wave,
                          {"intervalle_moyen_st": round(mean_abs, 2),
                           "inversions_direction": flips})

    def _axe_16_motivic_development(self) -> StructuralAxis:
        """Motifs = trigrammes d'INTERVALLES (invariants par transposition).
        v1 : n-grammes de hauteurs absolues — un motif transposé (procédé de
        développement classique) comptait comme du matériau neuf."""
        iv = self._melody_intervals()
        if len(iv) < 6:
            return self._make(16, 0.0, {"nb_intervalles": len(iv)})
        grams = [tuple(iv[i:i + 3]) for i in range(len(iv) - 2)]
        repeat_ratio = 1.0 - len(set(grams)) / len(grams)
        return self._make(16, band(repeat_ratio, 0.05, 0.25, 0.55, 0.9),
                          {"ratio_repetition": round(repeat_ratio, 3),
                           "nb_motifs_uniques": len(set(grams))})

    def _axe_17_theme_recognition(self) -> StructuralAxis:
        """v1 : crash garanti (`self.score_sections`, typo). v2 : un thème
        est reconnaissable si un motif long (6 notes) revient plusieurs fois,
        à la transposition près."""
        iv = self._melody_intervals()
        if len(iv) < 8:
            return self._make(17, 0.0, {"nb_intervalles": len(iv)})
        grams = [tuple(iv[i:i + 5]) for i in range(len(iv) - 4)]
        top_count = Counter(grams).most_common(1)[0][1]
        score = band(top_count, 1, 3, 9, 18)
        if len(grams) < 8:
            score *= len(grams) / 8
        return self._make(17, score, {"occurrences_motif_principal": top_count})

    def _axe_18_melodic_range(self) -> StructuralAxis:
        m = self._melody()
        if not m:
            return self._make(18, 0.0)
        rng = max(p.midi for p in m) - min(p.midi for p in m)
        return self._make(18, band(rng, 5, 12, 24, 36), {"etendue_st": rng})

    def _axe_19_intervallic_character(self) -> StructuralAxis:
        iv = self._melody_intervals()
        if len(iv) < 2:
            return self._make(19, 0.0)
        classes = Counter(min(abs(i), 12) for i in iv)
        diversity = _entropy_norm(classes, 8)
        leap_ratio = sum(1 for i in iv if abs(i) > 2) / len(iv)
        balance = band(leap_ratio, 0.03, 0.12, 0.35, 0.7)
        return self._make(19, 0.5 * diversity + 0.5 * balance,
                          {"types_intervalles": len(classes),
                           "ratio_sauts": round(leap_ratio, 3)})

    def _axe_20_melodic_rhythm_syncopation(self) -> StructuralAxis:
        """v2 : vraies syncopes depuis les positions d'attaque (fraction de
        temps). v1 : proxy sur les changements de nuances, sans rapport.

        v3 : deux corrections. La position est prise **dans la pulsation
        réelle** — en 6/8 la pulsation vaut une noire pointée, alors que la
        v2 découpait en noires partout. Et surtout la syncope est enfin
        définie comme telle (voir `_syncopation`) : la v2 comptait toute
        attaque hors du temps, donc classait « syncopée » une mélodie en
        croches parfaitement carrée."""
        ratio, n_onsets = self._syncopation()
        if n_onsets:
            # La syncope est un BONUS, jamais un prérequis. La v2 la notait
            # par une bande partant de 0.01, si bien qu'un morceau sans
            # syncope — un choral, une valse, une ballade — scorait 0 et se
            # faisait battre par n'importe quel décalage aléatoire. Ce que
            # l'axe doit d'abord constater, c'est que le rythme mélodique est
            # **lisible** : articulé sur la grille métrique. La syncope
            # s'ajoute par-dessus.
            grid = self._grid_alignment()
            bonus = band(ratio, -0.02, 0.06, 0.35, 0.7)
            return self._make(20, 0.65 * grid + 0.35 * bonus,
                              {"ratio_syncopes": round(ratio, 3),
                               "alignement_grille": round(grid, 3),
                               "nb_onsets": n_onsets,
                               "subdivision": self.score.subdivision})
        onsets = [b for s in self.score.sections for b in s.onset_beats]
        if onsets:
            off = sum(1 for b in onsets if 0.2 < (b % 1.0) < 0.8)
            ratio = off / len(onsets)
            return self._make(20, band(ratio, 0.02, 0.15, 0.45, 0.8),
                              {"ratio_contretemps": round(ratio, 3), "nb_onsets": len(onsets)})
        dyn = self.score.dynamics
        if len(dyn) < 2:
            return self._make(20, 0.0, {"proxy": "aucune donnée"})
        changes = sum(1 for i in range(len(dyn) - 1) if dyn[i][1] != dyn[i + 1][1])
        return self._make(20, min(0.5, changes / len(dyn)),
                          {"proxy": "variations dynamiques", "ratio": round(changes / len(dyn), 3)})

    # ── GROUPE D : RYTHME & TEMPO (axes 21-24) ──

    def _tempos(self) -> list[float]:
        if len(self.score.tempo_map) >= 2:
            return [float(t) for _, t in self.score.tempo_map]
        # v1 ignorait les tempos de sections quand tempo_map était vide.
        return [float(s.tempo) for s in self.score.sections]

    def _axe_21_tempo_variation(self) -> StructuralAxis:
        tempos = self._tempos()
        if len(tempos) < 2:
            return self._make(21, 0.0)
        changes = sum(1 for i in range(len(tempos) - 1)
                      if abs(tempos[i + 1] - tempos[i]) / max(tempos[i], 1) > 0.04)
        return self._make(21, band(changes, -2, 1, 4, 10), {"nb_variations": changes})

    def _axe_22_tempo_consistency(self) -> StructuralAxis:
        tempos = self._tempos()
        if not tempos:
            return self._make(22, 0.5, {"note": "aucune donnée tempo"})
        mean_t = sum(tempos) / len(tempos)
        cv = _std(tempos) / mean_t if mean_t else 0.0
        return self._make(22, 1.0 - 2.0 * cv, {"cv_tempo": round(cv, 3)})

    def _axe_23_rhythmic_complexity(self) -> StructuralAxis:
        """Complexité rythmique = richesse des durées **sur une grille tenue**.

        v2 additionnait entropie des IOI et taux de contretemps. Décaler
        chaque attaque au hasard augmente les deux : le brouillage rythmique
        scorait au-dessus de l'original (AUC 0.39). Or décaler n'est pas
        complexifier — un rythme complexe reste mesurable, c'est même ce qui
        le rend jouable. On multiplie donc la richesse par l'**alignement
        métrique** : la fraction d'attaques qui tombent sur une subdivision
        de la pulsation. Le jitter fait s'effondrer ce facteur.

        Les IOI sont comptés en **pulsations**, pas en noires, et la grille
        suit la subdivision réelle du chiffrage : en 6/8 la pulsation est la
        noire pointée et la grille est ternaire, donc une croche au tiers de
        pulsation est sur la grille — la v2, qui raisonnait en noires
        partout, la comptait comme une syncope."""
        sc = self.score
        onsets = sorted(b for s in sc.sections for b in s.onset_beats)
        phases = [p for s in sc.sections for p in s.onset_phases]
        if len(onsets) >= 8:
            pulse = sc.pulse_beats if sc.pulse_beats > 0 else 1.0
            sub = max(2, sc.subdivision)
            # IOI en pulsations, quantifiés à la double-subdivision.
            step = 1.0 / (2 * sub)
            iois = [round(((b2 - b1) / pulse) / step) * step
                    for b1, b2 in zip(onsets, onsets[1:]) if b2 - b1 > 1e-6]
            classes = Counter(min(i, 4.0) for i in iois)
            # Bande et non croissance monotone : une entropie de durées
            # maximale n'est pas le comble de la complexité rythmique, c'est
            # l'absence de rythme. Permuter les mesures d'un morceau élève
            # cette entropie sans rien complexifier — la v2 y voyait un
            # progrès (AUC 0.33 face à shuffle_bars).
            ent = band(_entropy_norm(classes, 8), 0.02, 0.2, 0.72, 0.96)
            grid = self._grid_alignment()
            off, _n = self._syncopation()
            richness = 0.6 * ent + 0.4 * clamp01(off * 3)
            return self._make(23, richness * grid,
                              {"classes_ioi": len(classes),
                               "alignement_grille": round(grid, 3),
                               "ratio_syncopes": round(off, 3),
                               "subdivision": sub})
        textures = [t for _, t in sc.texture_map]
        if not textures:
            return self._make(23, 0.5, {"note": "aucune donnée rythmique"})
        poly = sum(1 for t in textures if "poly" in t.lower() or "contrapunt" in t.lower())
        return self._make(23, min(1.0, poly / len(textures) * 3),
                          {"proxy": "textures", "nb_polyrythmie": poly})

    def _axe_24_hypermetric_regularity(self) -> StructuralAxis:
        """Redéfini. v1 : copie conforme de l'axe 2 (même calcul, même CV) —
        la même feature pesait double. v2 : carrure hypermétrique = sections
        en multiples de 4 mesures (8, 16, 32 = carrures classiques)."""
        lengths = [s.n_bars for s in self.score.sections]
        if not lengths:
            return self._make(24, 0.0)
        mult4 = sum(1 for n in lengths if n > 0 and n % 4 == 0) / len(lengths)
        pow2 = sum(1 for n in lengths if n in (2, 4, 8, 16, 32)) / len(lengths)
        return self._make(24, 0.7 * mult4 + 0.3 * pow2,
                          {"longueurs": lengths, "ratio_carrure": round(mult4, 3)})

    # ── GROUPE E : TEXTURE & ORCHESTRATION (axes 25-27) ──

    def _axe_25_texture_variety(self) -> StructuralAxis:
        """Variété texturale = la texture change **entre** les sections, pas
        au hasard à l'intérieur.

        v2 prenait l'écart max−min de polyphonie entre sections. C'est une
        statistique globale, aveugle à l'organisation : brouiller les hauteurs
        ou décaler les attaques fait fluctuer la polyphonie et élargit l'écart
        sans rien orchestrer (AUC 0.41). v3 mesure un rapport de variances à
        la Fisher sur la polyphonie mesure par mesure — quelle part de la
        variation tombe aux frontières de sections plutôt qu'à l'intérieur.
        Un arrangement voulu concentre ses changements aux frontières ; le
        bruit les répand partout."""
        per_bar = [[p for p in s.poly_by_bar if p > 0] for s in self.score.sections]
        per_bar = [b for b in per_bar if b]
        polys = [s.avg_polyphony for s in self.score.sections if s.avg_polyphony > 0]
        if len(per_bar) >= 2 and sum(len(b) for b in per_bar) >= 4:
            flat = [x for b in per_bar for x in b]
            grand = sum(flat) / len(flat)
            between = sum(len(b) * (sum(b) / len(b) - grand) ** 2 for b in per_bar)
            within = sum((x - sum(b) / len(b)) ** 2 for b in per_bar for x in b)
            structured = between / (between + within) if (between + within) > 1e-9 else 0.0
            spread = max(polys) - min(polys) if len(polys) >= 2 else 0.0
            return self._make(25, 0.6 * structured + 0.4 * band(spread, 0.1, 0.8, 4.0, 8.0),
                              {"part_structuree": round(structured, 3),
                               "polyphonie_min": round(min(polys), 2) if polys else 0,
                               "polyphonie_max": round(max(polys), 2) if polys else 0})
        if len(polys) >= 2:
            spread = max(polys) - min(polys)
            return self._make(25, band(spread, 0.1, 0.8, 3.0, 6.0),
                              {"polyphonie_min": round(min(polys), 2),
                               "polyphonie_max": round(max(polys), 2)})
        textures = [t for _, t in self.score.texture_map]
        if not textures:
            return self._make(25, 0.0)
        diversity = len(set(textures)) / len(textures)
        return self._make(25, min(1.0, diversity * 2), {"types": dict(Counter(textures))})

    def _axe_26_dynamic_range(self) -> StructuralAxis:
        vels = [s.mean_velocity for s in self.score.sections if s.mean_velocity > 0]
        if len(vels) >= 2:
            rng = max(vels) - min(vels)
            return self._make(26, band(rng, 4, 15, 45, 80),
                              {"velocite_min": round(min(vels)), "velocite_max": round(max(vels))})
        values = [DYN_LEVELS.get(d, 4) for _, d in self.score.dynamics]
        if not values:
            return self._make(26, 0.0)
        return self._make(26, band(max(values) - min(values), 0, 2, 5, 8),
                          {"min": min(values), "max": max(values)})

    def _axe_27_voice_count(self) -> StructuralAxis:
        """Polyphonie : densité de voix **tenue**.

        v2 : band(0.8, 1.8, 4.0, 8.0) — au-delà de 4 voix le score
        redescendait, ce qui punit toute orchestration nourrie sans raison
        musicale (un quintette n'est pas moins bien écrit qu'un trio). Pire,
        la décroissance rendait l'axe inversible : décaler les attaques
        désynchronise les voix, fait chuter la polyphonie simultanée vers 4,
        et le brouillage y gagnait le score maximal (AUC 0.29).

        v3 : plateau large et sans pénalité haute, multiplié par la stabilité
        de l'effectif à l'intérieur des sections — c'est la désynchronisation
        que l'axe doit voir, pas le nombre de pupitres."""
        polys = [s.avg_polyphony for s in self.score.sections if s.avg_polyphony > 0]
        if polys:
            avg = sum(polys) / len(polys)
            richness = band(avg, 0.7, 2.2, 12.0, 24.0)
            per_bar = [[p for p in s.poly_by_bar if p > 0] for s in self.score.sections]
            per_bar = [b for b in per_bar if len(b) >= 2]
            if per_bar:
                cvs = []
                for b in per_bar:
                    m = sum(b) / len(b)
                    if m > 0:
                        cvs.append(_std(b) / m)
                stability = clamp01(1.0 - 1.5 * (sum(cvs) / len(cvs))) if cvs else 1.0
            else:
                stability = 1.0
            return self._make(27, 0.6 * richness + 0.4 * stability,
                              {"moyenne_voix": round(avg, 2),
                               "stabilite_effectif": round(stability, 3)})
        textures = [t for _, t in self.score.texture_map]
        if not textures:
            return self._make(27, 0.5, {"note": "aucune donnée"})
        counts = []
        for t in textures:
            tl = t.lower()
            if "4v" in tl or "quatuor" in tl:
                counts.append(4)
            elif "3v" in tl or "trio" in tl:
                counts.append(3)
            elif "2v" in tl or "duo" in tl:
                counts.append(2)
            elif "solo" in tl or "mono" in tl:
                counts.append(1)
            else:
                counts.append(2)
        avg = sum(counts) / len(counts)
        return self._make(27, min(1.0, avg / 4.0), {"moyenne_voix": round(avg, 2)})

    # ── GROUPE F : COHÉRENCE GLOBALE (axes 28-29) ──

    def _axe_28_emotional_arc(self) -> StructuralAxis:
        """v1 : pente × amplitude — un arc en arche (montée puis descente,
        la forme émotionnelle la plus courante) avait une pente ≈ 0 donc un
        score ≈ 0. v2 : ajustement au meilleur des deux gabarits (arche avec
        pic aux 2/3, ou montée continue type finale)."""
        energies = self._energies()
        n = len(energies)
        if n < 3:
            return self._make(28, 0.4 if n else 0.0, {"nb_sections": n})
        amplitude = max(energies) - min(energies)
        if amplitude < 1e-6:
            return self._make(28, 0.2, {"amplitude": 0.0, "note": "profil plat"})
        xs = [i / (n - 1) for i in range(n)]
        arch = [1.0 - abs(x - 0.65) / 0.65 for x in xs]
        rise = xs
        fit = max(pearson(arch, energies), pearson(rise, energies))
        peak_pos = energies.index(max(energies)) / (n - 1)
        score = 0.65 * max(0.0, fit) + 0.35 * clamp01(amplitude * 2.5)
        return self._make(28, score, {"ajustement": round(fit, 3),
                                      "amplitude": round(amplitude, 3),
                                      "position_pic": round(peak_pos, 2),
                                      "energies": [round(e, 2) for e in energies]})

    def _axe_29_global_cohesion(self, computed: list[StructuralAxis]) -> StructuralAxis:
        """v1 : recalculait les axes 7/8 avec d'autres formules + plancher
        arbitraire de 0.2. v2 : vraie synthèse — équilibre entre groupes
        (faible dispersion) et absence de maillon faible."""
        by_group: dict[str, list[float]] = {}
        for ax in computed:
            num = int(ax.id[:2])
            group = AXES_META[num][3]
            by_group.setdefault(group, []).append(ax.score)
        means = {g: sum(v) / len(v) for g, v in by_group.items()}
        vals = list(means.values())
        consistency = clamp01(1.0 - 2.0 * _std(vals))
        weakest = min(vals) if vals else 0.0
        score = 0.6 * consistency + 0.4 * weakest
        return self._make(29, score, {"groupes": {g: round(m, 3) for g, m in sorted(means.items())},
                                      "equilibre": round(consistency, 3),
                                      "maillon_faible": round(weakest, 3)})

    # ── API publique ──

    def _resources(self) -> dict[str, float]:
        """Matière réellement disponible dans la partition, par ressource."""
        secs = self.score.sections
        return {
            "sections": len(secs),
            "harmony": len(self.score.all_chords),
            "melody": len(self.score.all_melody),
            "onsets": sum(len(s.onset_beats) for s in secs),
            "tempo": len(self._tempos()),
            "polyphony": sum(1 for s in secs if s.avg_polyphony > 0),
            "velocity": sum(1 for s in secs if s.mean_velocity > 0),
        }

    def _apply_confidence(self, axes: list[StructuralAxis]) -> None:
        """Renseigne la fiabilité de chaque axe d'après la matière dont il
        disposait — et la dégrade encore quand l'axe a dû se rabattre sur un
        repli indirect (textures au lieu d'onsets, nuances au lieu de
        vélocités mesurées)."""
        res = self._resources()
        for ax in axes:
            num = int(ax.id[:2])
            need = AXIS_DATA_NEEDS.get(num)
            if need is None:
                continue
            resource, minimum, comfortable = need
            conf = data_confidence(res.get(resource, 0.0), minimum, comfortable)
            if "proxy" in ax.details:
                conf *= PROXY_CONFIDENCE
            ax.confidence = clamp01(conf)

    def calculate(self) -> list[StructuralAxis]:
        """Exécute les 29 axes et retourne la liste des résultats."""
        builders = [
            self._axe_01_section_count,
            self._axe_02_section_balance,
            self._axe_03_section_label_diversity,
            self._axe_04_symmetry,
            self._axe_05_section_progression,
            self._axe_06_repeat_ratio,
            self._axe_07_section_transition_quality,
            self._axe_08_key_stability,
            self._axe_09_chord_progression_complexity,
            self._axe_10_cadence_presence,
            self._axe_11_modulation_count,
            self._axe_12_harmonic_rhythm,
            self._axe_13_tonal_contrast,
            self._axe_14_bass_line_melodic,
            self._axe_15_melodic_contour,
            self._axe_16_motivic_development,
            self._axe_17_theme_recognition,
            self._axe_18_melodic_range,
            self._axe_19_intervallic_character,
            self._axe_20_melodic_rhythm_syncopation,
            self._axe_21_tempo_variation,
            self._axe_22_tempo_consistency,
            self._axe_23_rhythmic_complexity,
            self._axe_24_hypermetric_regularity,
            self._axe_25_texture_variety,
            self._axe_26_dynamic_range,
            self._axe_27_voice_count,
            self._axe_28_emotional_arc,
        ]
        axes = [build() for build in builders]
        self._apply_confidence(axes)
        cohesion = self._axe_29_global_cohesion(axes)
        # L'axe 29 synthétise les 28 autres : il ne peut pas être plus sûr
        # que ce qu'il résume.
        total_w = sum(a.weight for a in axes)
        cohesion.confidence = (sum(a.confidence * a.weight for a in axes) / total_w
                               if total_w > 0 else 0.0)
        axes.append(cohesion)
        self.axes = axes
        return self.axes

    def get_score(self) -> float:
        """Score SMS global (moyenne pondérée, poids somme = 1.0)."""
        if not self.axes:
            self.calculate()
        total_weight = sum(a.weight for a in self.axes)
        weighted = sum(a.score * a.weight for a in self.axes)
        return (weighted / total_weight) if total_weight > 0 else 0.0

    def confidence(self) -> float:
        """Fiabilité globale du score : moyenne des confiances par axe,
        pondérée comme le score lui-même.

        À lire avec `get_score()`, jamais à sa place. Un 0.62 à confiance
        0.15 et un 0.62 à confiance 0.95 sont deux affirmations très
        différentes, que la v0.2 présentait à l'identique."""
        if not self.axes:
            self.calculate()
        total_weight = sum(a.weight for a in self.axes)
        if total_weight <= 0:
            return 0.0
        return sum(a.confidence * a.weight for a in self.axes) / total_weight

    def confidence_level(self) -> str:
        """Verdict lisible, calé sur des mesures et non sur l'intuition.

        Accuracy contrastive réellement observée par tranche, sur 200
        fichiers (60 morceaux + 140 boucles) :

            élevée       ≥ 0.75  →  0.94
            moyenne      ≥ 0.55  →  0.94
            faible       ≥ 0.35  →  0.33   ← pire que le hasard
            insuffisante < 0.35  →  0.51   ← le hasard

        La zone « faible » est donc la plus traître, et non un juste milieu :
        le fichier offre assez de matière pour que les axes s'engagent, pas
        assez pour qu'ils aient raison. D'où le seuil d'alerte à 0.55 —
        au-dessous, le score ne se lit pas, quel que soit le libellé."""
        c = self.confidence()
        if c >= 0.75:
            return "élevée"
        if c >= 0.55:
            return "moyenne"
        if c >= 0.35:
            return "faible"
        return "insuffisante"

    # En dessous, l'accuracy contrastive mesurée ne dépasse pas le hasard :
    # le score global ne doit pas être présenté comme une évaluation.
    INTERPRETABLE_CONFIDENCE = 0.55

    def is_interpretable(self) -> bool:
        return self.confidence() >= self.INTERPRETABLE_CONFIDENCE

    RESOURCE_LABELS = {
        "sections": "sections", "harmony": "accords", "melody": "notes mélodiques",
        "onsets": "attaques", "tempo": "repères de tempo",
        "polyphony": "sections avec polyphonie mesurée",
        "velocity": "sections avec vélocité mesurée",
    }

    def missing_resources(self) -> list[tuple[str, float, float]]:
        """(ressource, disponible, requis) pour ce qui manque — la raison
        concrète d'une fiabilité basse. « 1 section au lieu de 4 » désigne un
        problème de segmentation ; « 0 note mélodique » désigne une boucle de
        percussion. Ce ne sont pas les mêmes conseils."""
        res = self._resources()
        needed: dict[str, float] = {}
        for _num, (resource, _mini, comfortable) in AXIS_DATA_NEEDS.items():
            needed[resource] = max(needed.get(resource, 0.0), comfortable)
        out = []
        for resource, comfortable in sorted(needed.items()):
            have = res.get(resource, 0.0)
            if have < comfortable:
                out.append((self.RESOURCE_LABELS.get(resource, resource),
                            have, comfortable))
        return out

    def unreliable_axes(self, threshold: float = 0.5) -> list[StructuralAxis]:
        """Axes dont le score n'est pas soutenu par assez de matière."""
        if not self.axes:
            self.calculate()
        return [a for a in self.axes if a.confidence < threshold]

    def group_scores(self) -> dict[str, float]:
        if not self.axes:
            self.calculate()
        by_group: dict[str, list[tuple[float, float]]] = {}
        for ax in self.axes:
            group = AXES_META[int(ax.id[:2])][3]
            by_group.setdefault(group, []).append((ax.score, ax.weight))
        return {g: sum(s * w for s, w in v) / sum(w for _, w in v)
                for g, v in sorted(by_group.items())}

    def summary(self) -> str:
        """Rapport textuel groupé."""
        if not self.axes:
            self.calculate()
        lines = ["=== LIBRETTO SMS REPORT ==="]
        current_group = None
        for ax in self.axes:
            group = AXES_META[int(ax.id[:2])][3]
            if group != current_group:
                current_group = group
                lines.append(f"\n── {GROUP_NAMES[group]} ──")
            filled = int(round(ax.score * 10))
            bar = "█" * filled + "░" * (10 - filled)
            # Un axe sans matière est marqué : son score est un défaut, pas
            # une mesure, et l'afficher nu revient à l'affirmer.
            flag = "" if ax.confidence >= 0.5 else f"  ⚠ fiabilité {ax.confidence:.2f}"
            lines.append(f"[{ax.id}] {ax.name}: {ax.score:.2f} |{bar}|{flag}")
        lines.append("\n── Scores par groupe ──")
        for g, s in self.group_scores().items():
            lines.append(f"  {g} · {GROUP_NAMES[g]}: {s:.2f}")
        conf = self.confidence()
        lines.append(f"\nSCORE GLOBAL SMS: {self.get_score():.2f}")
        lines.append(f"FIABILITÉ: {conf:.2f} ({self.confidence_level()})")
        weak = self.unreliable_axes()
        if weak:
            lines.append(f"  {len(weak)} axe(s) sans matière suffisante : "
                         + ", ".join(a.id for a in weak))
        if not self.is_interpretable():
            missing = self.missing_resources()
            if missing:
                lines.append("  matière manquante : " + ", ".join(
                    f"{label} {have:g}/{want:g}" for label, have, want in missing))
            lines.append("  ⚠ Le score global n'est PAS interprétable : au-dessous de "
                         f"{self.INTERPRETABLE_CONFIDENCE:g} de fiabilité, il ne fait "
                         "pas mieux que le hasard.")
            lines.append("    " + self.diagnosis())
        return "\n".join(lines)

    def diagnosis(self) -> str:
        """Pourquoi la fiabilité est basse, en une phrase actionnable.

        L'ordre compte : une boucle de percussion et un morceau que la
        segmentation a raté produisent tous deux « une seule section », mais
        appellent des réponses opposées. On teste donc d'abord ce qui
        distingue un fichier trop pauvre d'un fichier mal découpé."""
        res = self._resources()
        if res["harmony"] == 0 and res["melody"] < 8:
            return ("Aucune harmonie ni mélodie exploitable : ce fichier est une "
                    "boucle rythmique. Libretto analyse des morceaux — 23 des 29 "
                    "axes n'ont rien à mesurer ici.")
        if res["harmony"] < 4 or res["melody"] < 10:
            return ("Trop peu de matière harmonique ou mélodique : boucle courte ou "
                    "pièce mono-instrument, plutôt qu'un morceau construit.")
        if res["sections"] <= 1:
            return ("Une seule section détectée : soit la pièce est continue, soit "
                    "la segmentation a échoué. Des marqueurs MIDI nommant les "
                    "sections lèvent l'ambiguïté.")
        return ("Pièce trop courte pour renseigner les axes de forme : les "
                "grandeurs à l'échelle du morceau (symétrie, arc, répétition) "
                "n'ont pas de support.")

    def to_dict(self) -> dict:
        if not self.axes:
            self.calculate()
        return {
            "global_score": round(self.get_score(), 4),
            "confidence": round(self.confidence(), 4),
            "confidence_level": self.confidence_level(),
            "unreliable_axes": [a.id for a in self.unreliable_axes()],
            "groups": {g: round(s, 4) for g, s in self.group_scores().items()},
            "group_names": GROUP_NAMES,
            "axes": [
                {"id": a.id, "name": a.name, "weight": a.weight,
                 "group": AXES_META[int(a.id[:2])][3],
                 "score": round(a.score, 4),
                 "confidence": round(a.confidence, 4),
                 "details": a.details}
                for a in self.axes
            ],
        }
