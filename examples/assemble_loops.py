"""
Assemble un morceau complet à partir de loops MIDI (pack « sloopy », amapiano).

Principe : chaque loop est normalisé en beats, transposé vers la tonalité
cible, puis tuilé sur les sections d'un PLAN d'arrangement (intro/couplet/
refrain/pont/outro) avec une échelle de vélocité par section. Marqueurs et
changements de tempo écrits dans le MIDI → analysables par Libretto.

Usage : python3 examples/assemble_loops.py <dossier_loops> <sortie.mid>

Le PLAN référence les fichiers du pack sloopy ; adapter les noms/transpositions
pour un autre pack (tonalités estimées via `libretto.axes.estimate_key`).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import parse_midi, write_midi  # noqa: E402

BPM = 104.0
BEATS_PER_BAR = 4

# rôle -> (fichier, transposition demi-tons, canal MIDI)
# Cible : Mi mineur. Loops Em natifs = 0 ; Ré -> +2 ; La -> -5.
LOOPS = {
    "chords_verse":  ("25_Chords.mid",   0,  0),
    "chords_chorus": ("66_Chords.mid",   0,  1),
    "chords_dark":   ("142_Chords.mid",  0,  2),   # graves sombres, pont
    "pad":           ("29_Pad.mid",      0,  3),
    "plucks":        ("29_Plucks.mid",   0,  4),
    "marimba":       ("68_Marimba.mid",  0,  5),
    "melody":        ("87_Melody.mid",   2,  6),   # Ré min -> Mi min
    "bass":          ("173_Bass.mid",    2,  7),   # Ré -> Mi
    "logdrum":       ("160_Log Drum.mid", 2, 8),   # Ré min -> Mi min
    "logdrum_hi":    ("94_Log Drum.mid", -5, 10),  # La -> Mi, registre bas gardé
}

# (marqueur, mesures, vélocité, rôles actifs)
PLAN = [
    ("Intro",   8, 0.55, ["pad", "chords_verse"]),
    ("Couplet", 8, 0.75, ["chords_verse", "bass", "logdrum"]),
    ("Refrain", 8, 1.00, ["chords_chorus", "marimba", "melody", "bass", "logdrum"]),
    ("Couplet", 8, 0.80, ["chords_verse", "bass", "logdrum", "plucks"]),
    ("Refrain", 8, 1.05, ["chords_chorus", "marimba", "melody", "bass", "logdrum", "plucks"]),
    ("Pont",    8, 0.60, ["chords_dark", "pad", "bass"]),
    ("Refrain", 8, 1.10, ["chords_chorus", "marimba", "melody", "bass", "logdrum",
                          "logdrum_hi", "plucks"]),
    ("Outro",   8, 0.50, ["pad", "chords_verse"]),
]

# Le pont respire un peu plus lentement, le dernier refrain relance.
TEMPO_PLAN = {"Pont": 98.0, "Refrain": BPM}


def load_loop(path: Path, transpose: int, channel: int) -> tuple[list, float]:
    """Retourne (événements normalisés en beats, période en beats)."""
    md = parse_midi(path)
    events = []
    for n in md.notes:
        start = n.start / md.ppq
        dur = max(0.05, (n.end - n.start) / md.ppq)
        pitch = min(126, max(1, n.pitch + transpose))
        events.append((start, dur, pitch, n.velocity, channel))
    raw_beats = md.end_tick / md.ppq
    # période musicale = arrondi à la mesure supérieure (loops de 7.7-7.9 mes.)
    period = max(BEATS_PER_BAR, math.ceil(raw_beats / BEATS_PER_BAR) * BEATS_PER_BAR)
    return events, float(period)


def assemble(loop_dir: Path, out_path: Path) -> None:
    bank = {}
    for role, (fname, transpose, channel) in LOOPS.items():
        path = loop_dir / fname
        if not path.exists():
            raise SystemExit(f"loop manquant : {path}")
        bank[role] = load_loop(path, transpose, channel)

    tracks: dict[str, list] = {role: [] for role in LOOPS}
    markers: list[tuple[float, str]] = []
    tempo_changes: list[tuple[float, float]] = []
    current_tempo = BPM

    beat = 0.0
    for marker, bars, vel_scale, roles in PLAN:
        markers.append((beat, marker))
        wanted = TEMPO_PLAN.get(marker, BPM)
        if wanted != current_tempo:
            tempo_changes.append((beat, wanted))
            current_tempo = wanted
        section_beats = bars * BEATS_PER_BAR
        for role in roles:
            events, period = bank[role]
            reps = max(1, round(section_beats / period))
            for rep in range(reps):
                offset = beat + rep * period
                for start, dur, pitch, vel, channel in events:
                    if start >= section_beats / reps + period:  # garde-fou
                        continue
                    v = min(127, max(20, round(vel * vel_scale)))
                    tracks[role].append((offset + start, dur, pitch, v, channel))
        beat += section_beats

    write_midi(out_path, list(tracks.values()), ppq=480, bpm=BPM,
               markers=markers, tempo_changes=tempo_changes)
    total_notes = sum(len(t) for t in tracks.values())
    print(f"écrit : {out_path} — {int(beat / BEATS_PER_BAR)} mesures, "
          f"{total_notes} notes, {len(PLAN)} sections")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    assemble(Path(sys.argv[1]), Path(sys.argv[2]))
