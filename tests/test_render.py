"""
Tests du rendu instrumental (FluidSynth) et de son intégration au protocole
d'écoute.

Les tests qui exigent FluidSynth + une banque sont sautés proprement quand
l'outil manque (CI, machine nue) : le rendu instrumental est un étage
OPTIONNEL du banc d'essai, pas une dépendance du paquet. Ce qui ne dépend
pas de l'outil — écriture SMF avec programmes, cache, absence de fuite —
est testé partout.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libretto.annotate import _WavCache, build_tasks, midi_pair
from libretto.midi import parse_midi, write_midi, write_mididata
from libretto.render import (
    GM_PROGRAMS,
    render_wav,
    renderer_available,
)

AVAILABLE = renderer_available()


def _corpus(tmp: Path, n: int = 2) -> Path:
    for i in range(n):
        notes = []
        for bar in range(8):
            vel = 45 + (bar % 4) * 18
            for iv in (0, 4, 7):
                notes.append((bar * 4.0, 3.6, 60 + i + iv, vel, 0))
            notes.append((bar * 4.0, 1.8, 36 + i, vel, 1))
            notes.append((bar * 4.0 + 2.0, 0.4, 38, 90, 9))
        write_midi(tmp / f"p{i}.mid", [notes], ppq=480, bpm=110)
    return tmp


class TestWriteMididata(unittest.TestCase):
    def test_roundtrip_preserves_notes_and_velocities(self):
        """Les vélocités sont la variable d'expérience : l'écriture SMF ne
        doit pas les toucher d'un iota."""
        with tempfile.TemporaryDirectory() as d:
            src = _corpus(Path(d), n=1) / "p0.mid"
            md = parse_midi(src)
            out = Path(d) / "rt.mid"
            write_mididata(out, md, programs=GM_PROGRAMS)
            back = parse_midi(out)
        self.assertEqual(
            [(n.start, n.end, n.pitch, n.velocity, n.channel) for n in md.notes],
            [(n.start, n.end, n.pitch, n.velocity, n.channel) for n in back.notes])
        self.assertEqual(md.tempos, back.tempos)
        self.assertEqual(md.time_sigs, back.time_sigs)

    def test_program_changes_are_emitted(self):
        """Sans program changes, un SoundFont joue tout au piano et les
        pupitres se confondent."""
        with tempfile.TemporaryDirectory() as d:
            src = _corpus(Path(d), n=1) / "p0.mid"
            out = Path(d) / "prog.mid"
            write_mididata(out, parse_midi(src), programs={0: 0, 1: 33})
            raw = out.read_bytes()
        self.assertIn(bytes([0xC0, 0]), raw)      # canal 0 -> piano
        self.assertIn(bytes([0xC1, 33]), raw)     # canal 1 -> basse

    def test_markers_survive(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.mid"
            write_midi(path, [[(0.0, 1.0, 60, 80, 0)]], ppq=480, bpm=100,
                       markers=[(0.0, "Intro")])
            out = Path(d) / "m2.mid"
            write_mididata(out, parse_midi(path))
            self.assertEqual(parse_midi(out).markers, [(0, "Intro")])


class TestWavCacheBlindness(unittest.TestCase):
    def test_wav_urls_leak_nothing(self):
        """Les URL servies ne contiennent que l'id de tâche et le côté —
        ni dégradation, ni position de l'original, ni nom de fichier."""
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=2), seed=1, per_file=1)
        for t in tasks:
            for slot in ("A", "B"):
                url = f"/api/audio/{t['id']}/{slot}.wav"
                self.assertNotIn(t["degradation"], url)
                self.assertNotIn(t["file"], url)

    def test_cache_rejects_unknown_task_or_slot(self):
        with tempfile.TemporaryDirectory() as d:
            tasks = build_tasks(_corpus(Path(d), n=1), seed=1, per_file=1)
            cache = _WavCache(tasks, 1, "fluidsynth-inexistant", Path("x.sf2"))
            with self.assertRaises(ValueError):
                cache.wav_for(9999, "A")
            with self.assertRaises(ValueError):
                cache.wav_for(tasks[0]["id"], "C")


@unittest.skipUnless(AVAILABLE, "FluidSynth ou SoundFont absent")
class TestInstrumentRendering(unittest.TestCase):
    def test_render_produces_valid_wav(self):
        with tempfile.TemporaryDirectory() as d:
            md = parse_midi(_corpus(Path(d), n=1) / "p0.mid")
            out = render_wav(md, Path(d) / "o.wav", *AVAILABLE)
            raw = out.read_bytes()
        self.assertEqual(raw[:4], b"RIFF")
        self.assertEqual(raw[8:12], b"WAVE")
        self.assertGreater(len(raw), 100_000)

    def test_velocity_actually_changes_the_audio(self):
        """Le cœur de l'étage v3 : deux rendus qui ne diffèrent que par les
        vélocités doivent produire des signaux différents. Si la banque
        ignorait la vélocité, tout ce banc d'essai serait du théâtre."""
        with tempfile.TemporaryDirectory() as d:
            src = _corpus(Path(d), n=1) / "p0.mid"
            md_soft = parse_midi(src)
            md_loud = parse_midi(src)
            for n in md_soft.notes:
                n.velocity = 30
            for n in md_loud.notes:
                n.velocity = 115
            a = render_wav(md_soft, Path(d) / "a.wav", *AVAILABLE).read_bytes()
            b = render_wav(md_loud, Path(d) / "b.wav", *AVAILABLE).read_bytes()
        # RMS sur les échantillons 16 bits (en-tête WAV sauté)
        import struct

        def rms(raw: bytes) -> float:
            samples = struct.unpack(f"<{(len(raw) - 44) // 2}h", raw[44:44 + (len(raw) - 44) // 2 * 2])
            return (sum(s * s for s in samples) / len(samples)) ** 0.5

        self.assertGreater(rms(b), rms(a) * 1.5,
                           "la banque doit répondre à la vélocité")

    def test_control_pair_renders_identically(self):
        """Une paire de contrôle doit produire deux WAV STRICTEMENT égaux :
        le moindre écart de rendu fabriquerait une fausse différence audible
        sur des extraits censés être identiques."""
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            task = {"file": "p0.mid", "path": str(corpus / "p0.mid"),
                    "degradation": "__control__", "id": 0, "original_slot": "A"}
            binary, font = AVAILABLE
            cache = _WavCache([task], 1, binary, font)
            wav_a = cache.wav_for(0, "A").read_bytes()
            wav_b = cache.wav_for(0, "B").read_bytes()
        self.assertEqual(wav_a, wav_b)

    def test_degraded_pair_renders_differently(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = _corpus(Path(d), n=1)
            task = {"file": "p0.mid", "path": str(corpus / "p0.mid"),
                    "degradation": "shuffle_bars", "id": 0, "original_slot": "A"}
            binary, font = AVAILABLE
            cache = _WavCache([task], 1, binary, font)
            self.assertNotEqual(cache.wav_for(0, "A").read_bytes(),
                                cache.wav_for(0, "B").read_bytes())


if __name__ == "__main__":
    unittest.main()
