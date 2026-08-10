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

## Validation externe : l'oreille

Tout ce qui précède — AUC, validation croisée, F-mesure — teste la
**cohérence interne** du moteur avec ses propres hypothèses. Une hypothèse
n'a jamais été vérifiée : qu'un morceau dégradé (mesures permutées, segments
transposés, attaques décalées) soit *réellement* moins bien structuré à
l'écoute. Si l'oreille ne perçoit pas ces dégradations, la calibration
contrastive optimise contre un fantôme.

```bash
python3 -m libretto.cli annotate corpus/ --out jugements.json  # http://127.0.0.1:8788
python3 -m libretto.cli agreement jugements.json --corpus corpus/
```

`annotate` présente deux versions du même morceau — l'une originale, l'autre
dégradée — **dans un ordre tiré au sort et sans dire laquelle est laquelle**.
Lecture par synthèse Web Audio dans le navigateur : rendu rudimentaire, ce
qui convient puisqu'on juge la structure et non le timbre, et surtout aucune
dépendance ajoutée.

Ce qui rend les jugements exploitables :

- **paires de contrôle** — une sur sept oppose un morceau à lui-même. Un
  annotateur qui les tranche répond au hasard ou croit entendre ce qu'on lui
  suggère ; sans ces paires, un taux de détection élevé peut n'être qu'un
  biais de réponse ;
- **« je n'entends pas de différence » est une réponse** — forcer un choix
  binaire fabrique de l'accord artificiel ;
- **position de l'original équilibrée**, sinon on finit par la repérer sans
  rien entendre ;
- le serveur n'envoie jamais au navigateur le nom de la dégradation ni la
  position de l'original (vérifié par test).

`agreement` répond dans l'ordre : les jugements sont-ils exploitables
(contrôles) ? les dégradations s'entendent-elles, dégradation par dégradation
et avec intervalle de confiance de Wilson ? le moteur choisit-il le même côté
que l'oreille ?

### Premiers résultats — 58 comparaisons

Une session a été menée (détail et réserves dans
[`resultats_ecoute.md`](resultats_ecoute.md)). Elle a produit une
confirmation et une réfutation.

| dégradation | l'oreille désigne l'original | verdict |
|---|---|---|
| `jitter_onsets` | 100 % | audible |
| `scramble_melody` | 91 % | audible |
| `transpose_segments` | 91 % | audible |
| `shuffle_bars` | 90 % | audible |
| **`flatten_dynamics`** | **18 %** | **inversée** |

Accord moteur / oreille : **74 %**, et **88 %** en écartant
`flatten_dynamics`, sur laquelle le désaccord est concentré.

Aplatir les vélocités n'est donc pas perçu comme une dégradation : c'est
préféré, avec un intervalle de confiance entièrement sous 0.5. Une seconde
session ciblée (31 jugements) l'a **reconfirmé** — cumul 16 %, IC95
[0.06, 0.35] — et a testé une dégradation de remplacement, `scramble_dynamics`
(permuter les vélocités au lieu de les aplatir) : **non concluante** elle
aussi, IC95 [0.17, 0.69].

Les deux façons de dégrader la dynamique échouent au test. La conclusion la
plus économique n'est pas qu'elles sont mal conçues, mais que **la vélocité
MIDI, en rendu de synthèse, ne porte pas de structure perceptible** : elle
n'y module que le volume, là où un vrai instrument change aussi le timbre.
La session 3 a retesté les deux dégradations sous un rendu `v2-timbre` où
la vélocité module brillance et attaque, comme sur un instrument. Verdict :
`flatten` encore plus préférée (0/11), `scramble` au même 40 % — **la
dynamique MIDI ne porte pas de structure perceptible, sous aucun des deux
rendus**. Conséquences appliquées : poids de `26_dynamic_range` réduit à
0.010 et `28_emotional_arc` à 0.030 au profit des axes validés par
l'oreille, et la calibration n'optimise plus par défaut que contre les
quatre dégradations audibles (`--all-degradations` sinon).

Le troisième étage a tranché : `annotate --render instrument` rend chaque
côté en WAV via FluidSynth + SoundFont (outil externe optionnel — le paquet
reste stdlib), où la vélocité déclenche de vraies couches d'échantillons.
Session 4 (30 jugements) : **zéro « aucune différence »** sur les paires
réelles — la dynamique s'entend parfaitement — et l'oreille préfère la
version dégradée dans 82 % des cas. Les deux dégradations sont **inversées**
(IC95 entièrement sous 0.5) : sur de vrais échantillons, permuter les
vélocités s'entend comme une humanisation, les aplatir comme un nettoyage.
La question est close sur les trois rendus — le générateur ne produit pas
de dynamique musicale, les axes 26/28 y sont intestables. La session 5 a
rejoué le même protocole sur des **interprétations réelles**
(`examples/fetch_maestro.py` : extraits de MAESTRO, vraies vélocités de
pianistes, pédale préservée) — et tout bascule : pour la première fois,
l'oreille désigne l'original — lot complet de 55, les **deux** dégradations
audibles (`flatten` 71 % [0.51–0.85], `scramble` 73 % [0.52–0.87], global
72 %) — et l'axe 26 discrimine à **AUC 0.93** sur la matière qui contient
enfin sa grandeur. La même permutation de vélocités qui s'entendait comme
une *humanisation* sur des rampes de générateur s'entend comme une
*destruction* sur du jeu de pianiste : l'aléa n'améliore que ce qui était
déjà arbitraire. La révision de poids de la session 3 est annulée (26/28
restaurés). Détail — y compris la réserve sur les contrôles (critère
libéral mesuré, qui dilue vers le hasard mais ne gonfle pas) — dans
[`resultats_ecoute.md`](resultats_ecoute.md). Le second annotateur a
rejoué le lot à l'identique (session 5b, `agreement A.json B.json`) :
**réplication indépendante** — 71 % et 75 % de détection, verdict groupé
à IC entièrement au-dessus de 0.5 (`flatten` [0.57–0.82], `scramble`
[0.60–0.84]), et κ ≈ 0 signant la décorrélation des erreurs — deux
oreilles, un même verdict, aucun artefact partagé.

**Et le classement ?** Tout ce qui précède valide des *dégradations* : un
morceau contre sa version abîmée. Rien n'y valide un *classement* — un
morceau contre un autre — qui est pourtant l'hypothèse sur laquelle Forge
repose tout entier. Le protocole s'y prête sans rien changer : le côté
« original » devient le candidat que le moteur place devant.

```bash
python3 examples/forge_loops.py /tmp/index.json sortie/ 48 3 --keep-all
python3 examples/forge_duels.py sortie/ --out duels.json
python3 -m libretto.cli annotate duels.json --out jugements_duels.json --render instrument
python3 -m libretto.cli agreement jugements_duels.json   # sans --corpus : pas de dégradation à rejouer
```

Deux sessions, cumulées : 60 duels, 11 contrôles, 71 jugements, rendu
v3-instrument (la première, à 14 duels, ne pouvait rien conclure — sur n = 7
par tranche, seul un 7/7 aurait été significatif ; détail en session 6).

| tranche | écart SMS | l'oreille suit le moteur | IC95 | p unilatéral |
|---|---|---|---|---|
| écart fort | 0.102 – 0.206 | **18/29 = 62 %** | 0.44 – 0.77 | 0.132 |
| écart faible | 0.001 – 0.038 | 13/30 = 43 % | 0.27 – 0.61 | 0.819 |

Aucun des deux n'est significatif, et les deux ne disent pas la même chose.
**Le classement fin ne s'entend pas** : sur les écarts de quelques
centièmes — ceux qui décident du n°1 contre le n°3 — l'oreille est au
hasard, et cette fois la puissance était là (un effet réel de 0.75 aurait
été vu 8 fois sur 10). **Sur les gros écarts il y a un signal, trop faible
pour ce lot** : 62 % va dans le sens attendu, l'écart entre tranches aussi
(Fisher p = 0.119), mais établir un effet de 0.62 demanderait 106 duels par
tranche. Le coût de la question est chiffré, il n'est pas payé.

À lire avec la réserve que le harnais signale lui-même : sur 11 paires
**identiques**, l'annotateur n'a dit « aucune différence » que 3 fois. Il
n'appuie pas au hasard pour autant — 27 % de « aucune différence » sur les
paires identiques contre 1.7 % sur les duels réels (Fisher p = 0.011) — mais
chaque préférence inventée est un tirage à pile ou face qui tire le taux vers
0.5 : **les 62 % sont un plancher, pas une estimation**.

Conséquence pratique, écrite noir sur blanc : sur du loop arrangé comme sur
du transcrit, Forge est un **triage grossier**. Écarter le bas du classement
se défend ; couronner le n°1 ne se défend pas. Détail en session 7 de
[`resultats_ecoute.md`](resultats_ecoute.md).

## Tonalité : l'estimateur confronté à une étiquette qu'il n'a pas écrite

Krumhansl-Kessler (`libretto.axes.estimate_key`) alimente sept axes du
groupe B et n'avait jamais été vérifié contre une tonalité connue — sur un
corpus synthétique, retrouver la tonalité revient à retrouver ce qu'on vient
d'y écrire. Deux vérités terrain existent pourtant, et elles ne disent pas la
même chose : le corpus généré, où la tonalité est écrite **par construction**
(contrôle positif, `annotations.json` porte désormais `tonic`/`mode`), et les
packs de loops du commerce, qui l'annoncent dans le nom des fichiers et des
dossiers — une étiquette **humaine, indépendante du moteur**.

```bash
python3 examples/make_corpus.py /tmp/ctrl 240 11
python3 scripts/mesure_tonalite.py ~/Desktop/SAMPLES/MIDI --controle /tmp/ctrl
```

Deux histogrammes sont mesurés côte à côte : `pipeline`, celui que le moteur
construit vraiment, et `brut`, les notes non percussives pondérées par leur
durée. Jusqu'à la v0.3, `_pc_hist` reconstruisait l'histogramme à partir des
accords détectés (fondamentale ×2, notes d'accord ×0.5) et de la voix
supérieure (×1) — et le payait cher :

| tonique juste | contrôle généré (157) | packs de loops (433) |
|---|---|---|
| `brut` | **0.783** | 0.400 |
| `pipeline`, histogramme reconstruit | 0.567 | 0.370 |
| `brut`, tonique **+ mode** | **0.898** (108 maj/min) | 0.334 (317) |

Le moteur lit désormais les **notes brutes** (durée × vélocité, percussions
exclues), conservées par le builder section par section — voir *L'histogramme
tonal* plus bas. L'écart s'est refermé : `pipeline` passe de 0.605 à 0.783
(graine 23) et de 0.491 à 0.723 (graine 31).

Trois enseignements, dans l'ordre où ils comptent.

**Le mécanisme est sain, c'est le répertoire qui résiste.** Sur le contrôle,
`brut` trouve **57/57** des pièces en majeur. La faiblesse est ailleurs, et
elle est nette :

| mode écrit | tonique juste (`brut`) |
|---|---|
| majeur | 57/57 |
| dorien | 23/27 |
| mineur | 40/51 |
| **mixolydien** | **3/22** |

KK n'a que deux profils. Une pièce en sol mixolydien emprunte les hauteurs
de do majeur : l'estimateur répond do — d'où les 23 erreurs de
**sous-dominante**, qui sont *toute* l'erreur mixolydienne. Ce n'est pas un
réglage à corriger, c'est un profil qui manque.

**L'histogramme du moteur était moins bon que les notes brutes** — 0.567
contre 0.783 sur le contrôle, même sens sur les packs. Pondérer par les
accords détectés et la voix supérieure *retirait* de l'information tonale au
lieu d'en ajouter. C'est corrigé, et ce que ça coûte est détaillé ci-dessous.

**Sur une boucle isolée, l'estimation ne vaut rien** : 0.400, et rien ne la
sauve. Mettre en commun les boucles d'un même kit ne la remonte pas (0.36) ;
la marge KK, qui est un vrai indicateur sur des morceaux (0.783 → 0.886 à
56 % de couverture), s'aplatit sur des loops (0.400 → 0.474 à 71 %). Une
boucle de quatre mesures sur un vamp modal n'a simplement pas de quoi
trancher entre une tonalité et sa voisine — les désaccords se répartissent
sur la dominante, la sous-dominante, la relative et le bVII, c'est-à-dire
sur le voisinage diatonique, pas au hasard. Conséquence pratique pour
indexer un pack : **croire l'étiquette du pack**, et ne recourir à
l'estimateur que pour ce qui n'en a pas, en sachant que c'est un pari.

### L'histogramme tonal : les notes plutôt que leur interprétation

L'estimation tonale ne partait pas des notes. `_pc_hist` les remplaçait par
une reconstruction — fondamentale de chaque accord détecté ×2, notes de
l'accord ×0.5, voix supérieure ×1 — c'est-à-dire qu'elle faisait passer par
une détection d'accords à un accord par mesure une information que les notes
portaient déjà. Le builder calculait pourtant déjà, mesure par mesure, un
chroma pondéré durée × vélocité et sans percussions : il ne le gardait pas.
`Section.pc_weights` le conserve, `_pc_hist` le préfère, et retombe sur les
accords pour un `Score` écrit à la main, qui n'a pas de notes.

Le mélange a été essayé avant de trancher — réglage sur une graine tenue à
part (11), vérification sur deux autres (23, 31) :

| histogramme | graine 11 | graine 23 | graine 31 |
|---|---|---|---|
| notes brutes | **0.834** | **0.783** | **0.723** |
| notes + basses pondérées ×3 | 0.803 | 0.743 | 0.704 |
| notes + 0.25 × accords | 0.783 | 0.743 | 0.692 |
| notes + 1.0 × accords | 0.688 | 0.691 | 0.579 |
| accords seuls (ancien) | 0.561 | 0.605 | 0.491 |

Chaque cuillerée d'accords dégrade le résultat, sur les trois graines. Le
réglage optimal était de ne rien mélanger.

**Ce que ça a coûté, et ce que le coût a appris.** Trois graines de corpus
généré, calibration complète des deux côtés : la validation croisée
contrastive perdait **0.012** en moyenne (les trois dans le même sens), et la
perte se concentrait sur un seul axe — `13_tonal_contrast`, −0.030 d'AUC,
0.49 sur une graine. Un axe que le dépôt avait réparé de 0.36 à 0.56 : pas
une décimale à cacher.

Le diagnostic était un effet de plafond. L'ancrage de l'axe 13 créditait
pleinement dès que **la moitié** des sections partageaient la tonique — une
bande réglée quand la tonalité était estimée sur les accords détectés, donc
bruitée. Avec une estimation nette, l'original comme sa version dégradée
atteignent ce seuil, saturent tous les deux, et l'axe cesse de les
distinguer. Le crédit plein n'est plus donné qu'à une pièce dont **toutes**
les sections partagent la tonique : `band(ancrage, 0.35, 1.0, 1.01, 1.01)`,
plus de plateau.

Réglé sur une graine tenue à part (11 : 0.567 → 0.619, plateau stable sur
tout l'intervalle 0.30-0.45, d'où le 0.35 choisi au milieu et non au
maximum), vérifié sur trois graines jamais consultées pour choisir :

| AUC `13_tonal_contrast` | graine 7 | graine 23 | graine 31 | moyenne |
|---|---|---|---|---|
| accords (avant tout) | 0.559 | 0.567 | 0.509 | 0.545 |
| notes brutes, bande d'origine | 0.488 | 0.536 | 0.522 | 0.515 |
| notes brutes, bande resserrée | **0.584** | **0.696** | **0.584** | **0.621** |

L'axe ne se contente pas de récupérer : il dépasse de 0.076 ce qu'il valait
avant qu'on touche à l'histogramme, et quitte la liste des axes sous 0.5. La
validation croisée regagne 0.005 et s'établit à 0.007 sous son niveau
d'origine — ce qui reste dû à l'histogramme, pas à l'axe.

Deux conséquences visibles ailleurs : les poids publiés sont recalibrés
(`weights_songs.json` accuracy calibrée 1.000 sur 3 fichiers — surappris et
signalé comme tel ; `weights_sloopy.json` 0.881, validation croisée 0.853),
et un fixture de test a dû gagner une basse. Quatre triades plaquées sans
basse ne désignent pas leur fondamentale : do-mi-sol et mi-sol-si partagent
assez de matière pour qu'un histogramme de hauteurs lise le relatif mineur.
L'ancien histogramme s'en sortait parce que la détection d'accords, elle,
nomme la fondamentale. La basse manquait au fixture, pas à l'axe — elle est
là dans toute musique.

### Les profils modaux, désormais par défaut

Le trou est identifié — il manque des profils. `libretto.axes` en propose
deux, construits et non mesurés sur des auditeurs comme l'ont été ceux de
Krumhansl et Kessler : chaque mode est **son parent dont le degré
caractéristique est échangé** avec son voisin chromatique (le dorien est un
mineur qui monte sa sixte, le mixolydien un majeur qui descend sa septième),
le reste du profil parent laissé intact. Dorien et mixolydien seulement :
ce sont les deux modes dont `make_corpus` écrit la vérité terrain, donc les
deux qu'on sait valider — en livrer d'autres reproduirait le défaut qu'on
reproche à KK.

`estimate_key` les utilise **par défaut** ; `estimate_key(hist,
KEY_PROFILES)` restreint aux deux profils d'origine, témoin de toutes les
mesures antérieures. Validé sur deux graines de contrôle fraîches (23 et 31,
311 morceaux non modulants), à histogramme rigoureusement identique :

| tonique juste | graine 23 | graine 31 |
|---|---|---|
| `brut` | 0.743 | 0.667 |
| `brut+modes` | **0.809** | **0.730** |

Le détail par mode dit exactement ce qui a été acheté et à quel prix
(cumul des deux graines) :

| mode écrit | `brut` | `brut+modes` |
|---|---|---|
| dorien | 45/58 | **58/58** |
| mixolydien | 9/54 | **21/54** |
| mineur | 69/102 | 69/102 |
| majeur | 96/97 | 91/97 |

Le dorien est réglé, le mixolydien seulement à moitié, et cinq pièces en
majeur sur 97 passent à la trappe — le vocabulaire élargi leur vole la
réponse. Le solde reste franchement positif (+6.5 points de tonique), et le
mode suit la tonique : sur `brut+modes`, **toute erreur de mode est déjà une
erreur de tonique**, jamais l'inverse.

Deux résultats nuls, du même lot : sur les **loops** isolés, les profils
modaux ne changent rien (0.400 avant, 0.400 après — une boucle de quatre
mesures ne contient pas de quoi trancher, un meilleur profil n'y peut rien) ;
et sur l'histogramme du moteur, le gain existe mais reste inutile (`pipeline`
0.566 → 0.605), parce que c'est l'histogramme qui plafonne, pas le
vocabulaire.

**Ce que la bascule a coûté.** Le mode retourné traverse sept axes, dont deux
en déduisent une gamme — ceux-là passent maintenant par `PARENT_MODE`, un
mixolydien étant analysé sur la gamme majeure et non sur la mineure. La
question était de savoir si mieux nommer la tonalité dégrade la tâche que le
moteur sait faire : distinguer un original de sa version dégradée. Mesuré
sur trois graines de corpus généré (40 morceaux chacune), calibration
complète des deux côtés :

| graine | validation croisée, KK seul | avec les modes | AUC 11 |
|---|---|---|---|
| 7 | 0.944 | 0.938 | 0.604 → 0.650 |
| 23 | 0.916 | 0.925 | 0.548 → 0.581 |
| 31 | 0.944 | 0.934 | 0.616 → 0.603 |

Écart moyen **−0.002** : la bascule ne coûte rien de mesurable, et
`11_modulation_count` y gagne en moyenne 0.022. Les poids publiés ont été
recalibrés derrière, avec les commandes qui les avaient produits :
`weights_songs.json` (3 morceaux assemblés, accuracy calibrée 0.833 → 0.896)
et `weights_sloopy.json` (49 loops, calibrée 0.898 → 0.902, validation
croisée 0.873 → 0.858 — dans son propre écart-type, ± 0.05). `check.sh`
passe, aucun axe ne repasse sous le plancher AUC.

Réserves inscrites dans la sortie du script plutôt qu'en note de bas de page :
l'étiquette vaut pour le **kit** et non pour le fichier (les 160 fichiers
percussifs sont écartés — un snare étiqueté `G_m` n'a pas de hauteur à
juger) ; les fichiers d'un même kit ne sont pas des tirages indépendants,
d'où l'exactitude macro par kit à côté de la micro ; et 15 fichiers nomment
leur **fondamentale locale** plutôt que la tonalité du kit
(`KIT_1_PAD_Eb_100BPM.mid` dans `KIT_1_Gmin_100BPM/` est le VI d'un sol
mineur), cas que le parseur tranche en faveur de l'étiquette porteuse d'un
mode. Le parseur lui-même est testé (`tests/test_mesure_tonalite.py`) :
une règle trop large et le chiffre ne mesure plus le moteur mais le bruit
qu'on lui a servi.

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

## Sélection structurelle (Forge)

Le score SMS peut servir de **fonction de fitness** : générer plusieurs
ébauches, garder celle que Libretto juge la mieux construite.
`examples/forge.py` en fait la démonstration — il tire N candidats (via le
générateur de `make_corpus`), les note, et sélectionne le meilleur
**fiabilité d'abord** : le gate `--min-confidence` écarte les scores non
interprétables avant le classement, puis les éligibles sont triés par
**tranche de fiabilité** (« élevée » ≥ 0.75 avant « moyenne » ≥ 0.55) et
seulement ensuite par score. Un morceau très bien noté mais moyennement
fiable ne bat donc pas un morceau à peine moins noté mais pleinement fiable —
son chiffre est plus digne de foi. (On ne trie pas sur la confiance brute,
qui laisserait un 0.68 à confiance 1.00 l'emporter sur un 0.88 à 0.99.)

```bash
python3 examples/forge.py sortie/ 24 1          # 24 candidats, graine 1
python3 examples/forge.py sortie/ 24 1 --axes        # + pourquoi ce gagnant, axe par axe
python3 examples/forge.py sortie/ 24 1 --shortlist 5 # + 5 candidats, diversité garantie
python3 examples/forge.py sortie/ 24 1 --reaper      # + pousse le gagnant dans REAPER
```

Le mode `--axes` explique **pourquoi ce gagnant** : pour chacun des 29 axes,
son score contre la moyenne du peloton éligible, l'écart, et le **levier**
(écart × poids). Les poids sommant à 1.0, la somme des leviers vaut
*exactement* l'avance SMS du gagnant sur le peloton — la décomposition est
complète, rien ne se cache dans un résidu. On lit d'un coup d'œil où l'avance
se construit (« Arc émotionnel +0.017 ») et où elle s'érode (« Variété
texturale −0.019 ») ; le détail est aussi sérialisé dans `forge_report.json`
(section `axes_report`, ordre canonique des axes).

Le mode `--shortlist K` répond à la collapse de diversité (circularité, plus
haut) par une **sélection sous contrainte de diversité** : un round-robin par
forme sur le classement fiabilité-d'abord — tant qu'une forme n'est pas
représentée, la place suivante lui revient ; à contrainte égale, c'est
toujours le mieux classé qui passe. La diversité choisit *qui* concourt, le
mérite garde l'ordre : la shortlist compte min(K, formes distinctes) formes,
garanti, et son premier élu reste le gagnant. Les K fichiers sont livrés
(`forge_short_XX.mid`) et le coût en score est affiché sans fard — il peut
même être négatif, quand la contrainte repêche un score élevé d'une tranche
plus basse.

**Commander plutôt que tirer.** Sans contrainte, chaque ébauche tire
tonalité, mode, tempo, métrique et longueur au sort : la graine est la seule
poignée, et elle ne dit rien de ce qui sortira. Les options
`--tonic/--mode/--bpm/--meter/--bars` imposent ces champs.

```bash
python3 examples/forge.py sortie/ 10 1 \
    --tonic F --mode min --bars 16 --bpm 72 --meter 4/4 --swing
# → verse_chorus_court · F min · 4/4 · 72 bpm · 16 mes.  (score 0.783, fiab. 0.923)
```

Contraindre **restreint** l'espace de recherche sans l'annuler : motifs,
progressions, arc d'énergie, effectif et — tant que `--bars` ne la dicte pas
— la forme restent tirés. Sans cet espace résiduel, N candidats seraient N
clones et la sélection n'aurait plus d'objet. `--bars` impose la longueur
**totale exacte** (intros et outros comptent pour moitié : `total_bars` est
la seule vérité sur ce décompte) et la forme est tirée parmi celles qui
tombent juste ; une longueur inatteignable est refusée **avant** tout rendu,
avec les valeurs voisines. Un tempo imposé désactive la dérive, sinon la
contrainte ne tient qu'au premier temps.

Le gate, lui, ne bouge pas : une commande contraignante peut ne produire
aucun candidat fiable. C'est un résultat — la structure demandée n'est pas
notable — pas une panne à contourner en baissant le seuil. Les contraintes ne
valent que pour la génération : `--from-dir` note des MIDI déjà écrits, et les
refuse plutôt que de les ignorer en silence.

Dans une vraie chaîne, la brique génératrice se remplace par une
transcription (basic-pitch) d'un rendu audio ou la sortie MIDI d'un modèle —
Forge ne parle que MIDI, le reste ne bouge pas. Ce branchement existe, à
deux niveaux :

```bash
# Niveau 1 — universel, zéro dépendance : noter les MIDI de n'importe quelle
# source déposés dans un dossier (n et seed ignorés, sources jamais effacées).
python3 examples/forge.py sortie/ --from-dir mes_candidats/ --axes --shortlist 5

# Niveau 2 — un vrai modèle : MusicLang (transformer symbolique open source).
# pip install musiclang-predict "numpy<2"   (hors contrat stdlib, pont optionnel)
python3 examples/forge_musiclang.py sortie/ 8 1 --axes
```

Avec `--from-dir`, la « forme » de chaque candidat est la **signature de
sections jugée par Libretto** (intro-verse-chorus-verse → `IVCV`) — personne
ne connaît la forme voulue d'un MIDI venu d'un modèle — et c'est elle que
`--shortlist` utilise comme clé de diversité. `forge_musiclang.py` assume
l'honnêteté du banc d'essai : MusicLang produit des textures harmoniques
cohérentes, pas des chansons à couplets/refrains — attendez-vous à des
fiabilités plus basses et un gate plus sollicité que sur `make_corpus`.
C'est le juge qui fait son travail, pas le branchement qui échoue.

Pour un modèle **audio** (ACE-Step, Suno local, YuE…), il faut transcrire —
et le maillon transcription a un piège, mesuré par le harnais rejouable
`examples/transcription_roundtrip.py` (MIDI connu → synthèse → basic-pitch →
re-jugement) : le score SMS chute (**−0.18 en moyenne** sur de l'audio
*propre* — un mix réel fera pire), le classement n'est que partiellement
préservé (**Spearman +0.1 à +0.6**, le vrai n°1 peut ressortir 5ᵉ), et —
le piège — **la fiabilité ne détecte rien** : elle peut même *monter* après
transcription (0.66 → 1.00 observé), parce que le transcripteur produit
beaucoup de notes et que la confiance mesure la quantité de matière, pas sa
provenance. Le gate `--min-confidence` ne protège donc pas d'une mauvaise
transcription. Conséquence : sur du transcrit, Forge est un **triage
grossier** — écarter le tiers du bas est fiable, couronner le n°1 ne l'est
pas ; livrez une `--shortlist` aux oreilles. La chaîne propre existe, en
deux étapes découplées — la conversion coûte ~1-3 min par prise, le
jugement quelques millisecondes, on ne paie le lourd qu'une fois :

```bash
python3 examples/audio2midi.py prises/ midi/    # Demucs par stems → basic-pitch
                                                # par stem tonal, batterie en
                                                # onsets canal 9, tempo estimé
python3 examples/forge.py sortie/ --from-dir midi/ --axes --shortlist 5
# ou d'un trait (raccourci) :
python3 examples/forge_acestep.py prises/ sortie/    # --shortlist 5 par défaut
```

Enfin, si vous partez d'**une** séquence à vous (un export Gadget, une
maquette) et voulez l'**embellir** plutôt que trier des candidats,
`examples/forge_embellish.py` en tire N variantes par transformations
symboliques (humanisation vélocité/timing, arc dynamique, doublages
d'octave, arpèges), les fait juger, et garde la mieux construite — 100 %
stdlib, déterministe :

```bash
python3 examples/forge_embellish.py ma_sequence.mid sortie/ 12 1 --axes
```

Trois partis pris d'honnêteté : **l'original concourt** (variante 000
intacte) — s'il gagne, votre séquence est déjà solide, c'est un résultat ;
les retouches touchent la **structure** que Libretto mesure (arc, dynamique,
texture), pas le son ni le groove, donc un gain de score est un gain de
charpente *à confirmer à l'oreille* ; et comme c'est du MIDI natif (pas du
transcrit), la fiabilité se lit. Le verdict le dit sans détour : « la
variante *arc+octaves* bat l'original de +0.03 » ou « l'original gagne, vos
retouches n'ont pas amélioré la structure ».

Le rapport signale aussi la
**collapse de diversité** qu'induit toute sélection sur un score unique :
optimiser le SMS resserre le peloton de tête sur l'esthétique inscrite dans
les bandes de tolérance (cf. circularité, plus haut). C'est montré, pas caché
— un pipeline sérieux sélectionnerait sous contrainte de diversité, pas sur
le score seul.

`examples/forge_sweep.py` mesure ce que la règle « fiabilité d'abord »
apporte, agrégé sur plusieurs graines : pour chaque tirage il compare le
gagnant à celui qu'aurait désigné le tri sur le score seul.

```bash
python3 examples/forge_sweep.py 10 24 1     # 10 graines × 24 candidats
```

Sur 10 graines (déterministe), la règle **change le gagnant six fois sur
dix**, pour un score brut cédé de seulement **0.026 en moyenne** (min 0.005,
max 0.054) — on paie moins de trois centièmes de score pour gagner une tranche
de fiabilité entière. Le gate `--min-confidence` recale au moins un candidat
sur 2 graines sur 10, même sur ce corpus 100 % « morceaux » : sur 238
candidats éligibles, **84 % tombent en « élevée » et 15 % en « moyenne »**. Le
top 5 libre ne retient que ~3.9 des ~6.9 formes générées — la collapse de
diversité est constante, pas un accident de tirage. Et la contrainte de
diversité est bon marché : la shortlist round-robin (k=5) **retient 5.0
formes sur 5, pour un coût en score moyen de 0.008** (max 0.020) — une forme
entière regagnée coûte moins d'un centième de score.

## Forge sur un pack de loops

Il manquait à Forge la source la plus banale d'un studio : **un pack de
loops**. Les progressions et les riffs y sont écrits par des humains, ce que
le générateur de `make_corpus` ne sait pas inventer et qu'une transcription
audio dégrade (−0.18). Ce qu'un pack n'a pas, en revanche, c'est une
**forme** : il livre huit boucles de quatre mesures, pas une chanson. C'est
exactement la division du travail que Forge permet — la matière vient du
pack, la charpente est cherchée.

```bash
python3 examples/loop_index.py ~/Desktop/SAMPLES/MIDI --out /tmp/index.json
python3 examples/forge_loops.py /tmp/index.json sortie/ 48 3 --axes --shortlist 5
python3 examples/forge_loops.py /tmp/index.json sortie/ 48 3 --famille "Naija Waves"
```

`loop_index.py` lit ce que les noms de fichiers et de dossiers annoncent
(tonalité, tempo, instrument) et mesure ce qu'ils taisent (longueur,
polyphonie, tessiture). **La tonalité vient de l'étiquette, pas de
l'estimateur** — sur une boucle isolée, celui-ci tombe juste 4 fois sur 10
(voir *Tonalité*) ; il n'est appelé que pour les fichiers non étiquetés, et
l'index dit toujours d'où vient l'information. Le rôle est lu dans le nom
quand il s'y trouve, tranché par le contenu sinon (polyphonie ≥ 1.8 =
accompagnement ; monodie grave = basse). Une **famille** — un dossier, une
tonalité, un tempo — est ce qui s'arrange ensemble sans transposer ; elle
sépare toute seule `Bonfire_Dm_100` de `Moody_90_Bm` dans le même dossier.
Sur 820 fichiers : 800 indexés, 76 familles arrangeables.

Chaque candidat tire un plan (forme, longueur des sections, effectif par
section, courbe d'énergie, parfois une modulation ou une mélodie empruntée à
une autre famille du même pack, transposée à mode égal), tuile les boucles
dessus et écrit les marqueurs. Seule la batterie se prête d'une famille à
l'autre : elle n'a pas de tonalité.

**Ce que ça vaut.** Le seul comparatif qui isole l'apport de la recherche est
à matière égale — le même pack Naija Waves, plan écrit à la main contre plan
cherché :

| Naija Waves | score SMS | fiabilité |
|---|---|---|
| `assemble_naija.py` (PLAN écrit à la main) | 0.760 | 0.97 |
| `forge_loops.py` (48 plans cherchés, graine 3) | **0.839** | 0.93 |

Contre le générateur synthétique, à budget égal (48 candidats, graines 1-2-3)
le match est nul, et il faut le dire ainsi plutôt que de choisir la ligne qui
arrange :

| médiane sur 3 graines | gagnant | meilleur score | médiane du peloton |
|---|---|---|---|
| `forge.py` (synthétique) | **0.863** | 0.884 | **0.786** |
| `forge_loops.py` (pack) | 0.849 | **0.893** | 0.757 |

Le plafond est un peu plus haut sur les loops, le peloton un peu plus bas :
la matière humaine est plus inégale que celle d'un générateur réglé sur les
mêmes hypothèses que les axes.

**Où ça se joue**, par groupe d'axes (médianes, 48 candidats) : la forme
monte (A 0.846 contre 0.766) et la cohérence globale aussi (F 0.649 contre
0.500) — c'est l'apport de la recherche de plan. La mélodie s'effondre
(**C 0.597 contre 0.834**), et l'axe 15 (contour mélodique) porte presque
tout l'écart : 0.471 contre 0.956. Deux corrections ont été essayées et
**réfutées**, chiffres à l'appui : pousser tout l'accompagnement sous la note
la plus grave de la mélodie, pour que la voix supérieure lue par Libretto
soit bien la mélodie — groupe C inchangé (0.597), meilleur score 0.908 →
0.899 ; et faire entrer la mélodie beaucoup plus souvent — groupe C 0.597 →
0.574. Ce qui reste est l'explication la plus économique : les lignes d'un
pack ne satisfont pas les bandes de tolérance des axes 15 et 19 comme le font
celles de `make_corpus`, **écrites sous les mêmes hypothèses que les axes**.
C'est la circularité vue d'un autre angle — le générateur maison a un
avantage de naissance sur le juge maison, et il ne se voyait pas tant qu'on
ne lui opposait pas de la matière étrangère.

Ces écarts sont ceux du **juge**, et le juge a depuis été confronté à une
oreille sur ces mêmes candidats (voir *Validation externe*, sessions 6-7) :
sur des écarts de score supérieurs à 0.10 l'accord est de 62 % — un signal
non établi ; sur des écarts de quelques centièmes, rien. Le 0.839 contre
0.760 tient donc comme mesure de charpente, pas comme promesse que le
gagnant s'entend mieux.

Rien du pack n'entre dans le dépôt : l'index ne contient que des chemins, et
le dossier reste où il est.

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
  `Pont`… — anglais et français normalisés), sinon segmentation par
  **nouveauté + répétitions** : la nouveauté repère les ruptures, la matrice
  d'auto-similarité repère les *retours*. Un couplet qui revient après un
  refrain ne crée aucune nouveauté — son matériau est déjà connu — mais
  forme une diagonale nette dans la matrice. Mesuré sur trois corpus
  annotés, ce second terme porte tout le gain : F-mesure des frontières
  0.55 → **0.73**. Le noyau en damier de Foote (2000) a été implémenté puis
  retiré : il ne faisait jamais mieux que la nouveauté par fenêtres et ne
  paraissait gagnant que sur le corpus ayant servi à régler son seuil.
  Deux garde-fous complètent le tandem, chacun réglé sur la graine 7 et
  validé sur trois graines tenues à l'écart, jamais négatifs : le budget
  du quantile admet **3 × min_len** cellules — les cellules admises se
  partagent entre toutes les diagonales, et les alignements de phrase
  mangeaient le budget du vrai retour, tuant le run A-A des petits
  ternaires — et la **cohérence des jumelles** supprime toute frontière
  intérieure à un run de répétition dont la coupure ne se répond pas à
  ±lag dans l'autre copie (un refrain coupé en deux à la couture de
  phrase tombe ; AB|AB survit, chaque coupure sauvant sa jumelle).
  Cumul des deux, quatre graines : F-mesure 0.775-0.892 →
  **0.790-0.921**, comptes de sections exacts 29 → 38/80 ;
- étiquetage par regroupement des sections sur la **similarité alignée
  mesure à mesure** (agglomératif à liaison moyenne, seuil absolu 0.975,
  pénalité de longueur). Deux seuils absolus ont existé et le premier a
  échoué : sur le cosinus des *moyennes* par section, tout s'écrase vers
  1.0 (29 morceaux sur 34 rangés dans un unique type en v0.2 — d'où un
  seuil adaptatif, longtemps). Comparer les mesures une à une rouvre
  l'échelle — reprises ≥ 0.99, matériaux distincts ≤ 0.97, couplet contre
  refrain 0.67 — et c'est alors le seuil *adaptatif* qui devenait le
  maillon faible. Réglé sur la graine 7, validé sur trois graines jamais
  consultées : partitions exactes à frontières vraies 4 → 17/23
  (réglage) et 16 → 40/57 (écartées). Les singletons gardent en outre
  des noms distincts (bridge, interlude, solo) — tout nommer « bridge »
  détruisait la partition que le clustering venait de trouver ;
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
- **Quatre profils tonaux, et le mixolydien reste à moitié manqué.** Aux deux
  profils de Krumhansl-Kessler s'ajoutent un dorien (réglé : 58/58) et un
  mixolydien (21/54, contre 9/54 sans lui) — le second reste le trou. Les
  modes plus rares (phrygien, lydien) sont absents faute de vérité terrain
  pour les valider. Voir *Tonalité*.
- Mélodie = voix supérieure échantillonnée par temps : attrape les sommets
  d'arpèges d'accompagnement.
- Segmentation évaluée, mais encore approximative : F-mesure des frontières
  **0.79-0.92** selon la graine (tolérance ±1 mesure), et surtout le **nombre
  de sections** n'est exact que dans 48 % des cas (38/80, contre 29 avant le
  budget de concurrence et la cohérence des jumelles) — c'est lui qui
  plafonne la forme : le regroupement par matériau, à frontières justes,
  est passé de 4/23 à 17/23 (validé sur trois graines écartées), et la
  forme complète — frontières détectées puis partition — de 12/80 à 29/80.
  Les axes qui lisent les étiquettes ont suivi le regroupement (AUC moyenne
  sur 3 graines : 03 0.50 → 0.58, 04 0.51 → 0.59, 06 0.44 → 0.73, ce
  dernier inversé sur une graine avant) ; le meilleur compte de sections,
  lui, n'a pas bougé ces AUC (±0.003 — il améliore l'original et sa
  version dégradée symétriquement) mais relève la validation croisée
  contrastive de +0.012 en moyenne (0.963 → 0.978, 0.959 → 0.981,
  0.966 → 0.966). L'axe 01 reste à 0.55. Le gate AUC reste calé à 0.42 :
  la marge avec le plancher de bruit mesuré (0.448) est mince, et le
  remonter suppose de stabiliser d'abord ce qui reste du compte de
  sections — les intros de deux mesures, sous `min_len`, restent
  indétectables par construction.
- Esthétique pop inscrite dans les bandes de tolérance : un nocturne ou une
  pièce ambient scorent bas par construction. Les profils de poids
  (`weights_*.json`) atténuent, ils ne suppriment pas.
- Corpus de validation synthétique : il valide la cohérence interne
  (original > dégradé), pas l'accord avec un jugement humain. L'outil de
  recueil existe (`annotate` / `agreement`), les annotations non — **le
  moteur n'a jamais été confronté à une oreille**.
- Synthèse d'écoute sommaire, mais plus aveugle à la dynamique : depuis le
  rendu `v2-timbre`, la vélocité module la brillance (filtre en v²) et
  l'attaque, pas seulement le volume — vérifié à l'analyseur (centroïde
  299 → 492 Hz entre vélocités 30 et 115). Chaque fichier de jugements
  enregistre la version du rendu qui l'a produit. Au-delà de 4000 notes le
  fichier est tronqué à la lecture.
