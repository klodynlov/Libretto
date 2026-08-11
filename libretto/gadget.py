"""
Libretto — sortie MIDI temps réel vers Korg Gadget (ou tout synthé) via un port
virtuel. Pendant de `reaper.py`, côté sortie.

Le pont REAPER *construit* un projet (REAPER est scriptable) ; Gadget n'expose
aucune API de script ni d'OSC. Le plus proche d'un « ▶ » est un port MIDI
virtuel (CoreMIDI / IAC) qu'une piste Gadget *armée* joue en temps réel :
audition, pas arrangement.

`python-rtmidi` (backend CoreMIDI) est optionnel — hors du 100 % stdlib du cœur.
Il n'est importé qu'à l'ouverture d'un port : importer ce module reste sûr sans
la dépendance (`available()` le teste). `schedule_events` est pur stdlib et
partagé avec `examples/play_gadget.py`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .midi import parse_midi
from .reaper import tick_to_seconds

DEFAULT_PORT_NAME = "Libretto"
NOTE_ON = 0x90
NOTE_OFF = 0x80
ALL_NOTES_OFF = 123  # contrôleur : coupe toutes les notes d'un canal
PIP_HINT = "pip install python-rtmidi"


class GadgetError(RuntimeError):
    pass


def available() -> bool:
    """python-rtmidi est-il installé ? (sans lever, pour piloter l'UI)."""
    try:
        import rtmidi  # noqa: F401
    except ImportError:
        return False
    return True


def schedule_events(md, channel: int | None = None) -> list[tuple[float, list[int]]]:
    """Notes -> messages MIDI datés en secondes, triés pour l'envoi.

    `channel` (1-16) force toutes les notes sur un même canal — un Gadget = une
    piste = un canal. `None` garde les canaux d'origine. Tempo map respectée via
    `tick_to_seconds` (partagé avec le pont REAPER). À instant égal, les note-off
    passent avant les note-on : on ne coupe pas une note re-frappée au même tick.
    """
    to_sec = tick_to_seconds(md.tempos, md.ppq)
    forced = None if channel is None else max(0, min(15, channel - 1))
    events: list[tuple[float, int, list[int]]] = []
    for n in md.notes:
        ch = n.channel if forced is None else forced
        pitch = max(0, min(127, n.pitch))
        vel = max(1, min(127, n.velocity))
        on = to_sec(n.start)
        off = max(on + 0.01, to_sec(n.end))  # durée plancher : jamais nulle
        events.append((on, 1, [NOTE_ON | ch, pitch, vel]))
        events.append((off, 0, [NOTE_OFF | ch, pitch, 0]))
    events.sort(key=lambda e: (e[0], e[1]))  # off (0) avant on (1) à t égal
    return [(t, msg) for t, _, msg in events]


class GadgetPlayer:
    """Un port virtuel persistant + un thread de lecture. Non bloquant : `play`
    démarre en fond et coupe toute lecture en cours ; `stop` arrête (all-notes-
    off). Pensé pour le serveur (un clic « ▶ Gadget » ne doit pas figer la
    requête HTTP pendant toute la durée du morceau).
    """

    def __init__(self, port_name: str = DEFAULT_PORT_NAME):
        self.port_name = port_name
        self._lock = threading.Lock()
        self._midiout = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _ensure_port(self) -> None:
        if self._midiout is not None:
            return
        try:
            import rtmidi
        except ImportError as exc:  # dépendance optionnelle
            raise GadgetError(f"python-rtmidi requis — {PIP_HINT}") from exc
        midiout = rtmidi.MidiOut()
        midiout.open_virtual_port(self.port_name)
        self._midiout = midiout

    def play(self, md, channel: int | None = 1, loop: bool = False) -> dict:
        """Joue une MidiData sur le port virtuel, en fond. Coupe d'abord toute
        lecture en cours. Renvoie un résumé pour l'UI."""
        if not md.notes:
            raise GadgetError("aucune note à jouer")
        events = schedule_events(md, channel)
        with self._lock:
            self._stop_locked()
            self._ensure_port()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(events, loop), daemon=True)
            self._thread.start()
        return {"notes": len(md.notes), "duration": round(events[-1][0], 1),
                "port": self.port_name, "loop": loop}

    def play_path(self, path, channel: int | None = 1, loop: bool = False) -> dict:
        return self.play(parse_midi(path), channel=channel, loop=loop)

    def _run(self, events, loop: bool) -> None:
        try:
            while not self._stop.is_set():
                base = time.monotonic()
                for t, msg in events:
                    wait = t - (time.monotonic() - base)
                    if wait > 0 and self._stop.wait(wait):
                        return  # stop demandé pendant l'attente : sortie nette
                    if self._stop.is_set():
                        return
                    self._midiout.send_message(msg)
                if not loop:
                    return
        finally:
            self._panic()

    def _panic(self) -> None:
        out = self._midiout
        if out is not None:
            for ch in range(16):
                out.send_message([0xB0 | ch, ALL_NOTES_OFF, 0])

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def is_playing(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def close(self) -> None:
        self.stop()
        with self._lock:
            if self._midiout is not None:
                self._midiout.close_port()
                self._midiout = None
