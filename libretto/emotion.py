"""
Libretto — profil émotionnel : lire une intention affective dans les axes.

L'idée
------
Libretto mesure la *structure* (29 axes). Ce module traduit une partie de
ces axes — plus le mode et le tempo — en trois coordonnées affectives
**interprétables**, dans [0, 1] :

  · **valence**  — sombre ↔ lumineux (l'axe « triste/joyeux »)
  · **énergie**  — calme ↔ intense (l'axe « arousal »)
  · **tension**  — stable ↔ instable (dissonance, modulation, dérive tonale)

Ce n'est **pas** un modèle entraîné sur des annotations affectives, et ce
module ne le cache pas : c'est une projection **transparente et
déterministe** des grandeurs que Libretto sait déjà mesurer, calée sur ce
que la théorie musicale tient pour acquis — le mode majeur tire la valence
vers le haut, le mineur vers le bas ; le tempo et la densité rythmique
tirent l'énergie ; la complexité harmonique et l'instabilité tonale tirent
la tension. Chaque contribution est nommée dans le champ `rationale`, pour
qu'un désaccord porte sur une pondération lisible et non sur une boîte
noire. La validation à l'oreille reste, ici comme ailleurs dans Libretto,
la seule preuve (voir `annotate`/`agreement`).

Les descripteurs (« mélancolique », « planant », « tendu »…) sont des
points fixes de cet espace : étiqueter une pièce, c'est nommer les points
les plus proches de sa position ; chercher « quelque chose de mélancolique »,
c'est viser le point du mot et classer la bibliothèque par distance
(`library.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .axes import PARENT_MODE, PC_NAMES, SenseOfMusicalStructure, clamp01

# ──────────────────────────────────────────────
# Descripteurs : points fixes de l'espace (valence, énergie, tension)
# ──────────────────────────────────────────────
#
# Chaque descripteur est une ancre. Les coordonnées ne prétendent pas à une
# vérité psychométrique — ce sont des repères cohérents entre eux, choisis
# pour couvrir le plan valence×énergie et border les extrêmes de tension.
# La tension pèse moins que les deux autres axes dans le rapprochement
# (DIST_WEIGHTS) : c'est une nuance, pas une dimension principale.
DESCRIPTORS: dict[str, tuple[float, float, float]] = {
    # valence haute (lumineux)
    "joyeux":        (0.85, 0.72, 0.20),
    "euphorique":    (0.92, 0.90, 0.35),
    "triomphal":     (0.85, 0.85, 0.35),
    "énergique":     (0.70, 0.85, 0.30),
    "enjoué":        (0.80, 0.65, 0.25),
    "lumineux":      (0.80, 0.50, 0.15),
    "tendre":        (0.72, 0.35, 0.15),
    "serein":        (0.72, 0.22, 0.10),
    "planant":       (0.60, 0.30, 0.22),
    "calme":         (0.60, 0.15, 0.15),
    # valence médiane (doux-amer)
    "nostalgique":   (0.45, 0.35, 0.35),
    "contemplatif":  (0.50, 0.20, 0.22),
    "solennel":      (0.42, 0.38, 0.38),
    "puissant":      (0.55, 0.90, 0.50),
    # valence basse (sombre)
    "intime":        (0.40, 0.20, 0.20),
    "mélancolique":  (0.25, 0.30, 0.35),
    "triste":        (0.20, 0.25, 0.30),
    "sombre":        (0.15, 0.38, 0.52),
    "tendu":         (0.30, 0.68, 0.80),
    "dramatique":    (0.28, 0.82, 0.75),
    "agité":         (0.35, 0.85, 0.65),
    "angoissant":    (0.15, 0.62, 0.88),
}

# Synonymes et variantes → descripteur canonique. Sert au marquage (jamais)
# et surtout à l'analyse d'une requête en langage libre : « mélodie triste et
# lente » doit viser le bon point même si le mot exact n'est pas une clé de
# DESCRIPTORS. Tout est comparé après repli des accents et minuscules.
SYNONYMS: dict[str, str] = {
    "heureux": "joyeux", "gai": "joyeux", "joie": "joyeux", "solaire": "lumineux",
    "radieux": "lumineux", "éclatant": "lumineux", "festif": "enjoué",
    "dansant": "énergique", "entraînant": "énergique", "vif": "énergique",
    "dynamique": "énergique", "épique": "triomphal", "victorieux": "triomphal",
    "grandiose": "triomphal", "extatique": "euphorique",
    "doux": "tendre", "chaleureux": "tendre", "romantique": "tendre",
    "paisible": "serein", "apaisant": "serein", "reposant": "calme",
    "tranquille": "calme", "zen": "calme", "aérien": "planant",
    "rêveur": "planant", "flottant": "planant", "éthéré": "planant",
    "méditatif": "contemplatif", "introspectif": "contemplatif",
    "nostalgie": "nostalgique", "doux-amer": "nostalgique", "doux amer": "nostalgique",
    "mélancolie": "mélancolique", "mélancolique": "mélancolique",
    "morose": "mélancolique", "spleen": "mélancolique",
    "triste": "triste", "tristesse": "triste", "pleurant": "triste",
    "larmoyant": "triste", "déprimé": "triste", "peiné": "triste",
    "grave": "solennel", "cérémonial": "solennel", "recueilli": "solennel",
    "intimiste": "intime", "feutré": "intime", "nocturne": "intime",
    "obscur": "sombre", "ténébreux": "sombre", "menaçant": "angoissant",
    "inquiétant": "angoissant", "anxieux": "angoissant", "sinistre": "angoissant",
    "oppressant": "angoissant", "stressant": "tendu", "nerveux": "tendu",
    "sous tension": "tendu", "suspense": "tendu", "haletant": "agité",
    "frénétique": "agité", "chaotique": "agité", "fébrile": "agité",
    "intense": "puissant", "massif": "puissant", "héroïque": "triomphal",
    "sombre et tendu": "sombre", "théâtral": "dramatique", "pathétique": "dramatique",
    "poignant": "dramatique",
}

# Poids de la distance dans l'espace affectif : la tension nuance, elle ne
# commande pas. Deux pièces également lumineuses et calmes sont proches même
# si l'une est un peu plus « instable » que l'autre.
DIST_WEIGHTS = (1.0, 1.0, 0.6)


@dataclass
class EmotionProfile:
    """Position affective d'une pièce, plus les étiquettes les plus proches.

    `rationale` garde la trace de chaque contribution (mode, tempo, axes),
    pour qu'un profil se discute au lieu de se subir."""
    valence: float
    energy: float
    tension: float
    arc: float                       # axe 28 (arche/montée), informatif
    descriptors: list[str] = field(default_factory=list)
    rationale: dict[str, float] = field(default_factory=dict)

    @property
    def point(self) -> tuple[float, float, float]:
        return (self.valence, self.energy, self.tension)

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "energy": round(self.energy, 4),
            "tension": round(self.tension, 4),
            "arc": round(self.arc, 4),
            "descriptors": self.descriptors,
            "rationale": {k: round(v, 4) for k, v in self.rationale.items()},
        }


# Valence de départ selon le mode. Le mineur n'est pas « triste » par
# décret — mais, toutes choses égales par ailleurs, il ancre la valence plus
# bas que le majeur, et c'est le signal isolé le plus fort dont on dispose.
# Le dorien et le mixolydien (modes mixtes) tombent entre les deux.
MODE_VALENCE = {"maj": 0.72, "mixolydien": 0.60, "dorien": 0.44, "min": 0.30}

# Bornes de normalisation du tempo en énergie. En dessous de SLOW → 0, au
# dessus de FAST → 1, linéaire entre les deux. Larges à dessein : un adagio
# n'est pas à 55, un presto pas à 175, mais au-delà l'information sature.
BPM_SLOW, BPM_FAST = 55.0, 175.0


def _axis_scores(sms: SenseOfMusicalStructure) -> dict[int, float]:
    if not sms.axes:
        sms.calculate()
    return {int(a.id[:2]): a.score for a in sms.axes}


def _bpm_energy(bpm: float | None) -> float | None:
    if not bpm or bpm <= 0:
        return None
    return clamp01((bpm - BPM_SLOW) / (BPM_FAST - BPM_SLOW))


def profile_from_axes(sms: SenseOfMusicalStructure,
                      *,
                      mode: str = "maj",
                      bpm: float | None = None,
                      density: float | None = None,
                      top: int = 4) -> EmotionProfile:
    """Projette les axes (+ mode, tempo, densité) sur (valence, énergie,
    tension) et nomme les descripteurs les plus proches.

    `mode` accepte les quatre modes rendus par `estimate_key`
    (maj/min/dorien/mixolydien) ; tout autre libellé retombe sur son mode
    parent via `PARENT_MODE`, ou sur une valence neutre.
    """
    ax = _axis_scores(sms)

    def a(n: int, default: float = 0.5) -> float:
        return ax.get(n, default)

    # ── VALENCE : mode d'abord, cadence et stabilité en nuance ──
    base = MODE_VALENCE.get(mode)
    if base is None:
        base = MODE_VALENCE.get(PARENT_MODE.get(mode, ""), 0.5)
    cadence, key_stab = a(10), a(8)
    valence = clamp01(base + 0.15 * (cadence - 0.5) + 0.10 * (key_stab - 0.5))

    # ── ÉNERGIE : tempo (quand connu) + rythme, texture, dynamique ──
    e_axes = (0.30 * a(23)     # complexité rythmique
              + 0.20 * a(20)   # syncopes
              + 0.20 * a(27)   # polyphonie
              + 0.15 * a(26)   # gamme dynamique
              + 0.15 * a(5))   # progression énergétique
    e_bpm = _bpm_energy(bpm)
    if e_bpm is None:
        energy = e_axes
    else:
        energy = 0.55 * e_bpm + 0.45 * e_axes
    if density and density > 0:
        # densité de notes/mesure : au-delà de ~8, texture dense. Petit
        # coup de pouce, borné, jamais décisif seul.
        energy = clamp01(energy + 0.10 * (clamp01(density / 8.0) - 0.5))
    energy = clamp01(energy)

    # ── TENSION : complexité harmonique, modulation, instabilité tonale ──
    instability = 1.0 - key_stab
    tension = clamp01(0.30 * a(9)          # complexité harmonique
                      + 0.22 * a(11)        # modulations
                      + 0.25 * instability  # dérive tonale
                      + 0.13 * a(19)        # caractère intervalique (sauts)
                      + 0.10 * a(13))       # contraste tonal

    arc = a(28)

    rationale = {
        "mode_base": base,
        "cadence": cadence,
        "key_stability": key_stab,
        "bpm_energy": e_bpm if e_bpm is not None else -1.0,
        "rhythmic_complexity": a(23),
        "harmonic_complexity": a(9),
        "modulation": a(11),
    }

    prof = EmotionProfile(valence=valence, energy=energy, tension=tension,
                          arc=arc, rationale=rationale)
    prof.descriptors = nearest_descriptors(prof.point, top=top)
    return prof


def weighted_distance(p: tuple[float, float, float],
                      q: tuple[float, float, float]) -> float:
    """Distance euclidienne pondérée dans l'espace affectif (tension à poids
    réduit — voir DIST_WEIGHTS)."""
    return sum(w * (a - b) ** 2 for w, a, b in zip(DIST_WEIGHTS, p, q)) ** 0.5


# Rayon au-delà duquel un descripteur n'est plus « proche ». Un point au
# centre du cube peut rester sans étiquette forte plutôt que d'en collecter
# de contradictoires.
NEAR_RADIUS = 0.28


def nearest_descriptors(point: tuple[float, float, float],
                        top: int = 4) -> list[str]:
    """Descripteurs les plus proches d'un point, du plus proche au plus
    loin. Toujours au moins un (le plus proche), puis ceux sous NEAR_RADIUS,
    dans la limite de `top`."""
    ranked = sorted(DESCRIPTORS.items(),
                    key=lambda kv: weighted_distance(point, kv[1]))
    out = [ranked[0][0]]
    for name, coord in ranked[1:top]:
        if weighted_distance(point, coord) <= NEAR_RADIUS:
            out.append(name)
    return out


def _fold(text: str) -> str:
    """Minuscule sans accents, pour comparer les mots d'une requête aux clés."""
    table = str.maketrans("àâäáãéèêëíìîïóòôöõúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    return text.lower().translate(table)


# Index replié des noms de descripteurs et synonymes → coordonnées.
_LEXICON: dict[str, tuple[float, float, float]] = {}
for _name, _coord in DESCRIPTORS.items():
    _LEXICON[_fold(_name)] = _coord
for _syn, _canon in SYNONYMS.items():
    _LEXICON.setdefault(_fold(_syn), DESCRIPTORS[_canon])


def target_from_words(text: str) -> tuple[tuple[float, float, float], list[str]] | None:
    """Extrait une cible affective d'une requête libre : moyenne des
    coordonnées des mots reconnus. Renvoie (point, mots_reconnus), ou None
    si aucun mot affectif n'est trouvé — le classement retombe alors sur le
    score structurel (comme Forge : la construction d'abord).

    Repère aussi les expressions de deux mots (« doux amer », « sous tension »)
    avant les mots isolés."""
    folded = _fold(text)
    tokens = [t for t in _split(folded) if t]
    matched: list[tuple[float, float, float]] = []
    hits: list[str] = []
    i = 0
    while i < len(tokens):
        bigram = f"{tokens[i]} {tokens[i + 1]}" if i + 1 < len(tokens) else None
        if bigram and bigram in _LEXICON:
            matched.append(_LEXICON[bigram])
            hits.append(bigram)
            i += 2
            continue
        if tokens[i] in _LEXICON:
            matched.append(_LEXICON[tokens[i]])
            hits.append(tokens[i])
        i += 1
    if not matched:
        return None
    n = len(matched)
    point = tuple(sum(c[k] for c in matched) / n for k in range(3))
    return point, hits


def _split(text: str) -> list[str]:
    """Découpe en mots : tout ce qui n'est pas lettre est une frontière."""
    out, cur = [], []
    for ch in text:
        if ch.isalpha():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


def mode_name(mode: str) -> str:
    """Libellé court d'un mode pour l'affichage."""
    return {"maj": "majeur", "min": "mineur",
            "dorien": "dorien", "mixolydien": "mixolydien"}.get(mode, mode)


def key_label(tonic_pc: int | None, mode: str | None) -> str | None:
    """« Ré mineur » à partir de (classe de hauteur, mode). None si inconnu."""
    if tonic_pc is None or mode is None:
        return None
    return f"{PC_NAMES[tonic_pc % 12]} {mode_name(mode)}"
