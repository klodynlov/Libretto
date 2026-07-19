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

MAJOR_SCALE = {0, 2, 4, 5, 7, 9, 11}
MINOR_SCALE = {0, 2, 3, 5, 7, 8, 10, 11}  # mineur naturel + sensible


def estimate_key(hist: list[float]) -> tuple[int, str, float, float]:
    """(classe de hauteur de la tonique, 'maj'|'min', corrélation, marge sur
    la 2e tonique candidate). hist = poids par classe de hauteur (12)."""
    if sum(hist) <= 0:
        return 0, "maj", 0.0, 0.0
    results = []
    for mode, profile in (("maj", KK_MAJOR), ("min", KK_MINOR)):
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

    def __post_init__(self):
        # Clamp central : plus aucun axe ne peut sortir de [0, 1].
        self.score = clamp01(self.score)


# id, nom, poids, groupe. Somme des poids = 1.0 (vérifiée par les tests).
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
    """Calculateur des 29 axes structurels du SMS."""

    def __init__(self, score: Score):
        self.score = score
        self.axes: list[StructuralAxis] = []

    # ── fabrique d'axe (id/nom/poids centralisés) ──

    def _make(self, num: int, score: float, details: dict | None = None) -> StructuralAxis:
        axis_id, name, weight, _group = AXES_META[num]
        return StructuralAxis(axis_id, name, weight, score, details or {})

    # ── features partagées ──

    def _melody(self) -> list[Pitch]:
        return self.score.all_melody

    def _melody_intervals(self) -> list[int]:
        m = self._melody()
        return [m[i + 1].midi - m[i].midi for i in range(len(m) - 1)]

    def _chord_chain(self) -> list[Chord]:
        return self.score.all_chords

    def _pc_hist(self, sections: list[Section]) -> list[float]:
        h = [0.0] * 12
        for s in sections:
            for c in s.harmony:
                h[c.root_pc] += 2.0
                for pc in c.chord_pcs:
                    h[pc] += 0.5
            for p in s.melody_pitches:
                h[p.pc] += 1.0
        return h

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
        durations = [s.n_bars for s in self.score.sections]
        if len(durations) < 2:
            return self._make(2, 0.0, {"nb_sections": len(durations)})
        mean_dur = sum(durations) / len(durations)
        if mean_dur <= 0:
            return self._make(2, 0.0, {"durees": durations})
        cv = _std(durations) / mean_dur
        return self._make(2, 1.0 - cv, {"cv_durees": round(cv, 3), "durees": durations})

    def _axe_03_section_label_diversity(self) -> StructuralAxis:
        labels = [s.label for s in self.score.sections]
        if not labels:
            return self._make(3, 0.0)
        diversity = len(set(labels)) / len(labels)
        return self._make(3, band(diversity, 0.1, 0.3, 0.6, 1.0),
                          {"labels": labels, "diversite": round(diversity, 3)})

    def _axe_04_symmetry(self) -> StructuralAxis:
        labels = [s.label for s in self.score.sections]
        if len(labels) < 3:
            return self._make(4, 0.0, {"labels": labels})
        half = len(labels) // 2
        pairs = sum(1 for i in range(half) if labels[i] == labels[-(i + 1)])
        base = pairs / half
        arch_bonus = 0.15 if labels[0] == labels[-1] else 0.0
        return self._make(4, base + arch_bonus,
                          {"labels": labels, "paires_symetriques": pairs})

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
        return self._make(6, band(ratio, 0.05, 0.2, 0.45, 0.8),
                          {"ratio": round(ratio, 3), "comptes": dict(counts)})

    def _axe_07_section_transition_quality(self) -> StructuralAxis:
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
            energy_step = 1.0 - abs(energies[i + 1] - energies[i])
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
        chords = self._chord_chain()
        if not chords:
            return self._make(9, 0.0)
        qualities = Counter(c.quality for c in chords)
        variety = band(len(qualities), 1, 2.5, 6, 9)
        hist = self._pc_hist(self.score.sections)
        root, mode, _corr, _m = estimate_key(hist)
        scale = MAJOR_SCALE if mode == "maj" else MINOR_SCALE
        scale_pcs = {(root + d) % 12 for d in scale}
        total = sum(hist)
        chrom_ratio = sum(w for pc, w in enumerate(hist) if pc not in scale_pcs) / total if total else 0.0
        chromatic = band(chrom_ratio, -0.05, 0.02, 0.18, 0.5)
        return self._make(9, 0.6 * variety + 0.4 * chromatic,
                          {"types": dict(qualities), "chromatisme": round(chrom_ratio, 3)})

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
        v2 : 1-3 modulations = optimal, 0 = correct, > 6 = instable."""
        keys = []
        for s in self.score.sections:
            if s.harmony or s.melody_pitches:
                root, mode, _c, _m = estimate_key(self._pc_hist([s]))
                keys.append((root, mode))
        modulations = sum(1 for i in range(len(keys) - 1) if keys[i][0] != keys[i + 1][0])
        return self._make(11, band(modulations, -1, 0.5, 3, 7),
                          {"nb_modulations": modulations,
                           "centres": [f"{PC_NAMES[r]}{'' if m == 'maj' else 'm'}" for r, m in keys]})

    def _axe_12_harmonic_rhythm(self) -> StructuralAxis:
        chords = self._chord_chain()
        if len(chords) < 2:
            return self._make(12, 0.0)
        changes = sum(1 for i in range(len(chords) - 1)
                      if chords[i].root_pc != chords[i + 1].root_pc)
        ratio = changes / (len(chords) - 1)
        return self._make(12, band(ratio, 0.05, 0.25, 0.6, 0.95),
                          {"ratio_changement": round(ratio, 3)})

    def _axe_13_tonal_contrast(self) -> StructuralAxis:
        """Contraste tonal mesuré sur le cycle des quintes (v1 : distance MIDI
        brute entre fondamentales, dépendante de l'octave)."""
        secs = [s for s in self.score.sections if s.harmony or s.melody_pitches]
        if len(secs) < 2:
            return self._make(13, 0.0)
        roots = [estimate_key(self._pc_hist([s]))[0] for s in secs]
        dists = [fifths_distance(roots[i], roots[i + 1]) / 6.0 for i in range(len(roots) - 1)]
        avg = sum(dists) / len(dists)
        return self._make(13, band(avg, -0.05, 0.1, 0.45, 0.85),
                          {"moyenne_contraste": round(avg, 3)})

    def _axe_14_bass_line_melodic(self) -> StructuralAxis:
        roots = [c.root_pc for c in self._chord_chain()]
        if len(roots) < 3:
            return self._make(14, 0.0)
        moves = [min((roots[i + 1] - roots[i]) % 12, (roots[i] - roots[i + 1]) % 12)
                 for i in range(len(roots) - 1)]
        nonzero = [m for m in moves if m > 0]
        move_ratio = len(nonzero) / len(moves)
        good = sum(1 for m in nonzero if m in (1, 2, 5)) / len(nonzero) if nonzero else 0.0
        score = 0.5 * band(move_ratio, 0.2, 0.5, 0.85, 1.01) + 0.5 * good
        return self._make(14, score, {"ratio_mouvement": round(move_ratio, 3),
                                      "ratio_degres_quintes": round(good, 3)})

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
        temps). v1 : proxy sur les changements de nuances, sans rapport."""
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
        onsets = sorted(b for s in self.score.sections for b in s.onset_beats)
        if len(onsets) >= 8:
            iois = [round((b2 - b1) * 4) / 4 for b1, b2 in zip(onsets, onsets[1:]) if b2 - b1 > 1e-6]
            classes = Counter(min(i, 4.0) for i in iois)
            ent = _entropy_norm(classes, 8)
            off = sum(1 for b in onsets if 0.2 < (b % 1.0) < 0.8) / len(onsets)
            return self._make(23, 0.6 * ent + 0.4 * clamp01(off * 2),
                              {"classes_ioi": len(classes), "ratio_contretemps": round(off, 3)})
        textures = [t for _, t in self.score.texture_map]
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
        polys = [s.avg_polyphony for s in self.score.sections if s.avg_polyphony > 0]
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
        polys = [s.avg_polyphony for s in self.score.sections if s.avg_polyphony > 0]
        if polys:
            avg = sum(polys) / len(polys)
            return self._make(27, band(avg, 0.8, 1.8, 4.0, 8.0),
                              {"moyenne_voix": round(avg, 2)})
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
        axes.append(self._axe_29_global_cohesion(axes))
        self.axes = axes
        return self.axes

    def get_score(self) -> float:
        """Score SMS global (moyenne pondérée, poids somme = 1.0)."""
        if not self.axes:
            self.calculate()
        total_weight = sum(a.weight for a in self.axes)
        weighted = sum(a.score * a.weight for a in self.axes)
        return (weighted / total_weight) if total_weight > 0 else 0.0

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
            lines.append(f"[{ax.id}] {ax.name}: {ax.score:.2f} |{bar}|")
        lines.append("\n── Scores par groupe ──")
        for g, s in self.group_scores().items():
            lines.append(f"  {g} · {GROUP_NAMES[g]}: {s:.2f}")
        lines.append(f"\nSCORE GLOBAL SMS: {self.get_score():.2f}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        if not self.axes:
            self.calculate()
        return {
            "global_score": round(self.get_score(), 4),
            "groups": {g: round(s, 4) for g, s in self.group_scores().items()},
            "group_names": GROUP_NAMES,
            "axes": [
                {"id": a.id, "name": a.name, "weight": a.weight,
                 "group": AXES_META[int(a.id[:2])][3],
                 "score": round(a.score, 4), "details": a.details}
                for a in self.axes
            ],
        }
