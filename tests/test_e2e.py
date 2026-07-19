import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from make_demo import build  # noqa: E402

from libretto.axes import SenseOfMusicalStructure  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.cli import main as cli_main  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.mid_path = Path(cls._tmp.name) / "demo.mid"
        build(cls.mid_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_builder_extracts_structure(self):
        score = build_score(parse_midi(self.mid_path))
        self.assertGreaterEqual(len(score.sections), 6)
        labels = [s.label for s in score.sections]
        # Marqueurs français normalisés : Couplet→verse, Refrain→chorus, Pont→bridge.
        self.assertIn("verse", labels)
        self.assertIn("chorus", labels)
        self.assertIn("bridge", labels)
        self.assertEqual(labels[0], "intro")
        # Do majeur attendu
        self.assertEqual(score.key_signature.pc, 0)
        # Données mesurées présentes
        for s in score.sections:
            self.assertGreater(s.mean_velocity, 0)
            self.assertGreater(s.note_density, 0)
        # Le refrain doit être plus énergique que le couplet
        verse_vel = max(s.mean_velocity for s in score.sections if s.label == "verse")
        chorus_vel = max(s.mean_velocity for s in score.sections if s.label == "chorus")
        self.assertGreater(chorus_vel, verse_vel)

    def test_full_analysis_bounded(self):
        score = build_score(parse_midi(self.mid_path))
        sms = SenseOfMusicalStructure(score)
        for ax in sms.calculate():
            self.assertGreaterEqual(ax.score, 0.0, ax.id)
            self.assertLessEqual(ax.score, 1.0, ax.id)
        self.assertGreater(sms.get_score(), 0.4)

    def test_cli_analyze_with_reports(self):
        html = Path(self._tmp.name) / "report.html"
        json_out = Path(self._tmp.name) / "report.json"
        code = cli_main(["analyze", str(self.mid_path), "--quiet",
                         "--html", str(html), "--json", str(json_out)])
        self.assertEqual(code, 0)
        html_text = html.read_text(encoding="utf-8")
        self.assertIn("Sense of Musical Structure", html_text)
        self.assertEqual(html_text.count("<tr>"), 30)  # 1 en-tête + 29 axes
        self.assertIn('"global_score"', json_out.read_text(encoding="utf-8"))

    def test_cli_gate(self):
        self.assertEqual(cli_main(["analyze", str(self.mid_path), "--quiet",
                                   "--min-score", "0.2"]), 0)
        self.assertEqual(cli_main(["analyze", str(self.mid_path), "--quiet",
                                   "--min-score", "0.99"]), 2)

    def test_cli_demo(self):
        self.assertEqual(cli_main(["demo", "--quiet", "--min-score", "0.4"]), 0)

    def test_cli_missing_file(self):
        self.assertEqual(cli_main(["analyze", "/nonexistent/x.mid", "--quiet"]), 1)

    def test_loop_tail_merged_into_single_section(self):
        # Loop 8 mesures sans marqueurs dont la dernière note déborde :
        # la queue résiduelle ne doit pas devenir une section à part.
        from libretto.midi import write_midi
        path = Path(self._tmp.name) / "loop.mid"
        notes = []
        for bar in range(8):
            root = 64 if bar % 2 == 0 else 62  # vamp Em/Dm
            for iv in (0, 3, 7, 10):
                notes.append((bar * 4.0, 3.8, root + iv, 70, 0))
        notes.append((28.0, 6.0, 76, 55, 0))  # déborde en "mesure 9"
        write_midi(path, [notes], bpm=102)
        score = build_score(parse_midi(path))
        self.assertEqual(len(score.sections), 1)
        self.assertGreaterEqual(score.sections[0].n_bars, 8)


if __name__ == "__main__":
    unittest.main()
