"""Timing evidence: expressive patterns are measured, never repaired."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from libretto.cli import main
from libretto.groove import analyze_groove
from libretto.midi import MidiData, MidiNote, write_mididata
from libretto.server import analyze_bytes


def pattern(offbeat=240, delay=0, tempo=120):
    md = MidiData(ppq=480, tempos=[(0, tempo)], time_sigs=[(0, 4, 4)], end_tick=3840)
    for beat in range(8):
        for position in (0, offbeat):
            t = beat * 480 + position + delay
            md.notes.append(MidiNote(t, t + 30, 42, 65 if position else 95, 9, 0))
    return md


class TestGroove(unittest.TestCase):
    def test_straight_and_swung_pairs_are_distinguished(self):
        straight = analyze_groove(pattern())
        swung = analyze_groove(pattern(offbeat=320))
        self.assertEqual(straight["swing"]["long_percent"], 50)
        self.assertEqual(swung["swing"]["long_short_ratio"], 2)
        self.assertEqual(swung["swing"]["pairs"], 7)
        # Good swing remains expressively off the binary sixteenth reference.
        self.assertGreater(swung["voices"][0]["spread_ms"], 0)

    def test_no_quantization_or_midi_mutation(self):
        md = pattern(offbeat=320, delay=7)
        before = copy.deepcopy(md)
        analyze_groove(md)
        self.assertEqual(md, before)

    def test_signed_offset_and_tempo_map_in_milliseconds(self):
        md = pattern(delay=12)
        self.assertEqual(analyze_groove(md)["median_offset_ms"], 12.5)
        md.tempos = [(0, 60)]
        self.assertEqual(analyze_groove(md)["median_offset_ms"], 25)
        md.tempos = [(0, 120), (1920, 60)]
        voice = analyze_groove(md)["voices"][0]
        self.assertEqual(voice["median_offset_ms"], 18.75)
        self.assertEqual(voice["spread_ms"], 6.25)

    def test_pitched_notes_are_not_misclassified_as_drums(self):
        md = pattern()
        for note in md.notes:
            note.channel = 0
        report = analyze_groove(md)
        self.assertFalse(report["available"])
        self.assertIsNone(report["swing"])

    def test_insufficient_evidence_does_not_produce_zero_swing(self):
        md = pattern()
        md.notes = md.notes[:4]
        self.assertIsNone(analyze_groove(md)["swing"])

    def test_compound_and_changing_meters_have_separate_grids(self):
        md = pattern()
        md.time_sigs = [(0, 6, 8), (1440, 3, 4)]
        grids = analyze_groove(md)["voices"][0]["grids"]
        self.assertEqual({g["meter"]: len(g["hits"]) for g in grids},
                         {"6/8": 12, "3/4": 12})
        md.time_sigs = [(0, 6, 8)]
        self.assertIsNone(analyze_groove(md)["swing"])

    def test_sixteenth_hat_pattern_is_not_invented_swing(self):
        md = pattern()
        for beat in range(8):
            for pos in (120, 360):
                t = beat * 480 + pos
                md.notes.append(MidiNote(t, t + 20, 42, 70, 9, 0))
        self.assertIsNone(analyze_groove(md)["swing"])

    def test_cli_and_upload_work_on_drum_only_loop(self):
        with tempfile.TemporaryDirectory() as d:
            midi, out = Path(d) / "drums.mid", Path(d) / "groove.json"
            write_mididata(midi, pattern(offbeat=320))
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["groove", str(midi), "--json", str(out)]), 0)
            self.assertIn("66.7", stdout.getvalue())
            self.assertTrue(json.loads(out.read_text())["available"])
            entry, sms = analyze_bytes(midi.read_bytes(), "drums.mid")
            self.assertFalse(entry["interpretable"])
            self.assertTrue(entry["groove"]["available"])
            self.assertEqual(len(sms.axes), 29)
