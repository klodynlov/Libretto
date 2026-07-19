"""
Libretto — builder : MidiData → Score symbolique.

Pipeline : grille de mesures (signatures) → chroma/densité/vélocité par
mesure → accord par mesure (gabarits maj/min/dim/7) → mélodie (voix
supérieure échantillonnée par temps) → sections (marqueurs MIDI si présents,
sinon segmentation par nouveauté sur le chroma) → étiquetage heuristique
(intro/verse/chorus/bridge/outro) → Score complet.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter

from .midi import MidiData, MidiNote
from .model import Chord, Score, Section, pitch_from_midi

DRUM_CHANNEL = 9

CHORD_TEMPLATES: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7),
    "min": (0, 3, 7),
    "dim": (0, 3, 6),
    "dom7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
}

# Normalisation des labels de marqueurs (anglais + français).
MARKER_LABELS = {
    "intro": "intro", "prelude": "intro",
    "verse": "verse", "couplet": "verse",
    "chorus": "chorus", "refrain": "chorus",
    "bridge": "bridge", "pont": "bridge",
    "outro": "outro", "coda": "outro", "fin": "outro",
    "solo": "solo", "interlude": "interlude", "build": "build",
}

DYN_BANDS = [(32, "pp"), (48, "p"), (64, "mp"), (80, "mf"), (96, "f"), (112, "ff"), (128, "fff")]


def _dyn_label(mean_vel: float) -> str:
    for limit, label in DYN_BANDS:
        if mean_vel < limit:
            return label
    return "fff"


def _texture_label(avg_poly: float) -> str:
    if avg_poly < 1.2:
        return "monophonic solo"
    if avg_poly < 2.5:
        return "2v homophonic"
    if avg_poly < 3.5:
        return "3v"
    return "4v polyphonic"


def _bar_starts(md: MidiData) -> list[int]:
    """Ticks de début de chaque mesure, en suivant les changements de
    signature."""
    sigs = sorted(md.time_sigs) or [(0, 4, 4)]
    if sigs[0][0] > 0:
        sigs.insert(0, (0, 4, 4))
    end = max(md.end_tick, 1)
    starts: list[int] = []
    tick = 0
    for idx, (sig_tick, num, den) in enumerate(sigs):
        seg_end = sigs[idx + 1][0] if idx + 1 < len(sigs) else end
        ticks_per_bar = max(1, round(md.ppq * 4 * num / den))
        tick = max(tick, sig_tick)
        while tick < seg_end:
            starts.append(tick)
            tick += ticks_per_bar
    return starts or [0]


def _cosine_dist(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu <= 0 or nv <= 0:
        return 1.0 if (nu > 0) != (nv > 0) else 0.0
    return 1.0 - dot / (nu * nv)


def _best_chord(weights: list[float]) -> Chord | None:
    total = sum(weights)
    if total <= 1e-9 or sum(1 for w in weights if w > 0) < 2:
        return None
    best: tuple[float, int, str] | None = None
    for quality, template in CHORD_TEMPLATES.items():
        for root in range(12):
            pcs = {(root + iv) % 12 for iv in template}
            inside = sum(weights[pc] for pc in pcs)
            outside = total - inside
            score = inside - 0.7 * outside + 0.5 * weights[root]
            if best is None or score > best[0]:
                best = (score, root, quality)
    _score, root, quality = best
    return Chord(pitch_from_midi(48 + root), list(CHORD_TEMPLATES[quality]), quality)


def _detect_boundaries(features: list[list[float]], window: int = 4, min_len: int = 4) -> list[int]:
    """Frontières de sections par nouveauté : distance cosinus entre les
    moyennes des fenêtres avant/après chaque mesure."""
    n = len(features)
    if n <= min_len:
        return [0]
    novelty = [0.0] * n
    for b in range(1, n):
        left = features[max(0, b - window):b]
        right = features[b:min(n, b + window)]
        mean_l = [sum(col) / len(left) for col in zip(*left)]
        mean_r = [sum(col) / len(right) for col in zip(*right)]
        novelty[b] = _cosine_dist(mean_l, mean_r)
    mean_nov = sum(novelty) / n
    std_nov = math.sqrt(sum((x - mean_nov) ** 2 for x in novelty) / n)
    threshold = mean_nov + 0.5 * std_nov
    boundaries = [0]
    for b in range(1, n):
        if novelty[b] >= threshold and novelty[b] == max(novelty[max(0, b - 2):b + 3]):
            if b - boundaries[-1] >= min_len:
                boundaries.append(b)
    return boundaries


def _assign_letters(section_feats: list[list[float]], sim_threshold: float = 0.82) -> list[str]:
    letters: list[str] = []
    reps: list[tuple[str, list[float]]] = []
    for feat in section_feats:
        assigned = None
        for letter, rep in reps:
            if 1.0 - _cosine_dist(feat, rep) >= sim_threshold:
                assigned = letter
                break
        if assigned is None:
            assigned = chr(ord("A") + len(reps))
            reps.append((assigned, feat))
        letters.append(assigned)
    return letters


def _name_sections(letters: list[str], energies: list[float]) -> list[str]:
    """Heuristique de nommage à partir des lettres de clusters et de
    l'énergie mesurée. Fallback : la lettre elle-même."""
    n = len(letters)
    labels = list(letters)
    if n < 2:
        return labels
    counts = Counter(letters)
    sorted_e = sorted(energies)
    median_e = sorted_e[n // 2]
    mean_energy = {
        letter: sum(e for lt, e in zip(letters, energies) if lt == letter) / c
        for letter, c in counts.items()
    }
    repeated = [lt for lt, c in counts.items() if c >= 2]
    chorus = max(repeated, key=lambda lt: mean_energy[lt]) if repeated else None
    verse = None
    others = [lt for lt in repeated if lt != chorus]
    if others:
        verse = max(others, key=lambda lt: counts[lt])
    for i, letter in enumerate(letters):
        if letter == chorus:
            labels[i] = "chorus"
        elif letter == verse:
            labels[i] = "verse"
        elif counts[letter] == 1 and 0 < i < n - 1:
            labels[i] = "bridge"
    if counts[letters[0]] == 1 and energies[0] <= median_e:
        labels[0] = "intro"
    if counts[letters[-1]] == 1 and energies[-1] <= median_e:
        labels[-1] = "outro"
    return labels


def build_score(md: MidiData) -> Score:
    notes = [n for n in md.notes if n.channel != DRUM_CHANNEL]
    all_notes = md.notes
    if not all_notes:
        return Score()

    ppq = md.ppq
    bar_starts = _bar_starts(md)
    n_bars = len(bar_starts)

    def bar_of(tick: int) -> int:
        return min(n_bars - 1, max(0, bisect_right(bar_starts, tick) - 1))

    # ── features par mesure ──
    chroma = [[0.0] * 12 for _ in range(n_bars)]
    density = [0] * n_bars
    vel_sum = [0.0] * n_bars
    vel_count = [0] * n_bars
    pitch_sum = [0.0] * n_bars
    for n in notes:
        b = bar_of(n.start)
        dur = max(1, n.end - n.start)
        chroma[b][n.pitch % 12] += dur * n.velocity / 127.0
        pitch_sum[b] += n.pitch
    for n in all_notes:
        b = bar_of(n.start)
        density[b] += 1
        vel_sum[b] += n.velocity
        vel_count[b] += 1

    # ── accord par mesure ──
    chords_by_bar: list[Chord | None] = [_best_chord(chroma[b]) for b in range(n_bars)]

    # ── mélodie : voix supérieure échantillonnée par temps ──
    total_beats = max(1, math.ceil(md.end_tick / ppq))
    melody_samples: list[tuple[int, MidiNote]] = []  # (beat, note)
    last_note: MidiNote | None = None
    for beat in range(total_beats):
        tick = beat * ppq
        sounding = [n for n in notes if n.start <= tick < n.end]
        if not sounding:
            continue
        top = max(sounding, key=lambda n: n.pitch)
        if top is not last_note:
            melody_samples.append((beat, top))
            last_note = top

    # ── polyphonie par temps ──
    poly_by_beat: list[int] = []
    for beat in range(total_beats):
        tick = beat * ppq
        poly_by_beat.append(len({n.pitch for n in notes if n.start <= tick < n.end}))

    # ── frontières de sections ──
    max_den = max(density) or 1
    features = []
    for b in range(n_bars):
        norm = math.sqrt(sum(x * x for x in chroma[b])) or 1.0
        feat = [x / norm for x in chroma[b]]
        feat.append(density[b] / max_den)
        feat.append((pitch_sum[b] / density[b] / 127.0) if density[b] else 0.0)
        features.append(feat)

    marker_bars = sorted({bar_of(t) for t, _ in md.markers})
    if len(marker_bars) >= 2:
        boundaries = sorted(set([0] + marker_bars))
        marker_by_bar = {bar_of(t): text for t, text in md.markers}
        raw_labels = []
        for b in boundaries:
            text = marker_by_bar.get(b, "").strip().lower()
            key = next((v for k, v in MARKER_LABELS.items() if k in text), None)
            raw_labels.append(key or text or "section")
    else:
        boundaries = _detect_boundaries(features)
        raw_labels = None

    boundaries = sorted(set(boundaries))
    edges = boundaries + [n_bars]

    # ── construction des sections ──
    tempo_events = sorted(md.tempos) or [(0, 120.0)]

    def tempo_at(tick: int) -> float:
        current = tempo_events[0][1]
        for t, bpm in tempo_events:
            if t <= tick:
                current = bpm
            else:
                break
        return current

    sections: list[Section] = []
    section_feats: list[list[float]] = []
    energies: list[float] = []
    max_vel_piece = max((n.velocity for n in all_notes), default=1)
    for idx in range(len(edges) - 1):
        b0, b1 = edges[idx], edges[idx + 1]
        if b1 <= b0:
            continue
        start_tick = bar_starts[b0]
        end_tick = bar_starts[b1] if b1 < n_bars else md.end_tick + 1
        sec_notes = [n for n in all_notes if start_tick <= n.start < end_tick]
        harmony = [chords_by_bar[b] for b in range(b0, b1) if chords_by_bar[b] is not None]
        beat0 = math.ceil(start_tick / ppq)
        beat1 = math.ceil(end_tick / ppq)
        melody = [pitch_from_midi(note.pitch)
                  for beat, note in melody_samples if beat0 <= beat < beat1]
        polys = [p for p in poly_by_beat[beat0:min(beat1, len(poly_by_beat))] if p > 0]
        mean_vel = (sum(n.velocity for n in sec_notes) / len(sec_notes)) if sec_notes else 0.0
        n_sec_bars = b1 - b0
        section = Section(
            id=f"s{idx:02d}",
            start_bar=b0 + 1,
            end_bar=b1 + 1,
            label="section",
            harmony=harmony,
            melody_pitches=melody,
            tempo=round(tempo_at(start_tick)),
            mean_velocity=round(mean_vel, 2),
            avg_polyphony=round(sum(polys) / len(polys), 2) if polys else 0.0,
            note_density=round(len(sec_notes) / n_sec_bars, 2) if n_sec_bars else 0.0,
            onset_beats=[round(n.start / ppq, 4) for n in sec_notes if n.channel != DRUM_CHANNEL]
                        or [round(n.start / ppq, 4) for n in sec_notes],
        )
        sections.append(section)
        feats = [features[b] for b in range(b0, b1)]
        section_feats.append([sum(col) / len(feats) for col in zip(*feats)])
        energies.append(0.6 * mean_vel / max_vel_piece
                        + 0.4 * min(1.0, (len(sec_notes) / max(1, n_sec_bars)) / 12.0))

    if not sections:
        return Score()

    # ── labels ──
    letters = _assign_letters(section_feats)
    if raw_labels is not None:
        for section, label in zip(sections, raw_labels):
            section.label = label
    else:
        for section, label in zip(sections, _name_sections(letters, energies)):
            section.label = label
    for section, letter in zip(sections, letters):
        section.id = f"{letter}_{section.id}"

    # ── cartes globales ──
    dynamics = [(s.start_bar, _dyn_label(s.mean_velocity)) for s in sections if s.mean_velocity > 0]
    texture_map = [(s.start_bar, _texture_label(s.avg_polyphony)) for s in sections
                   if s.avg_polyphony > 0]
    tempo_map = []
    seen_bars = set()
    for tick, bpm in tempo_events:
        b = bar_of(tick) + 1
        if b not in seen_bars:
            tempo_map.append((b, round(bpm)))
            seen_bars.add(b)

    first_sig = (sorted(md.time_sigs) or [(0, 4, 4)])[0]

    # tonalité globale estimée (informatif)
    from .axes import estimate_key  # import tardif : évite un cycle
    global_hist = [0.0] * 12
    for row in chroma:
        for pc in range(12):
            global_hist[pc] += row[pc]
    key_root, _mode, _corr, _margin = estimate_key(global_hist)

    return Score(
        sections=sections,
        key_signature=pitch_from_midi(60 + key_root),
        time_signature_num=first_sig[1],
        time_signature_den=first_sig[2],
        tempo_map=tempo_map,
        dynamics=dynamics,
        texture_map=texture_map,
    )
