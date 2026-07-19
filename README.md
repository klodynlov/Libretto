# Libretto — Sense of Musical Structure (SMS)

Analyse structurelle **symbolique** d'une œuvre musicale : 29 axes pondérés
(forme, harmonie, mélodie, rythme, texture, cohérence), chacun normalisé
[0, 1], agrégés en un score SMS global. **100 % stdlib Python** — aucune
dépendance, tout tourne en local.

```
MIDI (.mid) ──parse──▶ MidiData ──build──▶ Score symbolique ──SMS──▶ 29 axes ──▶ rapport
```

## Usage

```bash
# analyse d'un fichier MIDI (SMF format 0/1)
python3 -m libretto.cli analyze chanson.mid
python3 -m libretto.cli analyze chanson.mid --html rapport.html --json rapport.json

# gate CI : exit 2 si le score global est sous le seuil
python3 -m libretto.cli analyze chanson.mid --min-score 0.5

# partition de démonstration intégrée (aucun fichier requis)
python3 -m libretto.cli demo

# installation optionnelle de la commande `libretto`
pip install -e .
```

En bibliothèque :

```python
from libretto import SenseOfMusicalStructure, parse_midi, build_score

score = build_score(parse_midi("chanson.mid"))
sms = SenseOfMusicalStructure(score)   # ou un Score construit à la main
sms.calculate()
print(sms.summary())                    # rapport texte
print(sms.get_score())                  # score global [0, 1]
data = sms.to_dict()                    # axes + détails, sérialisable
```

## Les 6 groupes d'axes

| Groupe | Axes | Contenu |
|---|---|---|
| A · Forme & architecture | 1-7 | nb sections, équilibre, diversité, symétrie, progression, répétition, transitions |
| B · Harmonie & tonalité | 8-14 | tonalité (Krumhansl-Kessler), complexité, cadences, modulations, rythme harmonique, contraste (cycle des quintes), basse |
| C · Mélodie & thème | 15-20 | contour, motifs (invariants par transposition), thème, tessiture, intervalles, syncopes |
| D · Rythme & tempo | 21-24 | variation/cohérence tempo, complexité rythmique, carrure hypermétrique |
| E · Texture & orchestration | 25-27 | variété texturale, gamme dynamique, polyphonie |
| F · Cohérence globale | 28-29 | arc émotionnel (arche ou montée), synthèse inter-groupes |

Pondérations centralisées dans `libretto/axes.py::AXES_META`, somme exacte
= 1.0 (vérifiée par les tests). Tous les scores sont bornés par un clamp
central dans `StructuralAxis`.

## Calibration des poids (contrastive, auto-supervisée)

Pas besoin de corpus annoté : chaque MIDI réel est un positif, ses versions
**dégradées** (mesures permutées, segments transposés, vélocités aplaties,
attaques décalées, mélodie brouillée) sont des négatifs par construction.
`calibrate` cherche les poids qui maximisent la marge
score(original) − score(dégradé), par hill climbing sur le simplexe
(somme = 1, plancher par axe), régularisé vers les poids experts.

```bash
python3 -m libretto.cli calibrate mon_corpus/ --out weights.json --jobs 4
python3 -m libretto.cli analyze chanson.mid --weights weights.json
```

Les scores des 29 axes ne dépendant pas des poids, l'analyse (~10 ms/fichier)
est précalculée une seule fois (`--jobs` parallélise via multiprocessing) ;
la recherche de poids ne fait ensuite que des produits scalaires — 6000
itérations < 1 s. Le rapport JSON inclut `discrimination` (moyenne positifs
− négatifs par axe) : diagnostic direct des axes qui détectent réellement
la structure et de ceux qui récompensent le chaos. Déterministe (`--seed`).

## Interface web locale

```bash
python3 -m libretto.cli serve          # http://127.0.0.1:8787
```

Glisser-déposer des `.mid` → analyse complète (radar 6 groupes + 29 axes),
les analyses de la session s'empilent triées par score (comparateur de
fichiers/packs), et **« ▶ Reaper »** pousse le fichier dans REAPER via le
pont Klody (`127.0.0.1:9000` — pistes nommées, ReaSynth, marqueurs, lecture).
Stdlib pure (`http.server`), tout en mémoire, rien d'écrit sur disque.

En CLI directe : `python3 -m libretto.cli reaper chanson.mid [--no-play]`.

## Pipeline MIDI → Score

`libretto/midi.py` : parseur SMF pur stdlib (running status, note-on vél. 0,
tempo/signatures/marqueurs ; SMPTE rejeté) + writer minimal.
`libretto/builder.py` :

- grille de mesures suivant les changements de signature ;
- chroma pondéré (durée × vélocité) par mesure → **accord par gabarits**
  (maj/min/dim/7) ;
- mélodie = voix supérieure échantillonnée par temps ;
- sections depuis les **marqueurs MIDI** (`Intro`, `Couplet`, `Refrain`,
  `Pont`… — anglais et français normalisés), sinon **segmentation par
  nouveauté** sur le chroma + étiquetage heuristique (cluster répété le plus
  énergique = chorus, etc.) ;
- vélocité moyenne, polyphonie, densité et onsets par section (alimentent
  les axes 20, 23, 25-28).

## Maintenance

```bash
scripts/check.sh   # tests unitaires + e2e, gate démo >= 0.45, gate MIDI >= 0.40
```

`examples/make_demo.py` génère `examples/demo.mid` (pop 40 mesures, Do
majeur, marqueurs français, batterie syncopée) — sert de fixture e2e.

## Limites connues (v0.1)

- Symbolique uniquement : pas d'audio (coupler avec une transcription type
  basic-pitch pour analyser des rendus Local Suno).
- Détection d'accords par gabarits diatoniques : les renversements sont
  fusionnés, pas d'accords enrichis au-delà des 7èmes.
- Segmentation sans marqueurs = heuristique (nouveauté chroma) : correcte
  sur les formes franches, approximative sur les formes continues.
- Pondérations expertes fixes ; apprentissage des poids (régression sur un
  corpus noté) prévu en v0.2.
