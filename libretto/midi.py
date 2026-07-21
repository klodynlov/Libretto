"""
Libretto — lecture/écriture MIDI (Standard MIDI File), 100 % stdlib.

Lecture : formats 0 et 1, division PPQ (SMPTE rejeté), running status,
note-on vélocité 0 traité comme note-off, meta tempo / signature / marqueurs.
Écriture : minimale, suffisante pour les tests et les fichiers d'exemple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MidiNote:
    start: int      # ticks
    end: int        # ticks
    pitch: int      # 0-127
    velocity: int   # 1-127
    channel: int    # 0-15 (9 = percussions GM)
    track: int


@dataclass
class MidiData:
    ppq: int
    notes: list[MidiNote] = field(default_factory=list)
    tempos: list[tuple[int, float]] = field(default_factory=list)      # (tick, bpm)
    time_sigs: list[tuple[int, int, int]] = field(default_factory=list)  # (tick, num, den)
    markers: list[tuple[int, str]] = field(default_factory=list)       # (tick, texte)
    # (tick, canal, contrôleur, valeur). Capturé pour le corpus
    # d'interprétations réelles : une performance de piano vit sur la pédale
    # (CC64), et la perdre d'un seul côté d'une paire A/B ferait entendre la
    # pédale au lieu de la dynamique. Les dégradations n'y touchent jamais.
    controls: list[tuple[int, int, int, int]] = field(default_factory=list)
    end_tick: int = 0


def _read_varlen(data: bytes, i: int) -> tuple[int, int]:
    # Sur une entrée tronquée, data[i] lève IndexError : c'est parse_midi_bytes
    # qui le convertit en ValueError (voir le contrat public plus bas).
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def parse_midi(path: str | Path) -> MidiData:
    return parse_midi_bytes(Path(path).read_bytes(), origin=str(path))


def parse_midi_bytes(data: bytes, origin: str = "<bytes>") -> MidiData:
    """Analyse un SMF en mémoire.

    Contrat public : renvoie un MidiData, ou lève ValueError si l'entrée n'est
    pas exploitable — jamais IndexError ni aucune autre exception brute. Des
    octets tronqués ou corrompus provoquent des accès hors bornes un peu
    partout dans le parseur ; plutôt que de contrôler chaque lecture (coûteux
    sur le chemin nominal), on englobe le parsing d'un unique try/except.
    """
    try:
        return _parse_midi_bytes(data, origin)
    except ValueError:
        raise  # erreurs de format déjà formulées, on les laisse intactes
    except IndexError as exc:
        # Seul IndexError est reconverti : c'est l'unique famille que produit
        # une entrée tronquée (confirmé par le fuzz — 209 cas sur 800, tous
        # des accès hors bornes). Attraper `Exception` masquerait un bug de
        # programmation du parseur en « fichier corrompu », et ferait passer
        # une régression pour une donnée invalide.
        raise ValueError(
            f"{origin}: fichier MIDI corrompu ou tronqué "
            f"(accès hors bornes : {exc})"
        ) from exc


def _parse_midi_bytes(data: bytes, origin: str) -> MidiData:
    path = origin
    if data[:4] != b"MThd":
        raise ValueError(f"{path}: pas un fichier MIDI (en-tête MThd absent)")
    header_len = int.from_bytes(data[4:8], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"{path}: division SMPTE non supportée")
    n_tracks = int.from_bytes(data[10:12], "big")

    md = MidiData(ppq=division)
    i = 8 + header_len
    for track_idx in range(n_tracks):
        if data[i:i + 4] != b"MTrk":
            raise ValueError(f"{path}: chunk MTrk attendu à l'offset {i}")
        track_len = int.from_bytes(data[i + 4:i + 8], "big")
        j = i + 8
        track_end = j + track_len
        tick = 0
        running: int | None = None
        active: dict[tuple[int, int], list[tuple[int, int]]] = {}

        while j < track_end:
            delta, j = _read_varlen(data, j)
            tick += delta
            status = data[j]
            if status & 0x80:
                j += 1
            else:
                if running is None:
                    raise ValueError(f"{path}: running status sans statut précédent")
                status = running

            if status == 0xFF:  # meta
                meta_type = data[j]
                j += 1
                length, j = _read_varlen(data, j)
                payload = data[j:j + length]
                j += length
                running = None
                if meta_type == 0x51 and length == 3:
                    us_per_qn = int.from_bytes(payload, "big")
                    if us_per_qn > 0:
                        md.tempos.append((tick, 60_000_000 / us_per_qn))
                elif meta_type == 0x58 and length >= 2:
                    # L'exposant du dénominateur est borné : un octet corrompu
                    # donnerait 2**255, d'où une mesure de durée nulle, donc
                    # une grille de millions de mesures vides en aval (le
                    # builder divise la pièce par la durée de mesure). On
                    # ignore la signature aberrante plutôt que de propager
                    # une bombe de calcul. 2**10 = 1024 couvre tout SMF réel.
                    if payload[0] > 0 and payload[1] <= 10:
                        md.time_sigs.append((tick, payload[0], 2 ** payload[1]))
                elif meta_type == 0x06:
                    md.markers.append((tick, payload.decode("latin-1", errors="replace")))
                elif meta_type == 0x2F:
                    break
            elif status in (0xF0, 0xF7):  # sysex
                length, j = _read_varlen(data, j)
                j += length
                running = None
            else:
                running = status
                kind = status & 0xF0
                channel = status & 0x0F
                if kind in (0xC0, 0xD0):
                    j += 1
                else:
                    d1, d2 = data[j], data[j + 1]
                    j += 2
                    if kind == 0x90 and d2 > 0:
                        active.setdefault((channel, d1), []).append((tick, d2))
                    elif kind == 0xB0:
                        md.controls.append((tick, channel, d1, d2))
                    elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                        stack = active.get((channel, d1))
                        if stack:
                            start, vel = stack.pop()
                            if tick > start:
                                md.notes.append(MidiNote(start, tick, d1, vel, channel, track_idx))

        # Notes jamais refermées : on les clôt au dernier tick de la piste.
        for (channel, pitch), stack in active.items():
            for start, vel in stack:
                if tick > start:
                    md.notes.append(MidiNote(start, tick, pitch, vel, channel, track_idx))
        md.end_tick = max(md.end_tick, tick)
        i = i + 8 + track_len

    md.notes.sort(key=lambda n: (n.start, n.pitch))
    md.tempos.sort()
    md.time_sigs.sort()
    md.markers.sort()
    md.controls.sort()
    if md.notes:
        md.end_tick = max(md.end_tick, max(n.end for n in md.notes))
    return md


# ──────────────────────────────────────────────
# Écriture
# ──────────────────────────────────────────────

def _varlen(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _track_chunk(events: list[tuple[int, int, bytes]]) -> bytes:
    """events = liste (tick, ordre_tri, octets_evenement), tri interne."""
    events = sorted(events, key=lambda e: (e[0], e[1]))
    payload = bytearray()
    last_tick = 0
    for tick, _order, raw in events:
        payload += _varlen(tick - last_tick) + raw
        last_tick = tick
    payload += _varlen(0) + bytes([0xFF, 0x2F, 0x00])
    return b"MTrk" + len(payload).to_bytes(4, "big") + bytes(payload)


def write_mididata(path: str | Path, md: MidiData,
                   programs: dict[int, int] | None = None) -> None:
    """Écrit un MidiData complet (notes, tempos, signatures, marqueurs) en
    SMF format 1, avec des program changes optionnels {canal: programme GM}
    au tick 0.

    Nécessaire au rendu instrumental : nos fichiers générés n'assignent
    aucun instrument, et un synthétiseur SoundFont jouerait tout au piano.
    Les vélocités sont recopiées telles quelles — c'est la variable
    d'expérience, elle ne doit subir aucune transformation ici."""
    meta: list[tuple[int, int, bytes]] = []
    for tick, bpm in (md.tempos or [(0, 120.0)]):
        us = round(60_000_000 / max(1e-6, bpm))
        meta.append((tick, 0, bytes([0xFF, 0x51, 0x03]) + us.to_bytes(3, "big")))
    for tick, num, den in md.time_sigs:
        den_pow = max(0, int(den).bit_length() - 1)
        meta.append((tick, 1, bytes([0xFF, 0x58, 0x04, num, den_pow, 24, 8])))
    for tick, text in md.markers:
        raw = text.encode("latin-1", errors="replace")
        meta.append((tick, 2, bytes([0xFF, 0x06]) + _varlen(len(raw)) + raw))

    events: list[tuple[int, int, bytes]] = []
    for channel, program in sorted((programs or {}).items()):
        events.append((0, 0, bytes([0xC0 | (channel & 0x0F), program & 0x7F])))
    # Contrôleurs avant les note-on au même tick (ordre 0 < 1) : une pédale
    # levée doit précéder les attaques qu'elle ne soutient plus.
    for tick, channel, controller, value in md.controls:
        events.append((tick, 0, bytes([0xB0 | (channel & 0x0F),
                                       controller & 0x7F, value & 0x7F])))
    for n in md.notes:
        events.append((n.start, 1, bytes([0x90 | (n.channel & 0x0F),
                                          n.pitch & 0x7F, max(1, n.velocity) & 0x7F])))
        events.append((max(n.start + 1, n.end), 0,
                       bytes([0x80 | (n.channel & 0x0F), n.pitch & 0x7F, 0])))

    chunks = [_track_chunk(meta), _track_chunk(events)]
    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") \
        + len(chunks).to_bytes(2, "big") + md.ppq.to_bytes(2, "big")
    Path(path).write_bytes(header + b"".join(chunks))


# ──────────────────────────────────────────────
# Temps réel et découpe d'extraits
# ──────────────────────────────────────────────

def tick_to_seconds(md: MidiData, tick: int) -> float:
    """Position en secondes d'un tick, par intégration morceau par morceau
    de la carte de tempo. 120 BPM par convention MIDI avant le premier
    changement (et pour un fichier sans tempo)."""
    seconds = 0.0
    bpm = 120.0
    last_tick = 0
    for t, new_bpm in md.tempos:
        if t >= tick:
            break
        seconds += (t - last_tick) / md.ppq * 60.0 / bpm
        last_tick, bpm = t, new_bpm
    return seconds + (tick - last_tick) / md.ppq * 60.0 / bpm


def seconds_to_tick(md: MidiData, seconds: float) -> int:
    """Inverse de `tick_to_seconds`, même convention."""
    elapsed = 0.0
    bpm = 120.0
    last_tick = 0
    for t, new_bpm in md.tempos:
        span = (t - last_tick) / md.ppq * 60.0 / bpm
        if elapsed + span >= seconds:
            break
        elapsed += span
        last_tick, bpm = t, new_bpm
    return last_tick + round((seconds - elapsed) * bpm / 60.0 * md.ppq)


def slice_mididata(md: MidiData, start_s: float, dur_s: float) -> MidiData:
    """Extrait [start_s, start_s + dur_s) rebasé au tick 0.

    Conçu pour découper des extraits d'écoute dans des interprétations
    réelles (fichiers de 10-30 min). Trois soins particuliers :

    - seules les notes dont l'ATTAQUE tombe dans la fenêtre sont gardées
      (une note entamée avant serait une résonance sans frappe) ; leur fin
      est écrêtée à la fenêtre ;
    - l'état des contrôleurs au début de fenêtre est réémis au tick 0 —
      une pédale enfoncée trois mesures plus tôt doit rester enfoncée dans
      l'extrait, sinon les premières secondes sonnent sèches ;
    - le tempo et la signature actifs au début de fenêtre sont réémis au
      tick 0, puis les changements internes suivent, décalés.
    """
    t0 = seconds_to_tick(md, start_s)
    t1 = seconds_to_tick(md, start_s + dur_s)

    notes = [MidiNote(n.start - t0, min(n.end, t1) - t0, n.pitch,
                      n.velocity, n.channel, n.track)
             for n in md.notes if t0 <= n.start < t1 and min(n.end, t1) > n.start]

    def _rebase(entries, default):
        """Dernière entrée ≤ t0 réémise à 0 (ou défaut), puis l'intérieur."""
        state = default
        inside = []
        for entry in entries:
            if entry[0] <= t0:
                state = entry[1:]
            elif entry[0] < t1:
                inside.append((entry[0] - t0, *entry[1:]))
        head = [(0, *state)] if state is not None else []
        return head + inside

    tempos = _rebase(md.tempos, (120.0,))
    time_sigs = _rebase(md.time_sigs, None)
    markers = [(t - t0, text) for t, text in md.markers if t0 <= t < t1]

    # État de chaque contrôleur (canal, cc) au début de fenêtre, puis les
    # changements internes. Un état nul n'est pas réémis (défaut MIDI).
    state: dict[tuple[int, int], int] = {}
    inside_cc: list[tuple[int, int, int, int]] = []
    for tick, channel, controller, value in md.controls:
        if tick <= t0:
            state[(channel, controller)] = value
        elif tick < t1:
            inside_cc.append((tick - t0, channel, controller, value))
    controls = [(0, ch, cc, val) for (ch, cc), val in sorted(state.items())
                if val > 0] + inside_cc

    return MidiData(ppq=md.ppq, notes=notes, tempos=tempos,
                    time_sigs=time_sigs, markers=markers,
                    controls=controls, end_tick=t1 - t0)


def write_midi(
    path: str | Path,
    tracks: list[list[tuple[float, float, int, int, int]]],
    ppq: int = 480,
    bpm: float = 120.0,
    time_sig: tuple[int, int] = (4, 4),
    markers: list[tuple[float, str]] | None = None,
    tempo_changes: list[tuple[float, float]] | None = None,
) -> None:
    """tracks : liste de pistes, chaque note = (start_beat, dur_beats, pitch,
    velocity, channel). markers/tempo_changes en beats."""
    meta: list[tuple[int, int, bytes]] = []
    us = round(60_000_000 / bpm)
    meta.append((0, 0, bytes([0xFF, 0x51, 0x03]) + us.to_bytes(3, "big")))
    num, den = time_sig
    den_pow = max(0, den.bit_length() - 1)
    meta.append((0, 1, bytes([0xFF, 0x58, 0x04, num, den_pow, 24, 8])))
    for beat, text in markers or []:
        raw = text.encode("latin-1", errors="replace")
        meta.append((round(beat * ppq), 2, bytes([0xFF, 0x06]) + _varlen(len(raw)) + raw))
    for beat, new_bpm in tempo_changes or []:
        us = round(60_000_000 / new_bpm)
        meta.append((round(beat * ppq), 3, bytes([0xFF, 0x51, 0x03]) + us.to_bytes(3, "big")))

    chunks = [_track_chunk(meta)]
    for notes in tracks:
        events: list[tuple[int, int, bytes]] = []
        for start_beat, dur_beats, pitch, vel, channel in notes:
            on = round(start_beat * ppq)
            off = round((start_beat + dur_beats) * ppq)
            events.append((on, 1, bytes([0x90 | channel, pitch, vel])))
            events.append((max(on + 1, off), 0, bytes([0x80 | channel, pitch, 0])))
        chunks.append(_track_chunk(events))

    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") \
        + len(chunks).to_bytes(2, "big") + ppq.to_bytes(2, "big")
    Path(path).write_bytes(header + b"".join(chunks))
