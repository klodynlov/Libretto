"""
Libretto — rendu instrumental via FluidSynth (outil externe optionnel).

Pourquoi ce module existe
-------------------------
Trois sessions d'écoute ont montré que la dynamique MIDI ne porte aucune
structure perceptible sous les deux rendus de synthèse maison (volume seul,
puis timbre+attaque modélisés). Mais un modèle de timbre n'est pas un
instrument : dans une banque d'échantillons, la vélocité déclenche des
**couches d'enregistrement différentes** — un piano frappé fort n'est pas un
piano doux amplifié, c'est un autre son. C'est la dernière hypothèse qui
puisse réhabiliter les axes dynamiques (26, 28) : si la dynamique ne
s'entend toujours pas ici, la question est close.

Frontière stdlib
----------------
Le paquet Python reste 100 % stdlib : ce module n'utilise que `subprocess`
et `shutil.which`. FluidSynth est un OUTIL externe optionnel, au même titre
que REAPER pour `libretto reaper` — s'il est absent, tout le reste de
Libretto fonctionne, et `annotate --render instrument` explique quoi
installer au lieu de planter.

Instruments
-----------
Nos fichiers générés n'assignent aucun programme : sans intervention, un
synthétiseur SoundFont jouerait tout au piano, et les pupitres se
confondraient. `GM_PROGRAMS` mappe les canaux du générateur de corpus vers
des instruments General MIDI choisis pour leurs couches de vélocité
(piano, basse électrique, piano électrique, cordes). Le canal 9 reste la
batterie, par convention GM.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .midi import MidiData, write_mididata

# canal du générateur de corpus -> programme General MIDI
GM_PROGRAMS = {
    0: 0,     # accords  -> piano acoustique (couches de vélocité riches)
    1: 33,    # basse    -> basse électrique (doigts)
    2: 4,     # mélodie  -> piano électrique (brillance très vélocité-dépendante)
    3: 0,
    4: 48,    # nappe    -> ensemble de cordes
    5: 0, 6: 0, 7: 0, 8: 0,
    10: 21,   # accordéon des exemples Naija
}

SOUNDFONT_DIRS = [
    Path.home() / "Library/Audio/Sounds/Banks",
    Path("/opt/homebrew/share/soundfonts"),
    Path("/usr/local/share/soundfonts"),
    Path("/usr/share/sounds/sf2"),
]


def find_fluidsynth() -> str | None:
    return shutil.which("fluidsynth")


def find_soundfont() -> Path | None:
    """Première banque trouvée dans les emplacements usuels, .sf2 ou .sf3."""
    for directory in SOUNDFONT_DIRS:
        if not directory.is_dir():
            continue
        for pattern in ("*.sf2", "*.sf3", "*.SF2", "*.SF3"):
            for path in sorted(directory.glob(pattern)):
                return path
    return None


def renderer_available() -> tuple[str, Path] | None:
    """(binaire fluidsynth, soundfont) si le rendu instrumental est possible."""
    binary = find_fluidsynth()
    font = find_soundfont()
    if binary and font:
        return binary, font
    return None


def install_hint() -> str:
    return ("rendu instrumental indisponible — installez l'outil puis une "
            "banque :\n"
            "  brew install fluid-synth\n"
            "  curl -L -o ~/Library/Audio/Sounds/Banks/MuseScore_General.sf3 "
            "\\\n    https://ftp.osuosl.org/pub/musescore/soundfont/"
            "MuseScore_General/MuseScore_General.sf3")


def render_wav(md: MidiData, out_path: str | Path, fluidsynth: str,
               soundfont: str | Path, gain: float = 0.5,
               programs: dict[int, int] | None = None,
               sample_rate: int = 44100) -> Path:
    """Rend un MidiData en WAV via FluidSynth. Déterministe : même MidiData,
    même banque, même sortie. Lève ValueError si le rendu échoue — le
    protocole d'écoute préfère échouer bruyamment que servir un silence."""
    out = Path(out_path)
    with tempfile.TemporaryDirectory() as tmp:
        mid = Path(tmp) / "in.mid"
        write_mididata(mid, md, programs=GM_PROGRAMS if programs is None else programs)
        cmd = [fluidsynth, "-ni", "-g", str(gain), "-r", str(sample_rate),
               "-F", str(out), str(soundfont), str(mid)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        raise ValueError(
            f"rendu FluidSynth échoué (code {proc.returncode}) : "
            f"{(proc.stderr or proc.stdout).strip()[:300]}")
    return out
