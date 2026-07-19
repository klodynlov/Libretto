"""
Libretto — client du pont REAPER Klody (TCP 127.0.0.1:9000, JSON par ligne).

Le pont (`klody-code-ai/reaper_bridge`) tourne DANS REAPER (auto-démarré par
__startup.lua). Ce module pousse une MidiData dans le projet : pistes nommées,
notes en secondes (tempo map respectée), ReaSynth, marqueurs, lecture.
"""

from __future__ import annotations

import json
import socket

from .midi import MidiData

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 9000
CHUNK = 120  # notes par appel insert_midi_notes (garde-fou 64 KiB/ligne du pont)
DEFAULT_SYNTH = "ReaSynth"


class BridgeError(RuntimeError):
    pass


class ReaperBridge:
    def __init__(self, host: str = BRIDGE_HOST, port: int = BRIDGE_PORT, timeout: float = 15.0):
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise BridgeError(
                f"pont REAPER injoignable sur {host}:{port} — REAPER est-il lancé "
                f"(le pont Klody démarre avec lui) ?") from exc
        self.buf = b""

    def call(self, cmd: str, **args):
        self.sock.sendall((json.dumps({"cmd": cmd, "args": args}) + "\n").encode())
        while b"\n" not in self.buf:
            data = self.sock.recv(65536)
            if not data:
                raise BridgeError("pont REAPER : connexion fermée")
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        resp = json.loads(line)
        if not resp.get("ok"):
            raise BridgeError(f"{cmd}: {resp.get('error')}")
        return resp["result"]

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def tick_to_seconds(tempos: list[tuple[int, float]], ppq: int):
    """Convertisseur tick -> secondes suivant la tempo map (par morceaux)."""
    tempos = sorted(tempos) or [(0, 120.0)]
    if tempos[0][0] > 0:
        tempos = [(0, tempos[0][1])] + tempos
    segs: list[tuple[int, float, float]] = []  # (tick0, sec0, sec/tick)
    sec = 0.0
    for i, (tick, bpm) in enumerate(tempos):
        if i > 0:
            t0, s0, spt = segs[-1]
            sec = s0 + (tick - t0) * spt
        segs.append((tick, sec, 60.0 / bpm / ppq))

    def to_sec(tick: int) -> float:
        for t0, s0, spt in reversed(segs):
            if tick >= t0:
                return s0 + (tick - t0) * spt
        return tick * segs[0][2]

    return to_sec


def push_mididata(md: MidiData, track_names: list[str] | None = None,
                  synth: str = DEFAULT_SYNTH, base_db: float = -7.0,
                  play: bool = True) -> dict:
    """Pousse une MidiData dans REAPER : une piste par chunk MIDI source,
    notes en secondes, synthé + volume par piste, marqueurs, lecture."""
    to_sec = tick_to_seconds(md.tempos, md.ppq)
    by_track: dict[int, list] = {}
    for n in md.notes:
        by_track.setdefault(n.track, []).append(n)
    if not by_track:
        raise BridgeError("aucune note à pousser")

    bridge = ReaperBridge()
    try:
        info = bridge.call("ping")
        offset = bridge.call("get_track_count")["track_count"]
        first_bpm = (sorted(md.tempos) or [(0, 120.0)])[0][1]
        if offset == 0:
            bridge.call("set_tempo", bpm=round(first_bpm, 3))

        pushed = []
        for pos, src in enumerate(sorted(by_track)):
            notes = sorted(by_track[src], key=lambda n: n.start)
            name = (track_names[pos] if track_names and pos < len(track_names)
                    else f"Libretto {pos + 1}")
            idx = offset + pos
            bridge.call("add_track", name=name, index=idx)
            bridge.call("set_track_volume", index=idx, db=base_db)
            payload = [{"pitch": n.pitch,
                        "start": round(to_sec(n.start), 4),
                        "length": round(max(0.03, to_sec(n.end) - to_sec(n.start)), 4),
                        "velocity": n.velocity,
                        "channel": n.channel} for n in notes]
            for i in range(0, len(payload), CHUNK):
                bridge.call("insert_midi_notes", index=idx, notes=payload[i:i + CHUNK])
            bridge.call("add_fx", index=idx, name=synth)
            pushed.append({"track": name, "notes": len(payload)})

        for tick, text in md.markers:
            bridge.call("add_marker", position=round(to_sec(tick), 4), name=text)

        if play:
            bridge.call("transport_play")
        return {"reaper": info.get("reaper"), "tracks": pushed,
                "markers": len(md.markers), "playing": play,
                "total_notes": sum(p["notes"] for p in pushed)}
    finally:
        bridge.close()
