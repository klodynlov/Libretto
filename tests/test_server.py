import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

from make_demo import build  # noqa: E402

from libretto.library import Library, analyze_entry  # noqa: E402
from libretto.reaper import tick_to_seconds  # noqa: E402
from libretto.server import (analyze_bytes, push_library_path,  # noqa: E402
                             search_library, serve)


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


class TestSearchServer(unittest.TestCase):
    """Interface web avec bibliothèque : l'onglet recherche s'active, la
    recherche classe les entrées, et « ▶ Reaper » pousse une entrée désignée
    par son chemin — jamais un fichier arbitraire hors de l'index."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        mid = base / "demo.mid"
        build(mid)
        cls.mid_path = str(mid.resolve())
        cls.libpath = base / "lib.json"
        lib = Library()
        lib.add(analyze_entry(mid, tags=["demo"]))
        lib.save(cls.libpath)

        cls.httpd = serve(port=0, lib_path=str(cls.libpath))
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

    def _post(self, path, payload):
        c = self._conn()
        c.request("POST", path, body=json.dumps(payload),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_search_panel_and_flag(self):
        c = self._conn()
        c.request("GET", "/")
        self.assertIn("searchpanel", c.getresponse().read().decode())
        c.request("GET", "/api/analyses")
        self.assertTrue(json.loads(c.getresponse().read())["has_library"])

    def test_search_returns_ranked_hits(self):
        status, data = self._post("/api/search", {"query": "mélancolique"})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["empty"])
        self.assertEqual(data["count"], 1)
        hit = data["entries"][0]
        self.assertTrue(hit["name"].endswith("demo.mid"))
        self.assertIsNotNone(hit["distance"])   # intention exprimée → distance
        self.assertIn("descriptors", hit)

    def test_search_empty_query_rejected(self):
        status, _ = self._post("/api/search", {"query": "   "})
        self.assertEqual(status, 400)

    def test_reaper_path_must_be_in_library(self):
        status, data = self._post("/api/reaper", {"path": "/pas/dans/index.mid"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_reaper_known_path_reaches_bridge(self):
        # chemin valide de l'index : on dépasse la validation, seul le pont
        # manque (REAPER non lancé en test) → 502, pas 400.
        status, data = self._post("/api/reaper", {"path": self.mid_path})
        self.assertEqual(status, 502, data)
        self.assertIn("error", data)

    def test_search_library_direct(self):
        res = search_library(str(self.libpath), "joyeux lumineux")
        self.assertEqual(res["count"], 1)
        self.assertFalse(res["empty"])

    def test_push_unknown_path_raises(self):
        with self.assertRaises(ValueError):
            push_library_path(str(self.libpath), "/nope.mid")


def _fake_generator(params, workdir):
    """Générateur factice : écrit un vrai MIDI (make_demo) et renvoie le
    contrat attendu par le serveur, sans lancer Forge."""
    p = Path(workdir) / "gen_winner.mid"
    build(p)
    return {"results": [{"path": str(p), "name": p.name, "role": "winner",
                         "score": 0.71, "confidence": 0.9,
                         "confidence_level": "élevée", "form": "AABA"}],
            "summary": {"n_generated": int(params.get("n", 1)),
                        "n_eligible": 1, "n_rejected": 0}}


class TestGenerateServer(unittest.TestCase):
    """Panneau de génération : le serveur appelle un rappel `generator` fourni
    de l'extérieur (le cœur ignore les générateurs), enregistre les fichiers
    produits, et n'autorise download / Reaper / indexation QUE sur eux."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.libpath = Path(cls._tmp.name) / "lib.json"
        Library().save(cls.libpath)
        cls.httpd = serve(port=0, generator=_fake_generator,
                          gen_modes=["procedural"], lib_path=str(cls.libpath))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)

    def _post(self, path, payload):
        c = self._conn()
        c.request("POST", path, body=json.dumps(payload),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_flags_and_modes(self):
        c = self._conn()
        c.request("GET", "/api/analyses")
        d = json.loads(c.getresponse().read())
        self.assertTrue(d["can_generate"])
        self.assertEqual(d["gen_modes"], ["procedural"])

    def test_generate_returns_results(self):
        status, d = self._post("/api/generate",
                               {"mode": "procedural", "n": 3, "shortlist": 2})
        self.assertEqual(status, 200, d)
        self.assertEqual(len(d["results"]), 1)
        self.assertEqual(d["results"][0]["role"], "winner")
        self.assertEqual(d["summary"]["n_generated"], 3)

    def test_download_only_generated(self):
        _s, d = self._post("/api/generate", {"n": 1})
        path = d["results"][0]["path"]
        c = self._conn()
        from urllib.parse import quote
        c.request("GET", "/api/download?path=" + quote(path))
        r = c.getresponse()
        body = r.read()
        self.assertEqual(r.status, 200)
        self.assertEqual(body[:4], b"MThd")
        # chemin arbitraire refusé
        c = self._conn()
        c.request("GET", "/api/download?path=/etc/passwd")
        self.assertEqual(c.getresponse().status, 404)

    def test_library_add_generated(self):
        _s, d = self._post("/api/generate", {"n": 1})
        path = d["results"][0]["path"]
        status, res = self._post("/api/library_add", {"path": path})
        self.assertEqual(status, 200, res)
        self.assertIn("descriptors", res)
        # non généré → refusé
        status, _ = self._post("/api/library_add", {"path": "/etc/passwd"})
        self.assertEqual(status, 400)

    def test_reaper_generated_reaches_bridge(self):
        from unittest import mock

        import libretto.reaper as reaper
        _s, d = self._post("/api/generate", {"n": 1})
        path = d["results"][0]["path"]
        spy = mock.MagicMock(return_value={
            "reaper": "t", "tracks": [], "markers": 0,
            "playing": True, "total_notes": 0})
        # le handler fait `from .reaper import push_mididata` à l'appel :
        # patcher la source suffit à intercepter un push de fichier généré.
        with mock.patch.object(reaper, "push_mididata", spy):
            status, _res = self._post("/api/reaper", {"path": path})
        self.assertEqual(status, 200)
        spy.assert_called()


class TestGenerateDisabled(unittest.TestCase):
    def test_generate_disabled_without_generator(self):
        httpd = serve(port=0)   # aucun generator
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/api/analyses")
            self.assertFalse(json.loads(c.getresponse().read())["can_generate"])
            c.request("POST", "/api/generate", body=b"{}",
                      headers={"Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestSearchDisabledWithoutLibrary(unittest.TestCase):
    def test_no_library_disables_search(self):
        httpd = serve(port=0)   # sans --lib
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/api/analyses")
            self.assertFalse(json.loads(c.getresponse().read())["has_library"])
            c.request("POST", "/api/search", body=json.dumps({"query": "x"}),
                      headers={"Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


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
