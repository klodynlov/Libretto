"""Descriptive GM drum timing, separate from the 29 structural scores.

Offsets are measured against a sixteenth-note reference, never written back
to MIDI. A swung offbeat can therefore contribute to these offsets: they
describe timing, not mistakes or musical quality.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from statistics import median

from .builder import _bar_starts, meter_context
from .midi import MidiData, tick_to_seconds

VOICES = {
    "kick": ("Grosse caisse", {35, 36}),
    "snare": ("Caisse claire", {37, 38, 39, 40}),
    "hihat": ("Charley", {42, 44, 46}),
    "cymbal": ("Cymbales", {49, 51, 52, 53, 55, 57, 59}),
    "tom": ("Toms", {41, 43, 45, 47, 48, 50}),
}


def _voice(pitch: int) -> str:
    return next((key for key, (_, pitches) in VOICES.items()
                 if pitch in pitches), "other")


def _timing(offsets: list[float]) -> dict:
    center = median(offsets)
    return {"median_offset_ms": round(center, 2),
            "spread_ms": round(median(abs(x - center) for x in offsets), 2)}


def analyze_groove(md: MidiData) -> dict:
    """Describe channel 10 (zero-based 9) only; never infer drums by pitch.

    Each meter has its own folded sixteenth grid. Swing is estimated from
    consecutive two-hit hi-hat pulses in simple meter only, with at least
    four observed pairs. Missing evidence yields None, never a zero score.
    """
    drums = sorted((n for n in md.notes if n.channel == 9), key=lambda n: n.start)
    base = {"available": bool(drums), "drum_notes": len(drums),
            "reference": "double-croche", "voices": [], "swing": None,
            "notice": "Profil descriptif, sans note de qualité. Les écarts incluent "
                      "le swing et ne signalent pas des erreurs de jeu."}
    if not drums:
        return {**base, "reason": "Aucune percussion sur le canal MIDI 10 (GM)."}
    starts = _bar_starts(md)
    sigs = sorted(md.time_sigs)
    if not sigs or sigs[0][0] > 0:
        sigs.insert(0, (0, 4, 4))
    sig_ticks = [s[0] for s in sigs]
    offsets = defaultdict(list)
    velocities = defaultdict(list)
    grids = {}
    pulses = defaultdict(list)
    all_offsets = []
    for note in drums:
        si = max(0, bisect_right(sig_ticks, note.start) - 1)
        origin, num, den = sigs[si]
        step = md.ppq / 4
        pos = (note.start - origin) / step
        snapped = origin + math.floor(pos + 0.5) * step
        offset = 1000 * (tick_to_seconds(md, note.start)
                         - tick_to_seconds(md, snapped))
        key = _voice(note.pitch)
        offsets[key].append(offset)
        all_offsets.append(offset)
        velocities[key].append(note.velocity)
        meter = f"{num}/{den}"
        slots = max(1, round(num * 16 / den))
        grid = grids.setdefault((key, meter), [0] * slots)
        b = max(0, bisect_right(starts, note.start) - 1)
        slot = math.floor((note.start - starts[b]) / step + 0.5) % slots
        grid[slot] += 1
        pulse_beats, subdivision = meter_context(num, den)
        if key == "hihat" and subdivision == 2:
            pulse_ticks = md.ppq * pulse_beats
            pulse = math.floor((note.start - origin) / pulse_ticks + 0.08)
            pulses[(si, pulse)].append((note.start - origin) / pulse_ticks)
    ratios = []
    for (si, beat), hits in pulses.items():
        following = pulses.get((si, beat + 1), [])
        if len(hits) != 2 or not following:
            continue
        on, off = sorted(hits)
        end = min(following)
        if abs(on - beat) > 0.08 or abs(end - beat - 1) > 0.08:
            continue
        phase = (off - on) / (end - on)
        if 0.35 <= phase <= 0.8:
            ratios.append(phase)
    if len(ratios) >= 4:
        phase = median(ratios)
        base["swing"] = {"long_percent": round(100 * phase, 1),
                         "long_short_ratio": round(phase / (1 - phase), 2),
                         "pairs": len(ratios),
                         "label": "droit" if 0.45 <= phase <= 0.55 else "inégal"}
    else:
        base["swing_reason"] = "Pas assez de paires de charley en mesure simple."
    counts = Counter(_voice(n.pitch) for n in drums)
    for key in (*VOICES, "other"):
        if not counts[key]:
            continue
        base["voices"].append({
            "id": key, "name": VOICES[key][0] if key in VOICES else "Autres percussions",
            "notes": counts[key], **_timing(offsets[key]),
            "velocity_min": min(velocities[key]), "velocity_max": max(velocities[key]),
            "velocity_median": median(velocities[key]),
            "grids": [{"meter": meter, "hits": hits}
                      for (voice, meter), hits in grids.items() if voice == key],
        })
    return {**base, "bars": len(starts), **_timing(all_offsets)}


def render_groove_text(report: dict) -> str:
    if not report["available"]:
        return "GROOVE : " + report["reason"]
    lines = [f"GROOVE · {report['drum_notes']} frappes · {report['bars']} mesures",
             "Référence : double-croche ; décalage positif = en retard."]
    swing = report["swing"]
    lines.append(f"Swing : {swing['long_percent']} % ({swing['pairs']} paires)"
                 if swing else "Swing : " + report["swing_reason"])
    for voice in report["voices"]:
        lines.append(f"  {voice['name']} : {voice['median_offset_ms']:+.2f} ms, "
                     f"dispersion médiane {voice['spread_ms']:.2f} ms, "
                     f"vélocités {voice['velocity_min']}–{voice['velocity_max']}")
    lines.append(report["notice"])
    return "\n".join(lines)
