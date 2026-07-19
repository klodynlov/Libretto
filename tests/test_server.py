import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from make_demo import build  # noqa: E402

from libretto.reaper import tick_to_seconds  # noqa: E402
from libretto.server import analyze_bytes, serve  # noqa: E402


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        mid = Path(cls._tmp.name) / "demo.mid"
        build(mid)
        cls.mid_bytes = mid.read_bytes()
        cls.httpd = serve(port=0)  # port éphémère
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def test_index_served(self):
        c = self._conn()
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn("Libretto", body)
        self.assertIn("Glisse tes .mid", body)

    def test_analyze_roundtrip(self):
        c = self._conn()
        c.request("POST", "/api/analyze", body=self.mid_bytes,
                  headers={"X-Filename": "demo.mid"})
        r = c.getresponse()
        data = json.loads(r.read())
        self.assertEqual(r.status, 200, data)
        self.assertGreater(data["global_score"], 0.4)
        self.assertIn("radar", data)
        self.assertIn("chorus", data["sections"])

        c.request("GET", "/api/analyses")
        listing = json.loads(c.getresponse().read())
        self.assertGreaterEqual(len(listing["entries"]), 1)

    def test_analyze_rejects_garbage(self):
        c = self._conn()
        c.request("POST", "/api/analyze", body=b"pas du midi",
                  headers={"X-Filename": "bad.mid"})
        r = c.getresponse()
        self.assertEqual(r.status, 400)
        self.assertIn("error", json.loads(r.read()))

    def test_reaper_unknown_id(self):
        c = self._conn()
        c.request("POST", "/api/reaper", body=json.dumps({"id": 99999}),
                  headers={"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 404)

    def test_analyze_bytes_direct(self):
        entry, sms = analyze_bytes(self.mid_bytes, "demo.mid")
        self.assertEqual(len(sms.axes), 29)
        self.assertEqual(entry["key"], "C")


class TestTickToSeconds(unittest.TestCase):
    def test_constant_tempo(self):
        to_sec = tick_to_seconds([(0, 120.0)], ppq=480)
        self.assertAlmostEqual(to_sec(480), 0.5)   # 1 temps @120 = 0.5 s
        self.assertAlmostEqual(to_sec(1920), 2.0)  # 1 mesure 4/4

    def test_tempo_change(self):
        # 120 puis 60 BPM au tick 480 : temps 2 dure 1 s
        to_sec = tick_to_seconds([(0, 120.0), (480, 60.0)], ppq=480)
        self.assertAlmostEqual(to_sec(480), 0.5)
        self.assertAlmostEqual(to_sec(960), 1.5)

    def test_empty_map_defaults(self):
        to_sec = tick_to_seconds([], ppq=480)
        self.assertAlmostEqual(to_sec(960), 1.0)  # 120 BPM par défaut


if __name__ == "__main__":
    unittest.main()
