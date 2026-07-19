"""
Libretto — modèle symbolique.

Types de base pour représenter une œuvre : hauteurs, accords, sections, partition.
API compatible avec la v1 (mêmes noms, mêmes champs positionnels) ; les champs
ajoutés (vélocité, polyphonie, densité, onsets) ont des valeurs par défaut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NoteType(Enum):
    C = 0
    D = 1
    E = 2
    F = 3
    G = 4
    A = 5
    B = 6


class Accidentals(Enum):
    NATURAL = 0
    SHARP = 1
    FLAT = -1


# Position diatonique -> demi-tons au-dessus de C.
# (La v1 utilisait l'index diatonique comme des demi-tons : D4 valait 61 = C#4.)
NOTE_SEMITONES = {
    NoteType.C: 0,
    NoteType.D: 2,
    NoteType.E: 4,
    NoteType.F: 5,
    NoteType.G: 7,
    NoteType.A: 9,
    NoteType.B: 11,
}

# Classe de hauteur -> épellation par défaut (dièses).
PC_TO_NOTE: list[tuple[NoteType, Accidentals]] = [
    (NoteType.C, Accidentals.NATURAL),
    (NoteType.C, Accidentals.SHARP),
    (NoteType.D, Accidentals.NATURAL),
    (NoteType.D, Accidentals.SHARP),
    (NoteType.E, Accidentals.NATURAL),
    (NoteType.F, Accidentals.NATURAL),
    (NoteType.F, Accidentals.SHARP),
    (NoteType.G, Accidentals.NATURAL),
    (NoteType.G, Accidentals.SHARP),
    (NoteType.A, Accidentals.NATURAL),
    (NoteType.A, Accidentals.SHARP),
    (NoteType.B, Accidentals.NATURAL),
]

PC_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

QUALITY_INTERVALS: dict[str, list[int]] = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "7": [0, 4, 7, 10],
    "dom7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
}


@dataclass(frozen=True)
class Pitch:
    note: NoteType
    accidental: Accidentals = Accidentals.NATURAL
    octave: int = 4

    @property
    def midi(self) -> int:
        # C4 = 60 : midi = 12 * (octave + 1) + demi-tons + altération
        return 12 * (self.octave + 1) + NOTE_SEMITONES[self.note] + self.accidental.value

    @property
    def pc(self) -> int:
        """Classe de hauteur 0-11 (indépendante de l'octave)."""
        return self.midi % 12

    @property
    def name(self) -> str:
        return f"{PC_NAMES[self.pc]}{self.octave}"

    def __str__(self) -> str:
        return self.name


def pitch_from_midi(m: int) -> Pitch:
    note, acc = PC_TO_NOTE[m % 12]
    return Pitch(note, acc, m // 12 - 1)


@dataclass
class Chord:
    root: Pitch
    intervals: list[int] = field(default_factory=list)  # demi-tons au-dessus de la fondamentale
    quality: str = "maj"

    @property
    def root_pc(self) -> int:
        return self.root.pc

    @property
    def chord_pcs(self) -> set[int]:
        ivs = QUALITY_INTERVALS.get(self.quality) or self.intervals or [0, 4, 7]
        return {(self.root_pc + iv) % 12 for iv in ivs}


@dataclass
class Section:
    id: str
    start_bar: int
    end_bar: int
    label: str  # "intro", "verse", "chorus", "bridge", "outro", "A", "B", ...
    harmony: list[Chord] = field(default_factory=list)
    melody_pitches: list[Pitch] = field(default_factory=list)
    tempo: int = 120
    # Champs enrichis (remplis par le builder MIDI ; défauts neutres sinon)
    mean_velocity: float = 0.0     # 0-127, 0 = inconnu
    avg_polyphony: float = 0.0     # voix simultanées moyennes, 0 = inconnu
    note_density: float = 0.0      # notes déclenchées / mesure, 0 = inconnu
    onset_beats: list[float] = field(default_factory=list)  # positions d'attaque en temps (float)

    @property
    def n_bars(self) -> int:
        return max(0, self.end_bar - self.start_bar)


@dataclass
class Score:
    """Représentation symbolique d'une œuvre."""
    sections: list[Section] = field(default_factory=list)
    key_signature: Pitch = field(default_factory=lambda: Pitch(NoteType.C))
    time_signature_num: int = 4
    time_signature_den: int = 4
    tempo_map: list[tuple[int, int]] = field(default_factory=list)   # (mesure, tempo)
    dynamics: list[tuple[int, str]] = field(default_factory=list)    # (mesure, "p"|"mf"|...)
    texture_map: list[tuple[int, str]] = field(default_factory=list)  # (mesure, "monophonic"|...)

    @property
    def all_chords(self) -> list[Chord]:
        out: list[Chord] = []
        for s in self.sections:
            out.extend(s.harmony)
        return out

    @property
    def all_melody(self) -> list[Pitch]:
        out: list[Pitch] = []
        for s in self.sections:
            out.extend(s.melody_pitches)
        return out
