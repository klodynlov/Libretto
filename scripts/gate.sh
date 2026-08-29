#!/usr/bin/env bash
# gate.sh — CONTRAT (C3) : mesure la PROMESSE de Libretto = analyse structurelle
# sur 29 axes d'un morceau de référence, score global au-dessus du plancher.
# Déterministe (corpus généré à la volée), 100 % stdlib, offline, rapide (~qq s).
# Gate léger pour le nightly ; `scripts/check.sh` reste la QA lourde pré-commit.
#
# exit 0 = promesse tenue · 1 = régression (score/structure) · 2 = erreur d'exécution.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"

# 1) Promesse cœur : la démo génère un morceau connu, l'analyse sur 29 axes et
#    vérifie le score global. Codes libretto : 0 = OK, 2 = score < seuil, 3 = confiance < seuil.
if ! "$PY" -m libretto.cli demo --quiet --min-score 0.45; then
    echo "❌ démo sous le plancher (score/confiance) — régression structurelle"
    exit 1
fi

# 2) Complétude : les 29 axes sont TOUS produits (pas un sous-ensemble dégradé),
#    et le score global reste au-dessus du plancher démo (0.40, cf. scripts/check.sh).
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
"$PY" examples/make_demo.py "$TMP/demo.mid" >/dev/null || { echo "❌ génération démo KO"; exit 2; }
"$PY" -m libretto.cli analyze "$TMP/demo.mid" --quiet --json "$TMP/out.json" --min-score 0.40 \
    || { echo "❌ analyse de la démo sous 0.40 — régression"; exit 1; }
"$PY" - "$TMP/out.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
n = len(d.get("axes", []))
g = d.get("global_score")
if n != 29:
    print(f"❌ axes attendus 29, obtenus {n}"); sys.exit(1)
if not isinstance(g, (int, float)) or g < 0.40:
    print(f"❌ global_score invalide/sous plancher : {g}"); sys.exit(1)
print(f"✅ 29 axes produits, global_score={g:.3f}")
PYEOF

echo "✅ Libretto gate OK (promesse : analyse structurelle 29 axes)"
exit 0
