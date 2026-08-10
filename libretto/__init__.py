"""Libretto — Sense of Musical Structure (SMS) : analyse structurelle
symbolique d'une œuvre musicale sur 29 axes pondérés, score global [0, 1]."""

__version__ = "0.1.0"

from .axes import AXES_META, GROUP_NAMES, SenseOfMusicalStructure, StructuralAxis
from .builder import build_score
from .emotion import EmotionProfile, profile_from_axes
from .library import Entry, Library, analyze_entry, search
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
    "EmotionProfile",
    "Entry",
    "Library",
    "NoteType",
    "Pitch",
    "Score",
    "Section",
    "SenseOfMusicalStructure",
    "StructuralAxis",
    "analyze_entry",
    "build_score",
    "parse_midi",
    "pitch_from_midi",
    "profile_from_axes",
    "search",
    "write_midi",
]
