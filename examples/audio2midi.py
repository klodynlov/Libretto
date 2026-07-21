"""
audio2midi — le convertisseur audio → MIDI, autonome et payé UNE fois.

Pourquoi un script séparé
-------------------------
Libretto ne lit que du MIDI ; tout modèle audio (ACE-Step, Suno local,
YuE…) impose donc une conversion. Elle est ici DÉCOUPLÉE de la sélection,
parce que les deux coûts n'ont rien à voir : la séparation + transcription
coûte ~1-3 min par prise sur CPU, le jugement Libretto quelques dizaines de
millisecondes. Convertissez une fois, puis rejouez Forge autant de fois que
vous voulez (autres gates, `--axes`, `--shortlist`, sweep…) sans re-payer
le maillon lourd :

    python3 examples/audio2midi.py prises/ midi/          # une fois
    python3 examples/forge.py sortie/ --from-dir midi/ --axes --shortlist 5

(`forge_acestep.py` reste le raccourci qui enchaîne les deux d'un trait.)

La chaîne, par stems
--------------------
Transcrire le mix entier en une passe détruit précisément ce que les axes
mesurent. On sépare donc d'abord :

    audio → Demucs (voix/batterie/basse/reste)
          → basic-pitch par stem TONAL   (voix → canal 0 : la mélodie ;
                                          reste → canal 1 ; basse → canal 2,
                                          elle nourrit l'axe 14)
          → onsets de batterie → canal 9 (exclu du chroma et de la mélodie
                                          par Libretto, mais il alimente les
                                          axes rythmiques)
          → tempo estimé sur le mix, borné [60, 200]
          → fusion en un MIDI multi-pistes au tempo estimé

⚠ Le résultat reste du TRANSCRIT : score sous-évalué, classement
partiellement préservé, et une fiabilité qui ne détecte pas les artefacts
de transcription — chiffres mesurés et rejouables dans
`transcription_roundtrip.py`, conséquences documentées au README (« le
piège du transcrit »). Sur cette matière, Forge est un triage grossier.

Dépendances — OPTIONNELLES, hors contrat stdlib
-----------------------------------------------
    pip install demucs basic-pitch "numpy<2"

(librosa vient avec basic-pitch ; le pin numpy<2 contourne une
incompatibilité binaire pandas/numpy 2 au moment d'écrire ces lignes).
Demucs télécharge ses poids au premier lancement.

Usage
-----
    python3 examples/audio2midi.py prises/ midi/
        [--model htdemucs] [--no-drums] [--keep-work DIR]

Code retour 2 si aucune prise audio, 3 si dépendance manquante.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import write_midi  # noqa: E402

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg")
# Stems tonals → canal MIDI. La batterie est traitée à part (canal 9).
PITCHED_STEMS = {"vocals": 0, "other": 1, "bass": 2}
DRUM_CHANNEL = 9
DRUM_PITCH = 36  # kick GM : un déclencheur, pas une hauteur

PIP_HINT = ("  pip install demucs basic-pitch \"numpy<2\"\n"
            "Le cœur de Libretto reste 100 % stdlib : ces dépendances ne "
            "sont requises que par ce pont.")


def _estimate_tempo(mix_path: Path) -> float:
    """Tempo global du mix, borné à [60, 200] par repliement d'octave — un
    beat-tracker qui rend 320 bpm entend les doubles-croches. Le repliement
    ne répare pas les décrochages métriques (64 rendu pour 112 : mesuré) ;
    heureusement le round-trip montre que le tempo n'est pas la perte
    dominante du maillon."""
    import librosa
    y, sr = librosa.load(str(mix_path), sr=22050, mono=True)
    tempo = float(librosa.beat.beat_track(y=y, sr=sr)[0])
    if tempo <= 0:
        return 120.0
    while tempo > 200:
        tempo /= 2
    while tempo < 60:
        tempo *= 2
    return tempo


def _separate(audio: Path, work: Path, model: str) -> Path:
    """Sépare la prise en stems via Demucs (sous-processus : stable d'une
    version à l'autre). Renvoie le dossier contenant vocals/drums/bass/other."""
    subprocess.run(
        [sys.executable, "-m", "demucs.separate", "-n", model,
         "-o", str(work), str(audio)],
        check=True, capture_output=True, text=True)
    stem_dir = work / model / audio.stem
    if not stem_dir.is_dir():
        raise FileNotFoundError(f"stems attendus dans {stem_dir}")
    return stem_dir


def _transcribe_stem(wav: Path, bpm: float, channel: int, model) -> list:
    """Un stem tonal → notes (start_beat, dur_beats, pitch, velocity, channel)
    via basic-pitch. L'amplitude devient la vélocité : c'est approximatif,
    mais c'est la seule dynamique que la transcription sait rendre."""
    from basic_pitch.inference import predict
    _, _, note_events = predict(str(wav), model)
    notes = []
    for ev in note_events:
        start_s, end_s, pitch, amplitude = ev[0], ev[1], int(ev[2]), float(ev[3])
        start_b = start_s * bpm / 60.0
        dur_b = max((end_s - start_s) * bpm / 60.0, 0.05)
        vel = max(1, min(127, round(amplitude * 127)))
        notes.append((start_b, dur_b, pitch, vel, channel))
    return notes


def _drum_onsets(wav: Path, bpm: float) -> list:
    """Le stem batterie → déclencheurs sur le canal 9. Libretto exclut ce
    canal du chroma et de la mélodie, mais les attaques nourrissent les axes
    rythmiques — les jeter appauvrirait le jugement pour rien."""
    import librosa
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    times = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    return [(t * bpm / 60.0, 0.125, DRUM_PITCH, 100, DRUM_CHANNEL)
            for t in times]


def transcribe_take(audio: Path, out_mid: Path, work: Path, model: str,
                    bp_model, with_drums: bool = True) -> float:
    """Une prise audio → un MIDI multi-pistes par stems. Renvoie le bpm
    estimé (écrit dans le MIDI : la grille des mesures en dépend)."""
    bpm = _estimate_tempo(audio)
    stem_dir = _separate(audio, work, model)

    tracks = []
    for stem, channel in PITCHED_STEMS.items():
        wav = stem_dir / f"{stem}.wav"
        if wav.exists():
            notes = _transcribe_stem(wav, bpm, channel, bp_model)
            if notes:
                tracks.append(notes)
    if with_drums:
        wav = stem_dir / "drums.wav"
        if wav.exists():
            hits = _drum_onsets(wav, bpm)
            if hits:
                tracks.append(hits)

    if not tracks:
        return bpm  # rien de transcrit : pas de fichier, la prise sera « vide »
    write_midi(out_mid, tracks, ppq=480, bpm=bpm, time_sig=(4, 4))
    return bpm


def convert_dir(audio_dir: str | Path, midi_dir: str | Path,
                model: str = "htdemucs", with_drums: bool = True,
                work_dir: str | Path | None = None) -> list[tuple[str, float]]:
    """Convertit toutes les prises d'un dossier. Renvoie [(nom, bpm estimé)].

    C'est le point d'entrée programmatique (forge_acestep s'en sert). Les
    dépendances doivent être présentes — main() fait le contrôle poli."""
    from basic_pitch import ICASSP_2022_MODEL_PATH
    try:
        from basic_pitch.inference import Model
        bp_model = Model(ICASSP_2022_MODEL_PATH)
    except Exception:
        bp_model = ICASSP_2022_MODEL_PATH

    takes = sorted(p for p in Path(audio_dir).iterdir()
                   if p.suffix.lower() in AUDIO_EXTS)
    out = Path(midi_dir)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else Path(
        tempfile.mkdtemp(prefix="audio2midi_"))

    results = []
    for i, take in enumerate(takes):
        print(f"[{i + 1}/{len(takes)}] {take.name} : séparation + "
              f"transcription…", file=sys.stderr)
        bpm = transcribe_take(take, out / f"{take.stem}.mid", work,
                              model, bp_model, with_drums=with_drums)
        print(f"    tempo estimé {bpm:.0f} bpm", file=sys.stderr)
        results.append((take.stem, bpm))

    if not work_dir:
        shutil.rmtree(work, ignore_errors=True)
    return results


def check_deps() -> str | None:
    """None si tout est là, sinon le nom du module manquant."""
    try:
        import demucs.separate  # noqa: F401
        import basic_pitch  # noqa: F401
        import librosa  # noqa: F401
    except ImportError as exc:
        return str(exc)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audio2midi",
        description="Convertit des prises audio en MIDI multi-pistes par "
                    "stems (Demucs + basic-pitch), prêtes pour "
                    "forge --from-dir. Le coût lourd, payé une fois.")
    parser.add_argument("audio_dir", help="dossier des prises audio")
    parser.add_argument("midi_dir", help="dossier de sortie des MIDI")
    parser.add_argument("--model", default="htdemucs",
                        help="modèle Demucs (défaut htdemucs)")
    parser.add_argument("--no-drums", action="store_true",
                        help="ne pas transcrire les onsets de batterie (canal 9)")
    parser.add_argument("--keep-work", metavar="DIR",
                        help="conserver les stems séparés dans DIR "
                             "(défaut : effacés)")
    args = parser.parse_args(argv)

    missing = check_deps()
    if missing:
        print(f"audio2midi : dépendance manquante ({missing}).\n{PIP_HINT}",
              file=sys.stderr)
        return 3

    results = convert_dir(args.audio_dir, args.midi_dir, model=args.model,
                          with_drums=not args.no_drums,
                          work_dir=args.keep_work)
    if not results:
        print(f"audio2midi : aucune prise audio dans {args.audio_dir} "
              f"(extensions cherchées : {', '.join(AUDIO_EXTS)})",
              file=sys.stderr)
        return 2

    print(f"{len(results)} prises converties → {args.midi_dir}")
    print(f"suite : python3 examples/forge.py sortie/ --from-dir "
          f"{args.midi_dir} --axes --shortlist 5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
