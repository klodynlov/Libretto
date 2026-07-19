"""
Assemble un morceau complet à partir du pack « Naija Waves » (afrobeats).

Famille Bonfire (Ré mineur, 100 BPM) : basse, piano, guitares, brass, sax,
percussions. L'accordéon Moody (Si mineur) est transposé +3 vers Ré mineur
pour colorer le pont. Toutes les percussions (notes déclencheur pitch 60-65)
vont sur le canal 9 : Libretto les exclut du chroma et de la mélodie.

Usage : python3 examples/assemble_naija.py "<dossier MIDI FILES>" <sortie.mid>
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import parse_midi, write_midi  # noqa: E402

BPM = 100.0
BEATS_PER_BAR = 4
DRUMS = 9

# rôle -> (fichier, transposition demi-tons, canal MIDI)
LOOPS = {
    "piano":     ("Bonfire_Dm_100_Piano_Chords.mid",     0, 0),
    "gchords":   ("Bonfire_Dm_100_Guitar_Chords.mid",    0, 1),
    "plucks":    ("Bonfire_Dm_100_Plucks.mid",           0, 2),
    "bass":      ("Bonfire_Dm_100_Bassline.mid",         0, 3),
    "brass":     ("Bonfire_Dm_100_Brass.mid",            0, 4),
    "sax":       ("Bonfire_Dm_100_Saxophone_01.mid",     0, 5),
    "sax_solo":  ("Bonfire_Dm_100_Saxophone_02.mid",     0, 6),
    "gmel":      ("Bonfire_Dm_100_Guitar_Melody.mid",    0, 7),
    "gmel_echo": ("Bonfire_Dm_100_Guitar_Melody_02.mid", -12, 8),  # registre recentré
    "accordion": ("Moody_90_Bm_Accordion_02.mid",        3, 10),  # Si min -> Ré min
    # percussions : canal 9 (exclues de l'analyse harmonique/mélodique)
    "kick":      ("Bonfire_Dm_100_Kick_01.mid",  0, DRUMS),
    "snare":     ("Bonfire_Dm_100_Snare_01.mid", 0, DRUMS),
    "snare_alt": ("Bonfire_Dm_100_Snare_02.mid", 0, DRUMS),
    "hats":      ("Bonfire_Dm_100_Hats_01.mid",  0, DRUMS),
    "hats_open": ("Bonfire_Dm_100_Hats_02.mid",  0, DRUMS),
    "bongo":     ("Bonfire_Dm_100_Bongo.mid",    0, DRUMS),
    "conga":     ("Bonfire_Dm_100_Conga.mid",    0, DRUMS),
    "perc":      ("Bonfire_Dm_100_Perc_02.mid",  0, DRUMS),
    "perc_alt":  ("Bonfire_Dm_100_Perc_04.mid",  0, DRUMS),
}

# (marqueur, mesures, vélocité, rôles actifs) — arche énergétique, pic aux
# 2/3 (refrain 2), gamme de vélocités resserrée (l'axe 26 pénalise > 45).
PLAN = [
    ("Intro",   8, 0.62, ["piano", "plucks", "bongo", "conga"]),
    ("Couplet", 8, 0.78, ["piano", "gchords", "bass", "kick", "hats", "snare_alt",
                          "bongo"]),
    ("Refrain", 8, 0.95, ["piano", "gchords", "bass", "brass", "sax", "kick",
                          "snare", "hats", "hats_open", "conga", "perc"]),
    ("Couplet", 8, 0.82, ["piano", "gmel", "bass", "kick", "hats", "snare_alt",
                          "perc_alt", "plucks"]),
    ("Refrain", 8, 1.05, ["piano", "gchords", "bass", "brass", "sax", "gmel_echo",
                          "kick", "snare", "hats", "hats_open", "conga", "perc"]),
    ("Pont",    8, 0.65, ["accordion", "piano", "plucks", "conga", "bongo"]),
    ("Refrain", 8, 0.98, ["piano", "bass", "brass", "sax_solo", "gmel_echo",
                          "kick", "snare", "hats", "hats_open", "conga", "perc"]),
    ("Outro",   8, 0.60, ["piano", "plucks", "bongo"]),
]

# Le pont respire, les refrains restent au tempo nominal.
TEMPO_PLAN = {"Pont": 94.0, "Refrain": BPM}


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
                    if offset + start >= beat + section_beats:  # pas de débord
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
