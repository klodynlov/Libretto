"""Empaquetage des générateurs — le bug « No module named 'forge' ».

Les générateurs (forge, make_corpus, markov_gen, forge_markov) vivent dans
`examples/`, hors du paquet `libretto`. Une wheel installée ne les embarquait
pas : `libretto serve --generate` faisait alors `import forge` et échouait par
`ModuleNotFoundError` dès qu'on lançait la commande ailleurs que depuis les
sources.

Le correctif a deux moitiés, testées ici :
- empaquetage : `examples/` est embarqué sous `libretto/_generators/` (config
  pyproject) ;
- exécution : `_find_generators_dir` trouve le bon dossier selon
  l'installation (source/éditable → `examples/`, wheel → `_generators/`), et
  `_build_generator` échoue proprement (ValueError, pas un traceback brut) si
  aucun n'existe.

Tout est stdlib : pas de build de wheel ici (la CI est 100 % stdlib). La
présence réelle des modules dans la wheel découle de la config vérifiée par
`test_pyproject_embeds_generators`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import libretto  # noqa: E402
from libretto.cli import _build_generator, _find_generators_dir  # noqa: E402

GEN_MODULES = ("forge", "make_corpus", "markov_gen", "forge_markov")


class TestGeneratorResolution(unittest.TestCase):
    """`_find_generators_dir` : la logique qui rend l'import robuste."""

    def test_prefers_source_examples(self):
        # Source / éditable : les deux emplacements existent, on préfère la
        # source (`examples/`) pour rester aligné sur le dépôt vivant.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "examples").mkdir()
            (base / "examples" / "forge.py").write_text("")
            (base / "libretto" / "_generators").mkdir(parents=True)
            (base / "libretto" / "_generators" / "forge.py").write_text("")
            self.assertEqual(
                _find_generators_dir(base / "libretto"), base / "examples")

    def test_falls_back_to_embedded(self):
        # Wheel : pas de `examples/`, mais `libretto/_generators/` embarqué.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "libretto" / "_generators").mkdir(parents=True)
            (base / "libretto" / "_generators" / "forge.py").write_text("")
            self.assertEqual(
                _find_generators_dir(base / "libretto"),
                base / "libretto" / "_generators")

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_find_generators_dir(Path(d) / "libretto"))

    def test_real_source_tree_resolves_all_modules(self):
        # Dans ce dépôt (exécution depuis les sources), les quatre modules
        # doivent être trouvés, sinon le pont serveur est cassé.
        pkg = Path(libretto.__file__).resolve().parent
        found = _find_generators_dir(pkg)
        self.assertIsNotNone(found)
        for mod in GEN_MODULES:
            self.assertTrue((found / f"{mod}.py").is_file(),
                            f"{mod}.py absent de {found}")


class TestBuildGeneratorErrors(unittest.TestCase):
    def test_clean_error_when_generators_missing(self):
        # Aucun dossier trouvé → ValueError claire (rattrapée par la CLI en un
        # message « génération indisponible »), jamais un ModuleNotFoundError nu.
        with mock.patch("libretto.cli._find_generators_dir", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                _build_generator(None)
        self.assertIn("générateurs introuvables", str(ctx.exception))


class TestPackagingConfig(unittest.TestCase):
    def test_pyproject_embeds_generators(self):
        # Garde-fou d'empaquetage : si cette config saute, la wheel cesse
        # d'embarquer les générateurs et le bug d'origine revient.
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("tomllib requiert Python 3.11+")
        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
        setuptools_cfg = data["tool"]["setuptools"]
        self.assertIn("libretto._generators", setuptools_cfg["packages"])
        self.assertEqual(
            setuptools_cfg["package-dir"]["libretto._generators"], "examples")


if __name__ == "__main__":
    unittest.main()
