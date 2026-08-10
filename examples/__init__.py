"""Générateurs et démonstrations de Libretto.

Ces scripts restent lançables tels quels (`python3 examples/forge.py …`). Le
fichier `__init__.py` sert l'empaquetage : il permet à setuptools d'embarquer
ce dossier dans le paquet sous `libretto._generators`, afin que
`libretto serve --generate` fonctionne aussi depuis une wheel installée, sans
l'arborescence source. Voir `[tool.setuptools.package-dir]` dans pyproject.toml
et `libretto.cli._find_generators_dir`.
"""
