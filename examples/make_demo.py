"""
Génère examples/demo.mid : chanson pop de 40 mesures en Do majeur avec
marqueurs de sections en français (Intro/Couplet/Refrain/Pont/Outro),
piano (accords), mélodie, basse et batterie syncopée.

Usage : python3 examples/make_demo.py [chemin_sortie]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import write_midi  # noqa: E402

# Accords (fondamentale MIDI, tierce majeure ?) par degré
C, Dm, Em, F, G, Am = (60, "maj"), (62, "min"), (64, "min"), (65, "maj"), (67, "maj"), (69, "min")

VERSE = [C, Am, F, G]
CHORUS = [F, G, C, Am]
BRIDGE = [Am, F, Dm, G]

VERSE_MELODY = [64, 67, 69, 67, 64, 62, 60, 62]     # 2 notes/mesure
CHORUS_MELODY = [67, 72, 71, 69, 67, 69, 71, 72]
BRIDGE_MELODY = [69, 71, 72, 71, 69, 67, 65, 64]

PLAN = [
    ("Intro", VERSE[:1] * 4, VERSE_MELODY[:4] * 2, 45, False),
    ("Couplet", VERSE * 2, VERSE_MELODY * 2, 66, False),
    ("Refrain", CHORUS * 2, CHORUS_MELODY * 2, 96, True),
    ("Couplet", VERSE * 2, VERSE_MELODY * 2, 70, False),
    ("Refrain", CHORUS * 2, CHORUS_MELODY * 2, 100, True),
    ("Pont", BRIDGE, BRIDGE_MELODY, 58, False),
    ("Refrain", CHORUS * 2, CHORUS_MELODY * 2, 108, True),
    ("Outro", VERSE[:1] * 4, [60, 62, 64, 62, 60, 60, 60, 60], 42, False),
]

TRIADS = {"maj": (0, 4, 7), "min": (0, 3, 7)}


def build(path: str | Path) -> None:
    piano, melody, bass, drums = [], [], [], []
    markers = []
    beat = 0.0
    for name, chords, mel, vel, syncopated in PLAN:
        markers.append((beat, name))
        section_start = beat
        for root, quality in chords:  # 1 accord / mesure (4 temps)
            for iv in TRIADS[quality]:
                piano.append((beat, 3.6, root + iv, vel, 0))
            bass.append((beat, 2.0, root - 24, min(127, vel + 8), 1))
            if syncopated:
                bass.append((beat + 2.5, 1.5, root - 24, vel, 1))
            else:
                bass.append((beat + 2.0, 2.0, root - 12, vel, 1))
            beat += 4.0
        n_beats = beat - section_start
        for k, pitch in enumerate(mel):
            dur = n_beats / len(mel)
            melody.append((section_start + k * dur, dur * 0.9, pitch + 12,
                           min(127, vel + 14), 2))
        b = section_start
        while b < beat:
            drums.append((b, 0.4, 36, vel, 9))            # kick sur le temps
            drums.append((b + 2.0, 0.4, 38, vel, 9))       # snare sur 3
            if syncopated:
                drums.append((b + 0.5, 0.2, 42, max(30, vel - 20), 9))
                drums.append((b + 1.5, 0.2, 42, max(30, vel - 20), 9))
                drums.append((b + 2.5, 0.2, 42, max(30, vel - 20), 9))
                drums.append((b + 3.5, 0.2, 42, max(30, vel - 20), 9))
            b += 4.0
    write_midi(path, [piano, melody, bass, drums], ppq=480, bpm=100,
               markers=markers,
               tempo_changes=[(markers[5][0], 92.0), (markers[6][0], 100.0)])
    print(f"écrit : {path} ({int(beat / 4)} mesures)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "demo.mid")
    build(out)
