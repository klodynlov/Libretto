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
itérations < 1 s. Déterministe (`--seed`).

## Validation : ce que les chiffres veulent dire

L'accuracy d'entraînement monte toujours — avec 29 poids libres et quelques
centaines de paires, elle mesure surtout la capacité à mémoriser le corpus.
`calibrate` rapporte donc une **validation croisée à 5 plis, découpés par
fichier** (`validation.test_accuracy_mean`). Le split est au niveau du
fichier et non de la paire : les ~10 négatifs d'un fichier partagent son
vecteur positif, donc découper par paire mettrait le même positif des deux
côtés — fuite garantie. `overfit_gap` (train − test) et `gain_vs_expert`
disent respectivement ce qui est mémorisé et ce que la calibration apporte
vraiment.

Le rapport donne aussi, par axe, l'**AUC appariée** — la probabilité qu'un
original batte sa version dégradée :

| AUC | Lecture |
|---|---|
| > 0.5 | l'axe détecte la structure |
| ≈ 0.5 | l'axe est aveugle à cette dégradation (souvent normal : rien ne devrait faire réagir l'axe du tempo à un brouillage de hauteurs) |
| **< 0.5** | **l'axe vote pour le chaos** — il est à l'envers, et aucun réglage de poids ne le répare |

`axis_auc_by_degradation` ventile cette AUC par type de dégradation, ce qui
distingue « aveugle parce que hors sujet » de « inversé ». C'est ce
diagnostic qui a montré que **8 axes sur 29 étaient inversés** (v0.1 : de
0.25 à 0.42), tous pour la même raison — ils confondaient désordre et
richesse : une suite d'accords tirés au sort a plus de « complexité
harmonique » qu'un I-vi-ii-V, des attaques décalées au hasard plus de
« complexité rythmique » qu'un groove. Chacun a été reformulé pour mesurer
la richesse **sous contrainte de cohérence** (voir les docstrings de
`axes.py`, qui gardent la trace de l'erreur d'origine).

Résultats, poids experts sans calibration (accuracy de validation croisée) :

| Corpus | v0.1 | v0.2 |
|---|---|---|
| Morceaux structurés (60, générés) | 0.836 | **0.929** |
| Pack de loops sloopy (49) | 0.521 | **0.823** |
| Loops bruts mono-instrument (140) | 0.420 | 0.603 |

Le dernier chiffre est la limite honnête du moteur : sur un loop de
percussion de 4 mesures, il n'y a ni forme, ni harmonie, ni mélodie à
mesurer, et le score global n'a pas de sens. Libretto analyse des
**morceaux**.

## Fiabilité : savoir quand le score ne veut rien dire

Un score de 0.62 sur un morceau de cinq minutes et un 0.62 sur une boucle de
kick de quatre mesures n'affirment pas la même chose — la v0.2 les affichait
pourtant à l'identique. Chaque axe rapporte désormais une **fiabilité**
[0, 1] : la matière dont il disposait réellement. Un axe qui retourne 0.0
faute de données n'est plus indiscernable d'un axe qui a mesuré un vrai
zéro.

```
SCORE GLOBAL SMS: 0.23
FIABILITÉ: 0.10 (insuffisante)
  matière manquante : accords 0/16, notes mélodiques 16/48, sections 1/6
  ⚠ Le score global n'est PAS interprétable : au-dessous de 0.55 de
    fiabilité, il ne fait pas mieux que le hasard.
    Trop peu de matière harmonique ou mélodique : boucle courte ou pièce
    mono-instrument, plutôt qu'un morceau construit.
```

Le seuil de 0.55 n'est pas un réglage d'humeur : c'est l'accuracy
contrastive réellement observée par tranche, sur 200 fichiers.

| Fiabilité annoncée | n | Accuracy réelle |
|---|---|---|
| élevée ≥ 0.75 | 42 | 0.94 |
| moyenne ≥ 0.55 | 13 | 0.94 |
| faible ≥ 0.35 | 18 | **0.33** |
| insuffisante < 0.35 | 127 | 0.51 |

Corrélation entre fiabilité annoncée et accuracy réelle : **r = 0.59**.
L'indicateur prédit donc ce qu'il prétend prédire — c'était la condition
pour qu'il vaille mieux qu'une décoration.

Fait contre-intuitif que la mesure a imposé : la tranche « faible » est la
**pire**, sous le hasard, et non un juste milieu. Le fichier y offre assez
de matière pour que les axes s'engagent, pas assez pour qu'ils aient
raison. D'où le seuil d'alerte à 0.55 et non à 0.35.

Le diagnostic distingue les causes, parce qu'elles appellent des réponses
opposées : « 0 accord, 16 notes » désigne une boucle rythmique ; « 1 section
au lieu de 6 » désigne une segmentation ratée, que des marqueurs MIDI
corrigent. Gate CI : `analyze --min-confidence 0.55` (sortie 3, distincte
du 2 de `--min-score`, pour que la CI sache lequel a lâché).

## Corpus de validation

Valider demande des morceaux, et les packs du commerce n'en contiennent pas.
`examples/make_corpus.py` en génère : formes variées (verse-chorus, AABA,
rondo, binaire, à travers-composé), modes (majeur, mineur, dorien,
mixolydien), **mesures composées et impaires** (3/4, 6/8, 12/8, 5/4),
sections de 7 ou 10 mesures hors carrure, arcs plats ou descendants,
modulations vers des tonalités voisines, orchestration variable par section,
et la moitié des fichiers sans marqueurs pour exercer la segmentation.

```bash
python3 examples/make_corpus.py corpus/ 60 7    # 60 morceaux, graine 7
```

Un corpus synthétique ne prouve pas que le score SMS corrèle avec le goût
humain — seule une écoute annotée le ferait. Il prouve ce que la calibration
contrastive demande : qu'un morceau structuré batte sa propre version
dégradée. Cette question résiste à la synthèse, puisque la dégradation
s'applique au fichier généré lui-même. Le générateur varie délibérément
au-delà de la zone de confort des axes, y compris là où ils sont faibles.

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
- vélocité moyenne, polyphonie **par mesure**, densité et onsets par section
  (alimentent les axes 20, 23, 25-28) ;
- **contexte métrique** dérivé du chiffrage : un chiffrage composé (6/8,
  9/8, 12/8) se bat à la noire pointée avec une subdivision ternaire. Les
  attaques sont exprimées en phase de pulsation, ce qui rend les axes 20 et
  23 justes hors du 4/4 — la v0.1 raisonnait en noires partout et comptait
  comme syncopées les croches ternaires, qui sont sur le temps.

## Maintenance

```bash
scripts/check.sh   # tests, gate démo >= 0.45, gate MIDI >= 0.40, gate AUC
```

Le dernier étage est celui qui compte : il génère un corpus de 40 morceaux
et **échoue si un axe repasse sous AUC 0.45**, c'est-à-dire s'il se remet à
préférer une version dégradée. Seuil à 0.45 et non 0.50 parce que les axes
qu'aucune dégradation ne cible flottent autour de 0.5 par bruit
d'échantillon : mesuré sur 4 graines × 40 fichiers, ce plancher ne descend
jamais sous 0.480, quand les axes réellement inversés se tenaient entre 0.25
et 0.42.

`examples/make_demo.py` génère `examples/demo.mid` (pop 40 mesures, Do
majeur, marqueurs français, batterie syncopée) — sert de fixture e2e.

## Limites connues (v0.2)

- **Loops ≠ morceaux.** Sur du matériel court et mono-instrument, le score
  global n'a pas de sens (validation à 0.60, contre 0.93 sur des morceaux) :
  il n'y a pas de structure à mesurer. C'est désormais signalé (voir
  *Fiabilité*), mais le moteur ne sait toujours pas analyser ce matériel —
  il sait seulement dire qu'il ne sait pas.
- Symbolique uniquement : pas d'audio (coupler avec une transcription type
  basic-pitch pour analyser des rendus Local Suno).
- Détection d'accords par gabarits diatoniques : un accord par mesure au
  maximum, renversements fusionnés, rien au-delà des 7èmes. L'axe 12 est
  plafonné par cette résolution.
- Mélodie = voix supérieure échantillonnée par temps : attrape les sommets
  d'arpèges d'accompagnement, et le balayage est quadratique (lent sur les
  MIDI orchestraux denses).
- Segmentation sans marqueurs = nouveauté locale, sans matrice
  d'auto-similarité ni détection de répétitions, et jamais évaluée contre
  des annotations (type SALAMI). Les axes **03** (diversité des labels) et
  **06** (ratio de répétition) restent à AUC ≈ 0.47 pour cette raison : une
  transposition aléatoire fait sur-segmenter, ce qui gonfle artificiellement
  la diversité des sections.
- Esthétique pop inscrite dans les bandes de tolérance : un nocturne ou une
  pièce ambient scorent bas par construction. Les profils de poids
  (`weights_*.json`) atténuent, ils ne suppriment pas.
- Corpus de validation synthétique : il valide la cohérence interne
  (original > dégradé), pas l'accord avec un jugement humain.
