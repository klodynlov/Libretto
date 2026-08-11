"""
Libretto — jouer une séquence dans Korg Gadget (ou tout synthé) via MIDI virtuel.

Pendant de `libretto/reaper.py`, côté sortie. Le pont REAPER *construit* un
projet parce que REAPER est scriptable ; Gadget, lui, n'expose aucune API de
script ni d'OSC — impossible d'y bâtir un pont équivalent. Le plus proche :
un port MIDI virtuel (CoreMIDI / IAC) sur lequel une piste Gadget *armée* joue
les notes en temps réel. Ce script ouvre un port « Libretto », programme les
notes du .mid (tempo map respectée, comme le pont REAPER) et les envoie.

Audition, pas arrangement : Gadget joue les notes, il ne reconstruit pas les
pistes ni les marqueurs. Pour un vrai projet, importe le .mid dans Gadget
(bouton « ↓ .mid » de l'interface, puis import MIDI de Gadget).

Optionnel — hors du 100 % stdlib du cœur `libretto` : nécessite python-rtmidi
(backend CoreMIDI). Testé sur macOS ; les ports virtuels marchent aussi sous
Linux (ALSA). Windows n'a pas de port virtuel natif : passer par un bus loopMIDI
existant avec --port.

    pip install python-rtmidi

Usage :
    python3 examples/play_gadget.py forge_winner.mid            # port virtuel « Libretto »
    python3 examples/play_gadget.py forge_winner.mid --channel 1
    python3 examples/play_gadget.py forge_winner.mid --port "IAC Driver Bus 1"
    python3 examples/play_gadget.py --list                      # ports de sortie dispo
    python3 examples/play_gadget.py forge_winner.mid --dry-run  # plan de lecture, sans rtmidi

Dans Gadget : choisis « Libretto » (ou ton bus IAC) comme entrée MIDI de la
piste, arme-la, puis lance ce script.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.gadget import (  # noqa: E402  — cœur partagé (schedule + constantes)
    ALL_NOTES_OFF, DEFAULT_PORT_NAME, NOTE_OFF, NOTE_ON, PIP_HINT,
    schedule_events)
from libretto.midi import parse_midi  # noqa: E402


def _import_rtmidi():
    try:
        import rtmidi
    except ImportError as exc:  # dépendance optionnelle, hors cœur stdlib
        raise SystemExit(f"play_gadget : python-rtmidi manquant — {PIP_HINT}") from exc
    return rtmidi


def _open_output(rtmidi, port: str | None, virtual: str):
    """Ouvre une sortie MIDI : un port existant (index ou sous-chaîne de nom),
    sinon crée un port virtuel `virtual`. Renvoie (midiout, description)."""
    midiout = rtmidi.MidiOut()
    ports = midiout.get_ports()
    if port is None:
        midiout.open_virtual_port(virtual)
        return midiout, f"port virtuel « {virtual} »"
    idx = None
    if port.isdigit() and int(port) < len(ports):
        idx = int(port)
    else:
        idx = next((i for i, name in enumerate(ports)
                    if port.lower() in name.lower()), None)
    if idx is None:
        raise SystemExit(
            f"play_gadget : port introuvable « {port} » ; "
            f"dispo : {ports or '(aucun — active l’IAC Driver)'}")
    midiout.open_port(idx)
    return midiout, f"« {ports[idx]} »"


def _all_notes_off(midiout) -> None:
    for ch in range(16):
        midiout.send_message([0xB0 | ch, ALL_NOTES_OFF, 0])


def _play_once(midiout, events) -> None:
    start = time.monotonic()
    for t, msg in events:
        wait = t - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        midiout.send_message(msg)


def play(mid_path, port: str | None = None, virtual: str = DEFAULT_PORT_NAME,
         channel: int | None = None, loop: bool = False,
         countdown: float = 2.0) -> int:
    md = parse_midi(mid_path)
    if not md.notes:
        raise SystemExit(f"play_gadget : aucune note dans {mid_path}")
    events = schedule_events(md, channel)
    duration = events[-1][0]

    rtmidi = _import_rtmidi()
    midiout, where = _open_output(rtmidi, port, virtual)
    print(f"play_gadget : {len(md.notes)} notes · {duration:.1f}s · sortie {where}")
    if port is None:
        print(f"  → dans Gadget : arme une piste sur l’entrée MIDI « {virtual} »")
    if countdown > 0:
        print(f"  → lecture dans {countdown:.0f}s…  (Ctrl-C pour arrêter)")
        time.sleep(countdown)
    try:
        while True:
            _play_once(midiout, events)
            if not loop:
                break
    except KeyboardInterrupt:
        print("\nplay_gadget : arrêt")
    finally:
        _all_notes_off(midiout)
        midiout.close_port()
        del midiout
    return 0


def _dry_run(mid_path, channel: int | None) -> int:
    md = parse_midi(mid_path)
    if not md.notes:
        raise SystemExit(f"play_gadget : aucune note dans {mid_path}")
    events = schedule_events(md, channel)
    ons = sum(1 for _, m in events if (m[0] & 0xF0) == NOTE_ON)
    print(f"play_gadget (dry-run) : {len(md.notes)} notes · {events[-1][0]:.1f}s · "
          f"{ons} note-on · aucun port ouvert")
    for t, m in events[:8]:
        kind = "on " if (m[0] & 0xF0) == NOTE_ON else "off"
        print(f"  {t:7.3f}s  {kind} canal {(m[0] & 0x0F) + 1:2d}  pitch {m[1]:3d}  vel {m[2]:3d}")
    if len(events) > 8:
        print(f"  … (+{len(events) - 8} messages)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Jouer un .mid dans Korg Gadget via un port MIDI virtuel.")
    p.add_argument("midi", nargs="?", help="fichier .mid à jouer")
    p.add_argument("--port", help="port de sortie existant : index ou nom (ex. IAC)")
    p.add_argument("--virtual", default=DEFAULT_PORT_NAME,
                   help=f"nom du port virtuel à créer (défaut : {DEFAULT_PORT_NAME})")
    p.add_argument("--channel", type=int,
                   help="forcer toutes les notes sur ce canal (1-16)")
    p.add_argument("--loop", action="store_true", help="rejouer en boucle")
    p.add_argument("--no-countdown", action="store_true",
                   help="jouer immédiatement, sans décompte")
    p.add_argument("--list", action="store_true", dest="list_ports",
                   help="lister les ports de sortie MIDI et quitter")
    p.add_argument("--dry-run", action="store_true",
                   help="afficher le plan de lecture sans ouvrir de port (aucune dépendance)")
    args = p.parse_args(argv)

    if args.channel is not None and not (1 <= args.channel <= 16):
        p.error("--channel doit être entre 1 et 16")

    if args.list_ports:
        rtmidi = _import_rtmidi()
        ports = rtmidi.MidiOut().get_ports()
        print("Ports de sortie MIDI :")
        for i, name in enumerate(ports):
            print(f"  [{i}] {name}")
        if not ports:
            print("  (aucun — active l’IAC Driver, ou un port virtuel sera créé)")
        return 0

    if not args.midi:
        p.error("fichier .mid requis (ou --list)")

    if args.dry_run:
        return _dry_run(args.midi, args.channel)

    return play(args.midi, port=args.port, virtual=args.virtual,
                channel=args.channel, loop=args.loop,
                countdown=0.0 if args.no_countdown else 2.0)


if __name__ == "__main__":
    raise SystemExit(main())
