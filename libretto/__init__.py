"""Libretto — Sense of Musical Structure (SMS) : analyse structurelle
symbolique d'une œuvre musicale sur 29 axes pondérés, score global [0, 1]."""

__version__ = "0.1.0"

from .axes import AXES_META, GROUP_NAMES, SenseOfMusicalStructure, StructuralAxis
from .builder import build_score
from .midi import parse_midi, write_midi
from .model import (
    Accidentals,
    Chord,
    NoteType,
    Pitch,
    Score,
    Section,
    pitch_from_midi,
)

__all__ = [
    "AXES_META",
    "GROUP_NAMES",
    "Accidentals",
    "Chord",
    "NoteType",
    "Pitch",
    "Score",
    "Section",
    "SenseOfMusicalStructure",
    "StructuralAxis",
    "build_score",
    "parse_midi",
    "pitch_from_midi",
    "write_midi",
]
