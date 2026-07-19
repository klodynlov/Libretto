#!/bin/sh
# Gate qualité Libretto : tests + démo + analyse E2E d'un MIDI généré.
# Échec (exit != 0) = régression. À lancer avant tout commit.
set -e
cd "$(dirname "$0")/.."

echo "── tests unitaires + e2e ──"
python3 -m unittest discover -s tests -q

echo "── gate démo (score >= 0.45) ──"
python3 -m libretto.cli demo --quiet --min-score 0.45

echo "── e2e MIDI : génération + analyse (score >= 0.40) ──"
TMP_MID="$(mktemp -d)/demo.mid"
python3 examples/make_demo.py "$TMP_MID" >/dev/null
python3 -m libretto.cli analyze "$TMP_MID" --quiet --min-score 0.40
rm -rf "$(dirname "$TMP_MID")"

echo "CHECK OK"
