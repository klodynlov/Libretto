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

echo "── calibration contrastive (accuracy >= 0.6 sur la démo) ──"
python3 -m libretto.cli calibrate "$(dirname "$TMP_MID")" \
    --out "$(dirname "$TMP_MID")/weights.json" --variants 2 --iters 500 >/dev/null
python3 -m libretto.cli analyze "$TMP_MID" --quiet \
    --weights "$(dirname "$TMP_MID")/weights.json" --min-score 0.40
rm -rf "$(dirname "$TMP_MID")"

# Le gate qui compte vraiment. Un axe sous AUC 0.5 vote pour la version
# dégradée d'un morceau : il ne mesure pas mal la structure, il la mesure à
# l'envers, et aucun réglage de poids ne le répare. 8 des 29 axes étaient
# dans cet état avant d'être mesurés ; ce gate les empêche d'y revenir.
# Le corpus est généré à la volée (déterministe) : rien à versionner.
#
# Seuil 0.45 et non 0.50 : les axes qu'aucune dégradation ne cible — tempo,
# labels de forme — flottent autour de 0.5 par simple bruit d'échantillon.
# Mesuré sur 4 graines × 40 fichiers, ce plancher de bruit ne descend jamais
# sous 0.480, quand les axes réellement inversés se tenaient entre 0.25 et
# 0.42. 0.45 sépare les deux sans déclencher de faux positif.
echo "── validation sur corpus généré (aucun axe sous AUC 0.45) ──"
TMP_CORPUS="$(mktemp -d)"
python3 examples/make_corpus.py "$TMP_CORPUS" 40 7 >/dev/null
python3 -m libretto.cli calibrate "$TMP_CORPUS" \
    --out "$TMP_CORPUS/weights.json" --variants 2 --iters 800 --jobs 4 \
    --min-auc 0.45 --min-test-accuracy 0.80
rm -rf "$TMP_CORPUS"

echo "CHECK OK"
