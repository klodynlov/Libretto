"""
Génère un corpus de validation : N morceaux symboliques variés et structurés.

Pourquoi ce script existe
-------------------------
Calibrer et valider Libretto demande des **morceaux**. Les packs de loops du
commerce n'en sont pas : 4 à 8 mesures d'un seul instrument, sans sections,
sans mélodie, sans harmonie. Mesurer les 29 axes dessus ne mesure que du
bruit. Faute de corpus MIDI annoté librement redistribuable, on en fabrique
un : procédural, déterministe (graine), 100 % stdlib, versionnable en 400
lignes plutôt qu'en 300 Mo de MIDI.

Ce que ça vaut, et ce que ça ne vaut pas
---------------------------------------
Un corpus synthétique ne prouve PAS que le score SMS corrèle avec le goût
humain — seule une écoute annotée le ferait. Il prouve autre chose, qui est
exactement ce que la calibration contrastive demande : qu'un morceau
structuré batte sa propre version dégradée. Cette question est robuste à la
synthèse, parce que la dégradation s'applique au fichier généré lui-même —
si l'original et ses mesures permutées scorent pareil, l'axe est cassé,
quelle que soit l'origine du fichier.

Le risque réel est la circularité : générer uniquement ce que les axes
récompensent déjà. On la combat en variant *au-delà* de la zone de confort
des axes, y compris là où le moteur est faible :

- formes non pop : AABA, rondo, binaire, et **à travers-composé** (aucune
  répétition — l'axe 6 le pénalise, c'est voulu) ;
- sections de 7, 10, 12 mesures (l'axe 24 exige des multiples de 4) ;
- **mesures composées et impaires** (6/8, 12/8, 3/4, 5/4) alors que le
  moteur raisonne en noires ;
- modes (dorien, mixolydien) contre le profil majeur/mineur de
  Krumhansl-Kessler ;
- arcs plats et descendants, pas seulement l'arche que l'axe 28 attend ;
- la moitié des fichiers SANS marqueurs, pour exercer la segmentation par
  nouveauté au lieu de la court-circuiter.

Aucun paramètre de ce générateur n'a été réglé en regardant les scores SMS
obtenus. Le jour où un vrai corpus annoté existe, il remplace celui-ci.

Usage : python3 examples/make_corpus.py <dossier_sortie> [nb=60] [graine=7]
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import write_midi  # noqa: E402

DRUMS = 9

# ── Matériau harmonique : degrés en demi-tons au-dessus de la tonique ──
# (degré, qualité). Les progressions restent idiomatiques de leur mode ;
# c'est le mode lui-même qui varie, pas la grammaire interne.
PROGRESSIONS: dict[str, list[list[tuple[int, str]]]] = {
    "maj": [
        [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")],      # I–V–vi–IV
        [(0, "maj"), (5, "maj"), (7, "maj"), (0, "maj")],      # I–IV–V–I
        [(9, "min"), (5, "maj"), (0, "maj"), (7, "maj")],      # vi–IV–I–V
        [(0, "maj"), (9, "min"), (2, "min"), (7, "maj")],      # I–vi–ii–V
        [(0, "maj"), (4, "min"), (5, "maj"), (7, "maj")],      # I–iii–IV–V
    ],
    "min": [
        [(0, "min"), (8, "maj"), (3, "maj"), (10, "maj")],     # i–VI–III–VII
        [(0, "min"), (5, "min"), (7, "min"), (0, "min")],      # i–iv–v–i
        [(0, "min"), (10, "maj"), (8, "maj"), (10, "maj")],    # i–VII–VI–VII
        [(0, "min"), (3, "maj"), (8, "maj"), (7, "maj")],      # i–III–VI–V
    ],
    # Modes : la couleur tient à un seul degré caractéristique (VI majeur en
    # dorien, VII majeur en mixolydien). Krumhansl-Kessler n'a pas de profil
    # pour eux — le moteur doit se débrouiller.
    "dorien": [
        [(0, "min"), (5, "maj"), (0, "min"), (10, "maj")],     # i–IV–i–VII
        [(0, "min"), (2, "min"), (5, "maj"), (0, "min")],
    ],
    "mixolydien": [
        [(0, "maj"), (10, "maj"), (5, "maj"), (0, "maj")],     # I–VII–IV–I
        [(0, "maj"), (5, "maj"), (10, "maj"), (5, "maj")],
    ],
}

SCALES: dict[str, tuple[int, ...]] = {
    "maj": (0, 2, 4, 5, 7, 9, 11),
    "min": (0, 2, 3, 5, 7, 8, 10),
    "dorien": (0, 2, 3, 5, 7, 9, 10),
    "mixolydien": (0, 2, 4, 5, 7, 9, 10),
}

TRIADS = {"maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6)}

# (numérateur, dénominateur, noires par mesure, pulsation en noires)
# 6/8 et 12/8 sont ternaires : la pulsation vaut une noire pointée (1.5).
METERS: list[tuple[int, int, float, float]] = [
    (4, 4, 4.0, 1.0),
    (4, 4, 4.0, 1.0),      # doublé : 4/4 reste majoritaire, comme en vrai
    (3, 4, 3.0, 1.0),
    (6, 8, 3.0, 1.5),
    (12, 8, 6.0, 1.5),
    (5, 4, 5.0, 1.0),
]

# Formes : liste de rôles. Le nom de rôle pilote l'énergie et le matériau.
FORMS: dict[str, list[str]] = {
    "verse_chorus": ["intro", "verse", "chorus", "verse", "chorus", "bridge",
                     "chorus", "outro"],
    "verse_chorus_court": ["verse", "chorus", "verse", "chorus"],
    "aaba": ["A", "A", "B", "A"],
    "rondo": ["A", "B", "A", "C", "A"],
    "binaire": ["A", "A", "B", "B"],
    "ternaire": ["A", "B", "A"],
    # Aucune section ne revient : l'axe 6 (répétition) le punira, et c'est
    # correct — la musique à travers-composée existe.
    "travers_compose": ["A", "B", "C", "D", "E"],
}

# rôle -> échelle d'énergie
ROLE_ENERGY = {
    "intro": 0.55, "verse": 0.75, "A": 0.78, "chorus": 1.0, "B": 0.9,
    "bridge": 0.65, "C": 0.85, "D": 0.8, "E": 0.72, "outro": 0.5,
}

# rôle -> pupitres actifs. Aucun morceau ne garde le même effectif du début
# à la fin : une intro est dépouillée, un refrain est plein, un pont retire
# la batterie. C'est précisément ce que l'axe 25 (variété texturale) est
# censé voir, et un corpus à orchestration constante ne peut pas le valider.
ROLE_VOICES = {
    "intro":  {"chords", "bass"},
    "verse":  {"chords", "melody", "bass", "drums"},
    "A":      {"chords", "melody", "bass", "drums"},
    "chorus": {"chords", "melody", "bass", "pad", "drums"},
    "B":      {"chords", "melody", "bass", "pad", "drums"},
    "bridge": {"chords", "melody", "pad"},
    "C":      {"chords", "melody", "bass", "pad"},
    "D":      {"chords", "melody", "bass", "drums"},
    "E":      {"chords", "bass", "drums"},
    "outro":  {"chords", "bass"},
}

MARKER_NAMES = {"intro": "Intro", "verse": "Couplet", "chorus": "Refrain",
                "bridge": "Pont", "outro": "Outro"}


@dataclass
class Style:
    """Tous les degrés de liberté d'un morceau généré."""
    tonic: int              # classe de hauteur 0-11
    mode: str
    meter: tuple[int, int, float, float]
    bpm: float
    form: str
    bars_per_section: int
    arc: str                # "arche" | "montee" | "plat" | "descente"
    swing: bool             # place des attaques sur les temps faibles
    # Proportion de pulsations où le temps fort est omis au profit de la
    # subdivision faible — une vraie syncope, pas un décalage. Sans ça le
    # corpus est intégralement carré et l'axe 20 n'a rien à mesurer.
    syncopation: float
    with_markers: bool
    with_drums: bool
    tempo_drift: bool
    n_voices: int           # 2 à 5
    # Modulation : demi-tons appliqués à partir de la section `modulate_at`
    # (0 = pas de modulation). Sans ça, aucun morceau du corpus ne change de
    # centre tonal et les axes 11 et 13 n'ont rien à mesurer.
    modulate_at: int
    modulate_by: int


def random_style(rng: random.Random) -> Style:
    mode = rng.choice(["maj", "maj", "min", "min", "dorien", "mixolydien"])
    modulates = rng.random() < 0.35
    return Style(
        tonic=rng.randrange(12),
        mode=mode,
        meter=rng.choice(METERS),
        bpm=float(rng.choice([64, 72, 84, 90, 96, 100, 112, 120, 128, 140, 168])),
        form=rng.choice(list(FORMS)),
        # 7 et 10 mesures : hors carrure, l'axe 24 doit le voir sans que ça
        # contamine les autres axes.
        bars_per_section=rng.choice([4, 4, 8, 8, 8, 7, 10, 12, 16]),
        arc=rng.choice(["arche", "arche", "montee", "plat", "descente"]),
        swing=rng.random() < 0.35,
        syncopation=rng.choice([0.0, 0.0, 0.25, 0.45]),
        with_markers=rng.random() < 0.5,
        with_drums=rng.random() < 0.75,
        tempo_drift=rng.random() < 0.3,
        n_voices=rng.randrange(2, 6),
        modulate_at=rng.randrange(2, 5) if modulates else 0,
        # Tonalités voisines (±quinte, relatif, ton au-dessus) : les
        # modulations que la musique tonale pratique réellement.
        modulate_by=rng.choice([2, 3, 5, 7, 9, -3, -5]) if modulates else 0,
    )


def section_bars(role: str, bars_per_section: int) -> int:
    """Mesures RÉELLES d'une section : intro et outro sont écourtées de moitié.

    Publique parce qu'elle est la seule vérité sur la longueur d'un morceau :
    `bars_per_section` est une consigne, pas un décompte. Qui veut prédire la
    durée sans rendre le MIDI passe par ici (cf. `total_bars`)."""
    if role in ("intro", "outro"):
        return max(2, bars_per_section // 2)
    return bars_per_section


def total_bars(form: str, bars_per_section: int) -> int:
    """Longueur totale d'un morceau, en mesures, écourtements compris."""
    return sum(section_bars(r, bars_per_section) for r in FORMS[form])


def _arc_scale(arc: str, i: int, n: int) -> float:
    """Facteur de vélocité de la section i sur n, selon l'arc voulu."""
    if n <= 1:
        return 1.0
    x = i / (n - 1)
    if arc == "arche":
        return 0.62 + 0.38 * (1.0 - abs(x - 0.65) / 0.65)
    if arc == "montee":
        return 0.60 + 0.40 * x
    if arc == "descente":
        return 1.00 - 0.38 * x
    return 0.82  # plat


def _motif(rng: random.Random, scale: tuple[int, ...], length: int) -> list[int]:
    """Motif en DEGRÉS d'échelle (pas en demi-tons) : la transposition
    diatonique reste dans le mode."""
    deg = rng.randrange(0, 4)
    out = [deg]
    for _ in range(length - 1):
        step = rng.choice([-2, -1, -1, 1, 1, 2, 3])
        deg = max(-3, min(9, deg + step))
        out.append(deg)
    return out


def _degree_to_midi(deg: int, scale: tuple[int, ...], tonic: int, octave: int) -> int:
    octaves, idx = divmod(deg, len(scale))
    return 12 * (octave + 1) + tonic + scale[idx] + 12 * octaves


def render(style: Style, rng: random.Random) -> tuple[list[list], list, list, int, list[int], list[str]]:
    """(pistes, marqueurs, changements de tempo, nb de mesures, frontières de
    référence en index de mesure, étiquette de rôle par section).

    Les frontières sont la vérité terrain : le générateur sait exactement où
    il a coupé. C'est ce qui permet d'évaluer la segmentation par F-mesure
    au lieu de l'estimer à l'œil — sans annotations, on ne peut que constater
    qu'un fichier ressort en une seule section, pas mesurer si les frontières
    trouvées sont les bonnes."""
    num, den, bar_beats, pulse = style.meter
    scale = SCALES[style.mode]
    prog_bank = PROGRESSIONS[style.mode]
    roles = FORMS[style.form]

    # Un motif par rôle distinct, réutilisé à chaque retour de la section :
    # c'est ce qui fait qu'un morceau a une identité et que sa version aux
    # hauteurs permutées doit perdre.
    distinct = sorted(set(roles))
    motifs = {r: _motif(rng, scale, rng.choice([4, 5, 6, 8])) for r in distinct}
    progs = {r: prog_bank[rng.randrange(len(prog_bank))] for r in distinct}
    octaves = {r: rng.choice([4, 5]) for r in distinct}

    chords_tr, mel_tr, bass_tr, drums_tr = [], [], [], []
    pad_tr = []
    markers: list[tuple[float, str]] = []
    tempo_changes: list[tuple[float, float]] = []
    truth_bars: list[int] = []      # frontières de référence, en mesures
    bar_cursor = 0

    beat = 0.0
    n_sections = len(roles)
    for i, role in enumerate(roles):
        vel_scale = _arc_scale(style.arc, i, n_sections) * ROLE_ENERGY.get(role, 0.8)
        vel = max(28, min(120, round(96 * vel_scale)))
        bars = section_bars(role, style.bars_per_section)
        truth_bars.append(bar_cursor)
        bar_cursor += bars
        if style.with_markers:
            markers.append((beat, MARKER_NAMES.get(role, role)))
        if style.tempo_drift and role in ("bridge", "B", "C"):
            tempo_changes.append((beat, round(style.bpm * 0.94)))
        elif style.tempo_drift and tempo_changes and role == "chorus":
            tempo_changes.append((beat, style.bpm))

        prog = progs[role]
        motif = motifs[role]
        octave = octaves[role]
        section_start = beat
        voices = ROLE_VOICES.get(role, {"chords", "melody", "bass", "drums"})
        # Modulation : à partir de la section prévue, tout est transposé.
        shift = style.modulate_by if (style.modulate_at and i >= style.modulate_at) else 0
        tonic = (style.tonic + shift) % 12

        for bar in range(bars):
            degree, quality = prog[bar % len(prog)]
            root = 12 * 4 + tonic + degree
            # accords : une attaque par pulsation, tenue jusqu'à la suivante
            n_pulses = max(1, int(round(bar_beats / pulse)))
            sub = 3 if pulse == 1.5 else 2
            # Pulsations syncopées : le temps fort est sauté, l'attaque tombe
            # une subdivision plus tard. Jamais la première pulsation de la
            # mesure — une mesure qui ne pose pas son premier temps ne se
            # perçoit plus comme une mesure.
            sync_pulses = {p for p in range(1, n_pulses)
                           if style.syncopation and rng.random() < style.syncopation}

            def onset_of(p: int) -> float:
                return beat + p * pulse + (pulse / sub if p in sync_pulses else 0.0)

            if "chords" in voices:
                for p in range(n_pulses):
                    t = onset_of(p)
                    hold = pulse * 0.92
                    for iv in TRIADS[quality]:
                        chords_tr.append((t, hold, root + iv, vel, 0))
            if style.n_voices >= 4 and "pad" in voices:
                pad_tr.append((beat, bar_beats * 0.95, root + 12, max(24, vel - 30), 4))
            # basse : fondamentale sur le premier temps + relance
            if "bass" in voices:
                bass_tr.append((beat, pulse * 0.9, root - 24, min(127, vel + 6), 1))
                if style.n_voices >= 3 and n_pulses > 1:
                    # relance sur la subdivision faible : sur la grille, donc
                    # lisible comme contretemps et non comme jeu imprécis
                    bass_tr.append((onset_of(1) + pulse / sub, pulse * 0.6,
                                    root - 24 + 7, max(24, vel - 12), 1))
            beat += bar_beats

        # mélodie : le motif du rôle, étalé sur la section, transposé
        # diatoniquement d'un degré à chaque répétition
        section_beats = beat - section_start
        sub = 3 if pulse == 1.5 else 2
        grid = pulse / sub
        raw_step = section_beats / max(1, len(motif) * max(1, bars // 2))
        # Quantifié sur la grille de subdivision : une mélodie réelle tombe
        # sur la grille métrique. Un pas quelconque produisait des attaques
        # à des positions arbitraires — inanalysable rythmiquement, et faux
        # comme modèle de mélodie.
        step = max(grid, round(raw_step / grid) * grid)
        t = section_start
        rep = 0
        while t < beat - 1e-6 and "melody" in voices:
            for k, deg in enumerate(motif):
                if t >= beat - 1e-6:
                    break
                pitch = _degree_to_midi(deg + rep, scale, tonic, octave)
                pitch = max(36, min(100, pitch))
                # swing : la note paire recule d'une subdivision — sur la
                # grille, donc lisible comme contretemps
                shift = grid if (style.swing and k % 2 == 1 and step > grid) else 0.0
                mel_tr.append((t + shift, step * 0.85, pitch,
                               min(127, vel + 12), 2))
                t += step
            rep = (rep + 1) % 3

        if style.with_drums and "drums" in voices:
            t = section_start
            while t < beat - 1e-6:
                drums_tr.append((t, 0.3, 36, vel, DRUMS))                    # kick
                mid = t + (bar_beats / 2.0)
                if mid < beat:
                    # la caisse claire suit la syncope quand il y en a une
                    hit = mid + (grid if style.syncopation >= 0.45 else 0.0)
                    drums_tr.append((min(hit, beat - 0.1), 0.3, 38, vel, DRUMS))
                h = t
                while h < t + bar_beats - 1e-6 and h < beat:
                    drums_tr.append((h, 0.15, 42, max(20, vel - 28), DRUMS))
                    h += grid
                t += bar_beats

    tracks = [chords_tr, mel_tr, bass_tr]
    if style.n_voices >= 4 and pad_tr:
        tracks.append(pad_tr)
    if style.with_drums:
        tracks.append(drums_tr)
    return (tracks, markers, tempo_changes, int(round(beat / bar_beats)),
            truth_bars, list(roles))


def build_corpus(out_dir: str | Path, n: int = 60, seed: int = 7) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    truth: dict[str, dict] = {}
    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        style = random_style(rng)
        tracks, markers, tempo_changes, bars, truth_bars, roles = render(style, rng)
        num, den, _bb, _p = style.meter
        name = (f"{i:03d}_{style.form}_{style.mode}_{num}-{den}_"
                f"{int(style.bpm)}bpm_{style.arc}.mid")
        path = out / name
        write_midi(path, tracks, ppq=480, bpm=style.bpm,
                   time_sig=(num, den), markers=markers or None,
                   tempo_changes=tempo_changes or None)
        written.append(path)
        truth[name] = {"boundaries": truth_bars, "roles": roles,
                       "n_bars": bars, "form": style.form,
                       "with_markers": style.with_markers,
                       # Tonalité écrite : sert de contrôle positif à
                       # l'estimateur Krumhansl-Kessler (scripts/
                       # mesure_tonalite.py --controle), qui n'a sinon aucune
                       # matière dont la tonalité soit connue par construction.
                       # La modulation est notée : sur ces morceaux-là, il n'y
                       # a pas une tonalité mais deux.
                       "tonic": style.tonic, "mode": style.mode,
                       "modulate_at": style.modulate_at,
                       "modulate_by": style.modulate_by}
    # Vérité terrain à côté du corpus : c'est elle qui rend la segmentation
    # évaluable (F-mesure des frontières) au lieu de seulement observable.
    (out / "annotations.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    directory = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    graine = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    files = build_corpus(directory, count, graine)
    print(f"écrit : {len(files)} morceaux dans {directory}")
