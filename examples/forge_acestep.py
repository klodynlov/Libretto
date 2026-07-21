"""
Forge × ACE-Step — trier les prises d'un modèle AUDIO local, par stems.

La chaîne
---------
Un modèle type ACE-Step (ou Suno local, YuE, DiffRhythm…) sort de l'audio
mixé — voix, batterie, reverb. Libretto ne juge que du MIDI : il faut donc
transcrire, et transcrire le mix entier en une passe détruit précisément ce
que les axes mesurent. Ce script fait le trajet proprement, par stems :

    ┌──────────┐  ┌─────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────┐
    │ ACE-Step │→ │ Demucs  │→ │ basic-pitch  │→ │  LIBRETTO  │→ │ sélection│
    │ (prises  │  │ (stems) │  │ par stem     │  │ SMS + fiab.│  │ shortlist│
    │  .wav)   │  │ v/d/b/o │  │ + fusion ch9 │  └────────────┘  └──────────┘
    └──────────┘  └─────────┘  └──────────────┘

Par prise : séparation Demucs (voix/batterie/basse/reste), tempo estimé sur
le mix (librosa), transcription basic-pitch de chaque stem TONAL (la basse
séparée nourrit l'axe 14, la voix devient la mélodie), onsets de batterie
sur le canal 9 (exclu du chroma et de la mélodie par Libretto, mais il
alimente les axes rythmiques), fusion en un MIDI multi-pistes au tempo
estimé. Puis `forge_from_dir` fait son travail habituel — gate, classement
fiabilité-d'abord, `--axes`, `--shortlist`.

⚠ LE PIÈGE DU TRANSCRIT — à lire avant de faire confiance aux chiffres
----------------------------------------------------------------------
Mesuré sur un aller-retour contrôlé (voir
`examples/transcription_roundtrip.py`, le harnais rejouable) : la
transcription fait chuter le score SMS (~−0.18 en moyenne sur de l'audio
*propre* — un mix réel fera pire), ne préserve le classement que
partiellement (Spearman +0.1 à +0.6 : le gagnant vrai peut ressortir 5ᵉ),
et — le piège — **la fiabilité ne détecte rien** : elle peut même MONTER
après transcription, parce que basic-pitch produit beaucoup de notes et que
la confiance mesure la quantité de matière, pas sa provenance. Le gate
`--min-confidence` ne protège donc PAS d'une mauvaise transcription.

Conséquence pratique : utilisez ce pipeline en TRIAGE GROSSIER, pas en juge
fin. Écarter le tiers du bas est fiable ; couronner le n°1 ne l'est pas.
D'où le défaut `--shortlist 5` : Forge réduit ce que vos oreilles doivent
écouter, il ne les remplace pas.

Dépendances — OPTIONNELLES, hors contrat stdlib
-----------------------------------------------
    pip install demucs basic-pitch "numpy<2"

(librosa vient avec basic-pitch ; le pin numpy<2 contourne une
incompatibilité binaire pandas/numpy 2 au moment d'écrire ces lignes).
Demucs télécharge ses poids au premier lancement. Comptez ~1-3 min par
prise sur CPU (séparation comprise).

Usage
-----
    python3 examples/forge_acestep.py prises/ sortie/
        [--model htdemucs] [--no-drums] [--keep-work]
        [--min-confidence 0.55] [--min-score 0.0]
        [--axes] [--shortlist 5]

`prises/` contient les fichiers audio des N prises (.wav/.mp3/.flac/.ogg).
Sortie : `sortie/candidates/*.mid` (les transcriptions, conservées),
`sortie/forge_winner.mid`, `sortie/forge_short_XX.mid`,
`sortie/forge_report.json`. Code retour 2 si aucun candidat fiable, 3 si
dépendance manquante.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.midi import write_midi  # noqa: E402

from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, forge_from_dir)

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg")
# Stems tonals → canal MIDI. La batterie est traitée à part (canal 9).
PITCHED_STEMS = {"vocals": 0, "other": 1, "bass": 2}
DRUM_CHANNEL = 9
DRUM_PITCH = 36  # kick GM : un déclencheur, pas une hauteur


def _estimate_tempo(mix_path: Path) -> float:
    """Tempo global du mix, borné à [60, 200] par repliement d'octave — un
    beat-tracker qui rend 320 bpm entend les doubles-croches."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_acestep",
        description="Forge × ACE-Step — sépare (Demucs), transcrit par stem "
                    "(basic-pitch), fusionne, puis sélection Libretto. "
                    "TRIAGE GROSSIER : voir « le piège du transcrit » dans "
                    "la docstring.")
    parser.add_argument("takes_dir", help="dossier des prises audio")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("--model", default="htdemucs",
                        help="modèle Demucs (défaut htdemucs)")
    parser.add_argument("--no-drums", action="store_true",
                        help="ne pas transcrire les onsets de batterie (canal 9)")
    parser.add_argument("--keep-work", action="store_true",
                        help="conserver les stems séparés (défaut : effacés)")
    parser.add_argument("--min-confidence", type=float, default=0.55,
                        help="fiabilité minimale pour concourir (défaut 0.55 — "
                             "rappel : elle ne détecte PAS les artefacts de "
                             "transcription)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=5, metavar="K",
                        help="K candidats sous contrainte de diversité "
                             "(défaut 5 : sur du transcrit, on livre une "
                             "shortlist aux oreilles, pas un verdict)")
    args = parser.parse_args(argv)

    try:
        import demucs.separate  # noqa: F401
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model
        import librosa  # noqa: F401
    except ImportError as exc:
        print(f"forge_acestep : dépendance manquante ({exc}).\n"
              f"  pip install demucs basic-pitch \"numpy<2\"\n"
              f"Le cœur de Libretto reste 100 % stdlib : ces dépendances ne "
              f"sont requises que par ce pont.", file=sys.stderr)
        return 3

    takes = sorted(p for p in Path(args.takes_dir).iterdir()
                   if p.suffix.lower() in AUDIO_EXTS)
    if not takes:
        print(f"forge_acestep : aucune prise audio dans {args.takes_dir} "
              f"(extensions cherchées : {', '.join(AUDIO_EXTS)})",
              file=sys.stderr)
        return 2

    try:
        bp_model = Model(ICASSP_2022_MODEL_PATH)
    except Exception:
        bp_model = ICASSP_2022_MODEL_PATH

    out = Path(args.out_dir)
    cand_dir = out / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="forge_acestep_")) \
        if not args.keep_work else out / "stems"

    for i, take in enumerate(takes):
        print(f"[{i + 1}/{len(takes)}] {take.name} : séparation + "
              f"transcription…", file=sys.stderr)
        bpm = transcribe_take(take, cand_dir / f"{take.stem}.mid", work,
                              args.model, bp_model,
                              with_drums=not args.no_drums)
        print(f"    tempo estimé {bpm:.0f} bpm", file=sys.stderr)

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)

    report = forge_from_dir(cand_dir, out,
                            min_confidence=args.min_confidence,
                            min_score=args.min_score,
                            axes_report=args.axes, shortlist=args.shortlist)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    print("\n⚠ Rappel : classement sur MIDI TRANSCRIT — triage grossier, "
          "pas verdict fin. La fiabilité affichée ne voit pas les artefacts "
          "de transcription (docstring, « le piège du transcrit »). "
          "Écoutez la shortlist.")
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
