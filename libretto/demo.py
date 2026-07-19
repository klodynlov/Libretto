"""
Libretto — partition de démonstration construite en mémoire.
Structure pop : intro / verse / chorus / verse / chorus / bridge / chorus / outro,
avec mélodie, harmonies, nuances et onsets — exerce les 29 axes sans MIDI.
"""

from __future__ import annotations

from .model import Accidentals, Chord, NoteType, Pitch, Score, Section

_N = NoteType
_A = Accidentals


def _p(note: NoteType, octave: int = 4, acc: Accidentals = _A.NATURAL) -> Pitch:
    return Pitch(note, acc, octave)


def _maj(note: NoteType, octave: int = 3) -> Chord:
    return Chord(_p(note, octave), [0, 4, 7], "maj")


def _min(note: NoteType, octave: int = 3) -> Chord:
    return Chord(_p(note, octave), [0, 3, 7], "min")


def _phrase(*specs: tuple[NoteType, int]) -> list[Pitch]:
    return [_p(n, o) for n, o in specs]


def demo_score() -> Score:
    verse_melody = _phrase(
        (_N.E, 4), (_N.G, 4), (_N.A, 4), (_N.G, 4), (_N.E, 4), (_N.D, 4), (_N.C, 4), (_N.D, 4),
        (_N.E, 4), (_N.G, 4), (_N.A, 4), (_N.G, 4), (_N.E, 4), (_N.D, 4), (_N.C, 4), (_N.C, 4),
    )
    chorus_melody = _phrase(
        (_N.G, 4), (_N.C, 5), (_N.B, 4), (_N.A, 4), (_N.G, 4), (_N.A, 4), (_N.B, 4), (_N.C, 5),
        (_N.G, 4), (_N.C, 5), (_N.B, 4), (_N.A, 4), (_N.G, 4), (_N.E, 4), (_N.D, 4), (_N.C, 4),
    )
    bridge_melody = _phrase(
        (_N.A, 4), (_N.B, 4), (_N.C, 5), (_N.B, 4), (_N.A, 4), (_N.G, 4), (_N.F, 4), (_N.E, 4),
    )
    verse_harm = [_maj(_N.C), _min(_N.A), _maj(_N.F), _maj(_N.G)] * 2
    chorus_harm = [_maj(_N.F), _maj(_N.G), _maj(_N.C), _min(_N.A),
                   _maj(_N.F), _maj(_N.G), _maj(_N.C), _maj(_N.C)]
    bridge_harm = [_min(_N.A), _maj(_N.F), _min(_N.D), _maj(_N.G)]

    def onsets(start_beat: int, n_beats: int, syncopated: bool) -> list[float]:
        out = []
        for beat in range(start_beat, start_beat + n_beats):
            out.append(float(beat))
            if syncopated:
                out.append(beat + 0.5)
        return out

    sections = [
        Section("intro", 1, 5, "intro",
                harmony=[_maj(_N.C), _maj(_N.C), _maj(_N.F), _maj(_N.G)],
                melody_pitches=_phrase((_N.E, 4), (_N.D, 4), (_N.C, 4), (_N.D, 4)),
                tempo=100, mean_velocity=52, avg_polyphony=1.6, note_density=3.0,
                onset_beats=onsets(0, 8, False)),
        Section("verse1", 5, 13, "verse", harmony=list(verse_harm),
                melody_pitches=list(verse_melody),
                tempo=100, mean_velocity=68, avg_polyphony=2.4, note_density=6.0,
                onset_beats=onsets(16, 24, False)),
        Section("chorus1", 13, 21, "chorus", harmony=list(chorus_harm),
                melody_pitches=list(chorus_melody),
                tempo=100, mean_velocity=92, avg_polyphony=3.6, note_density=9.0,
                onset_beats=onsets(48, 28, True)),
        Section("verse2", 21, 29, "verse", harmony=list(verse_harm),
                melody_pitches=list(verse_melody),
                tempo=100, mean_velocity=72, avg_polyphony=2.6, note_density=6.5,
                onset_beats=onsets(80, 24, False)),
        Section("chorus2", 29, 37, "chorus", harmony=list(chorus_harm),
                melody_pitches=list(chorus_melody),
                tempo=100, mean_velocity=95, avg_polyphony=3.8, note_density=9.5,
                onset_beats=onsets(112, 28, True)),
        Section("bridge", 37, 41, "bridge", harmony=list(bridge_harm),
                melody_pitches=list(bridge_melody),
                tempo=92, mean_velocity=60, avg_polyphony=2.0, note_density=4.5,
                onset_beats=onsets(144, 12, False)),
        Section("chorus3", 41, 49, "chorus", harmony=list(chorus_harm),
                melody_pitches=list(chorus_melody),
                tempo=100, mean_velocity=104, avg_polyphony=4.2, note_density=10.0,
                onset_beats=onsets(160, 28, True)),
        Section("outro", 49, 53, "outro",
                harmony=[_maj(_N.F), _maj(_N.G), _maj(_N.C), _maj(_N.C)],
                melody_pitches=_phrase((_N.E, 4), (_N.D, 4), (_N.C, 4)),
                tempo=94, mean_velocity=48, avg_polyphony=1.8, note_density=3.0,
                onset_beats=onsets(192, 8, False)),
    ]
    return Score(
        sections=sections,
        key_signature=Pitch(_N.C),
        tempo_map=[(1, 100), (37, 92), (41, 100), (49, 94)],
        dynamics=[(1, "p"), (5, "mp"), (13, "f"), (21, "mp"), (29, "f"),
                  (37, "mp"), (41, "ff"), (49, "p")],
        texture_map=[(1, "2v homophonic"), (13, "4v polyphonic"), (37, "2v homophonic"),
                     (41, "4v polyphonic"), (49, "monophonic solo")],
    )
