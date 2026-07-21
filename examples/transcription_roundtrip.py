"""
Aller-retour transcription — ce que le maillon audio→MIDI fait au jugement.

La question
-----------
Brancher un modèle AUDIO sur Forge (ACE-Step, Suno local…) impose une
transcription. Que perd-on dans ce maillon ? Trois grandeurs, mesurées sur
des morceaux dont Libretto connaît le score exact :

1. **le score SMS** après aller-retour MIDI → audio → basic-pitch → MIDI ;
2. **le classement** (la seule chose que Forge consomme) — corrélation de
   rang entre le classement vrai et le classement post-transcription ;
3. **la fiabilité** — détecte-t-elle la dégradation ?

Le protocole
------------
N candidats make_corpus (vérité connue), synthèse audio VOLONTAIREMENT
propre — sinus + 2 harmoniques, enveloppe exponentielle, percussions
exclues : tout ce que la transcription perd ici, elle le perdra a fortiori
sur un mix réel avec voix, batterie et reverb. Deux passes basic-pitch :
tempo naïf (120 par défaut) et tempo informé (le vrai bpm, celui qu'un
beat-tracker fournirait). Puis re-jugement Libretto et comparaison.

Le verdict mesuré (graine 1, 8 candidats, basic-pitch 0.4.0)
------------------------------------------------------------
- score SMS : **−0.18 en moyenne** (originaux 0.68-0.88 → transcrits
  0.55-0.73) — fragmentation des tenues, harmoniques fantômes ;
- classement : **partiellement détruit** (Spearman +0.57 naïf, +0.10
  informé ; le vrai n°1 ressort 5ᵉ). Le tempo informé ne sauve PAS le
  classement : la perte principale est dans le contenu des notes, pas la
  grille ;
- fiabilité : **aveugle au dégât** — elle peut MONTER après transcription
  (0.66 → 1.00 observé), car basic-pitch produit beaucoup de notes et la
  confiance mesure la quantité de matière, pas sa provenance. Le gate
  `--min-confidence` ne protège pas d'une mauvaise transcription.

Conséquence : sur du transcrit, Forge est un TRIAGE GROSSIER (écarter le
tiers du bas, livrer une shortlist aux oreilles), pas un juge fin. C'est
documenté dans le README et dans `forge_acestep.py` ; ce script est le
harnais qui permet de re-vérifier ces chiffres quand basic-pitch ou le
moteur bougent.

Dépendances — OPTIONNELLES, hors contrat stdlib
-----------------------------------------------
    pip install basic-pitch "numpy<2"

Usage
-----
    python3 examples/transcription_roundtrip.py [n=8] [seed=1] [--keep DIR]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.midi import parse_midi, tick_to_seconds  # noqa: E402

from forge import forge, judge_midi  # noqa: E402

SR = 22050


def render_wav(midpath: Path, wavpath: Path) -> float:
    """Synthèse volontairement propre : sinus + 2 harmoniques, enveloppe
    exponentielle, percussions (canal 9) exclues. C'est un PLAFOND
    optimiste : un mix réel transcrira moins bien. Renvoie le bpm vrai."""
    import numpy as np
    md = parse_midi(midpath)
    dur = tick_to_seconds(md, md.end_tick) + 1.0
    buf = np.zeros(int(dur * SR) + SR)
    for n in md.notes:
        if n.channel == 9:
            continue
        t0 = tick_to_seconds(md, n.start)
        t1 = tick_to_seconds(md, n.end)
        length = max(t1 - t0, 0.05)
        t = np.arange(int(length * SR)) / SR
        f = 440.0 * 2 ** ((n.pitch - 69) / 12)
        env = np.minimum(1.0, t / 0.01) * np.exp(-t * 3.0)
        sig = (np.sin(2 * np.pi * f * t)
               + 0.5 * np.sin(4 * np.pi * f * t)
               + 0.25 * np.sin(6 * np.pi * f * t)) * env * (n.velocity / 127.0)
        i0 = int(t0 * SR)
        buf[i0:i0 + len(sig)] += sig
    peak = float(np.abs(buf).max()) or 1.0
    pcm = (buf / peak * 0.9 * 32767).astype(np.int16)
    with wave.open(str(wavpath), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return md.tempos[0][1] if md.tempos else 120.0


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: -xs[i])
        r = [0.0] * len(xs)
        for pos, i in enumerate(order):
            r[i] = pos + 1.0
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return 1 - 6 * d2 / (n * (n * n - 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcription_roundtrip",
        description="Mesure ce que le maillon transcription (audio → "
                    "basic-pitch → MIDI) fait au score, au classement et à "
                    "la fiabilité.")
    parser.add_argument("n", nargs="?", type=int, default=8,
                        help="nombre de candidats (défaut 8)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--keep", metavar="DIR",
                        help="conserver wav et MIDI transcrits dans DIR")
    args = parser.parse_args(argv)

    try:
        import inspect
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import predict
    except ImportError as exc:
        print(f"transcription_roundtrip : dépendance manquante ({exc}).\n"
              f"  pip install basic-pitch \"numpy<2\"\n"
              f"Le cœur de Libretto reste 100 % stdlib : cette dépendance "
              f"n'est requise que par ce harnais.", file=sys.stderr)
        return 3

    base = Path(args.keep) if args.keep else Path(
        tempfile.mkdtemp(prefix="rt_transcription_"))
    gen = base / "gen"
    forge(gen, n=args.n, seed=args.seed, keep_all=True)
    for aux in gen.glob("forge_*"):
        aux.unlink()
    mids = sorted(gen.glob("candidate_*.mid"))

    model = ICASSP_2022_MODEL_PATH
    try:
        from basic_pitch.inference import Model
        model = Model(ICASSP_2022_MODEL_PATH)
    except Exception:
        pass
    has_tempo = "midi_tempo" in inspect.signature(predict).parameters

    for mode in ("naive", "informed"):
        (base / mode).mkdir(parents=True, exist_ok=True)
    (base / "wav").mkdir(exist_ok=True)

    rows = []
    for i, m in enumerate(mids):
        wav = base / "wav" / f"{m.stem}.wav"
        bpm = render_wav(m, wav)
        for mode in ("naive", "informed"):
            kw = ({"midi_tempo": 120.0 if mode == "naive" else bpm}
                  if has_tempo else {})
            _, midi_data, _ = predict(str(wav), model, **kw)
            midi_data.write(str(base / mode / f"{m.stem}.mid"))

        def stats(c):
            return None if c is None else (c.score, c.confidence, c.level)
        rows.append({
            "id": m.stem,
            "orig": stats(judge_midi(m, i)),
            "naive": stats(judge_midi(base / "naive" / f"{m.stem}.mid", i)),
            "informed": stats(judge_midi(base / "informed" / f"{m.stem}.mid", i)),
        })
        print(f"  {m.stem} : rendu, transcrit ×2, re-jugé", file=sys.stderr)

    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)

    print("\ncandidat        | original             | naïf (120)           | informé (vrai bpm)")
    print("----------------|----------------------|----------------------|-------------------")
    for r in rows:
        def fmt(s):
            if s is None:
                return "— (aucune section)   "
            return f"{s[0]:.3f} conf {s[1]:.2f} {s[2][:4]:<4}"
        print(f"{r['id']:<15} | {fmt(r['orig'])} | {fmt(r['naive'])} | "
              f"{fmt(r['informed'])}")

    usable = [r for r in rows if r["orig"] and r["naive"] and r["informed"]]
    if len(usable) >= 3:
        o = [r["orig"][0] for r in usable]
        for label, key in (("naïf   ", "naive"), ("informé", "informed")):
            t = [r[key][0] for r in usable]
            dconf = sum(r["orig"][1] - r[key][1] for r in usable) / len(usable)
            print(f"\n{label} : Spearman {spearman(o, t):+.2f} | "
                  f"chute de score {sum(x - y for x, y in zip(o, t)) / len(o):+.3f} | "
                  f"chute de confiance {dconf:+.3f}"
                  + ("  ← la confiance ne voit pas le dégât" if dconf < 0.05 else ""))
        top = max(usable, key=lambda r: r["orig"][0])["id"]
        print(f"vrai n°1 : {top} | "
              f"n°1 naïf : {max(usable, key=lambda r: r['naive'][0])['id']} | "
              f"n°1 informé : {max(usable, key=lambda r: r['informed'][0])['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
