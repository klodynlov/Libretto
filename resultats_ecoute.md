# Première confrontation à l'oreille — 58 comparaisons A/B

Session unique, un annotateur, corpus `corpus_ecoute` (27 morceaux générés,
~47 s chacun), ~50 minutes d'écoute cumulée. Données brutes dans
`jugements.json`, protocole décrit dans `libretto/annotate.py`.

```bash
python3 -m libretto.cli agreement jugements.json --corpus corpus_ecoute
```

## Ce qui est établi

**Quatre dégradations sur cinq s'entendent nettement.** C'est la première
vérification externe de l'hypothèse qui porte tout le projet : qu'un morceau
dégradé soit réellement moins bien structuré à l'écoute.

| dégradation | l'oreille désigne l'original | IC95 | verdict |
|---|---|---|---|
| `jitter_onsets` | 100 % | 0.74–1.00 | audible |
| `scramble_melody` | 91 % | 0.62–0.98 | audible |
| `transpose_segments` | 91 % | 0.62–0.98 | audible |
| `shuffle_bars` | 90 % | 0.60–0.98 | audible |
| **`flatten_dynamics`** | **18 %** | **0.05–0.48** | **inversée** |

**Accord moteur / oreille : 74 %** sur les 54 paires tranchées — mais **88 %
si l'on écarte `flatten_dynamics`**, sur laquelle le désaccord est presque
entièrement concentré.

## Le résultat gênant

`flatten_dynamics` — aplatir toutes les vélocités à leur moyenne — n'est pas
perçue comme une dégradation : elle est préférée. L'intervalle de confiance
est entièrement sous 0.5, ce n'est donc pas un résultat nul mais une
inversion.

Hypothèse écartée : « les sections douces sont inaudibles ». L'écart mesuré
entre sections faibles et fortes est de 7 à 8 dB, un contraste modeste et
parfaitement audible.

Hypothèse retenue : en synthèse, la vélocité MIDI ne module que le volume,
alors qu'un instrument réel change aussi de timbre. Une variation de
vélocité s'entend donc comme un fader qu'on bouge, et l'uniformité passe
pour de la stabilité. Le test aurait alors mesuré « préférez-vous un volume
constant ? » plutôt que « l'arc dynamique structure-t-il ? ».

Ce que ça coûte au moteur, mesuré :

| | 5 dégradations | sans `flatten_dynamics` |
|---|---|---|
| accuracy poids experts | 0.931 | 0.930 |
| validation croisée | 0.924 | **0.929** |
| `26_dynamic_range` (AUC) | 0.667 | **0.584** |
| `28_emotional_arc` (AUC) | 0.681 | **0.614** |
| `14_bass_melodic` (AUC) | 0.737 | 0.818 |
| `17_theme_recognition` (AUC) | 0.769 | 0.835 |

Deux axes tiraient l'essentiel de leur pouvoir discriminant d'une
dégradation que l'écoute conteste ; les autres gagnent à s'en débarrasser.
Retirer `flatten_dynamics` ne coûte donc rien globalement — la validation
croisée monte même légèrement.

## Réserves

- **Un seul annotateur, une seule session.** Aucun accord inter-annotateur
  n'est mesurable.
- **Onze jugements par dégradation.** Assez pour trancher un taux de 90 %
  contre le hasard, pas pour un écart fin.
- **Contrôles imparfaits** : sur 4 paires strictement identiques, une seule
  a reçu « aucune différence ». L'annotateur tranche des paires
  indiscernables, signe d'une réticence à répondre « je n'entends pas ». Cela
  ajoute du bruit centré sur 0.5, qui *dilue* les taux vers le milieu sans
  les inverser — les valeurs à 90-100 % en sont donc plutôt sous-estimées, et
  l'inversion de `flatten_dynamics` n'en est pas expliquée.
- **Rendu synthétique** (ondes simples, bruit filtré pour les percussions) :
  suffisant pour juger une structure, pas un arrangement.

## Suite

### 1. `scramble_dynamics` — testée, écartée (session 2)

Seconde session, 31 jugements, ciblée sur les deux dégradations dynamiques
(`jugements2.json`, seed 2). Contrôles corrects cette fois : 50 % de « aucune
différence » sur les paires identiques, contre 25 % à la session 1.

| dégradation | l'oreille désigne l'original | IC95 | verdict |
|---|---|---|---|
| `flatten_dynamics` | 14 % | 0.04–0.40 | inversée (reconfirmée) |
| `scramble_dynamics` | 40 % | 0.17–0.69 | **non concluante** |

Deux constats, un provisoire et un de fond.

`flatten_dynamics` est **réfutée une seconde fois**, sur un lot indépendant.
Cumul des deux sessions : 4 succès sur 25, soit 16 %, IC95 [0.06, 0.35].
Ce n'est pas un accident d'échantillonnage.

`scramble_dynamics` n'est **pas** la solution espérée. Son intervalle enjambe
0.5, avec une pente vers l'inversion. Et ce n'est pas une question de
puissance statistique : un taux au voisinage de 0.5 ne devient pas
significatif en accumulant des jugements autour de 0.5 — à n=60 simulé,
l'IC resterait [0.29, 0.53].

**Ce que ça dit du moteur.** Les deux façons de dégrader la dynamique —
supprimer son amplitude, détruire son organisation — échouent au test
d'écoute. La conclusion la plus économique n'est pas que ces dégradations
sont mal conçues, mais que **la vélocité MIDI, en rendu de synthèse, ne
porte pas de structure perceptible**. En synthèse, la vélocité ne module que
le volume ; sur un vrai instrument, elle change aussi le timbre, l'attaque,
la brillance — et c'est probablement là que l'arc dynamique s'entend.

Les axes 26 (`dynamic_range`) et 28 (`emotional_arc`) mesurent donc une
dimension réelle de la partition, mais que ce protocole ne peut pas valider.
Ce n'est pas une réfutation des axes ; c'est une limite du banc d'essai, à
lever avec un rendu instrumental (SoundFont, ou export vers un synthé). En
attendant, leur poids reste à considérer comme non vérifié par l'oreille.

`scramble_dynamics` est conservée comme négatif de cohérence interne (elle
raffermit la calibration : validation croisée 0.924 → 0.937) mais reste hors
d'`AUDIBLE_DEGRADATIONS`, au même titre que `flatten_dynamics`.

### Historique — `scramble_dynamics`, l'hypothèse initiale

Une sixième dégradation permute les vélocités **à l'intérieur de chaque
canal**, au lieu de les aplatir. Hypothèse musicale : l'incohérence
dynamique s'entend comme un défaut, là où l'uniformité passe pour de la
production.

Le choix de permuter — plutôt que de tirer au hasard — et de le faire par
canal n'est pas cosmétique. Un tirage changerait la distribution des
vélocités, et l'on ne saurait plus si l'oreille réagit au désordre ou à un
écrasement de la plage. Une permutation globale enverrait les nuances des
percussions sur le piano, ce qui déséquilibre le mixage : un artefact
d'orchestration sans rapport avec la structure.

Propriétés vérifiées par test : histogramme de vélocité identique par canal,
hauteurs et rythme intacts, deux tiers des vélocités déplacées, contraste
d'intensité entre sections effondré.

Effet mesuré sur les axes que `flatten_dynamics` laissait orphelins :

| axe | avec `flatten` seule | avec les deux |
|---|---|---|
| `26_dynamic_range` | 0.667 | **0.722** |
| `28_emotional_arc` | 0.681 | **0.730** |

Validation croisée globale : 0.924 → **0.937**.

**Rien de tout cela ne prouve qu'elle s'entende.** Ce sont des mesures
internes, exactement le genre de chiffres que la session précédente a
démentis pour `flatten_dynamics`. La session ciblée :

```bash
python3 -m libretto.cli annotate corpus_ecoute \
    --only flatten_dynamics,scramble_dynamics --out jugements2.json
python3 -m libretto.cli agreement jugements2.json --corpus corpus_ecoute
```

Les deux dégradations dans le même lot, sur les mêmes morceaux : si
`scramble_dynamics` ressort audible là où `flatten_dynamics` reste inversée,
la substitution est fondée. Sinon, c'est toute la dimension dynamique du
moteur qu'il faut revoir — et les axes 26 et 28 avec elle.

### Session 3 — rendu v2-timbre : le verdict

Les sessions 1-2 partageaient un défaut de banc d'essai : la vélocité n'y
modulait **que le volume**. Sur un instrument réel, frapper fort rend aussi
le son plus brillant et l'attaque plus mordante — c'est probablement là que
l'arc dynamique s'entend. Le rendu `v2-timbre` le modélise en synthèse
soustractive, toujours sans dépendance : dent de scie → passe-bas dont la
coupure suit v², attaque de 4 à 32 ms selon la vélocité, pour les voix comme
pour les percussions.

Vérifié à l'analyseur spectral, sur la page servie : centroïde 299 Hz à
vélocité 30 contre 492 Hz à vélocité 115 (rapport 1.6×, comparable à l'écart
doux/fort d'un instrument acoustique), en plus des écarts d'attaque et de
volume.

Chaque fichier de jugements enregistre désormais la **version du rendu** qui
l'a produit, et la reprise d'une session refuse de mélanger deux rendus —
deux rendus sont deux expériences, les mélanger rendrait le fichier
ininterprétable. Les fichiers antérieurs valent `v1-volume`.

Session ciblée identique à la session 2 (mêmes 27 morceaux, `flatten` +
`scramble`, contrôles garantis), graine 3, `jugements3.json`. 30 jugements,
contrôles excellents (67 % de « aucune différence » sur 6 paires
identiques — la session la plus fiable des trois).

**Résultat : la branche « toujours rien », la plus coûteuse.**

| dégradation | v1-volume (s1+s2) | v2-timbre (s3) |
|---|---|---|
| `flatten_dynamics` | 4/25 = 16 % [0.06–0.35] | **0/11 = 0 %** [0.00–0.26] |
| `scramble_dynamics` | 4/10 = 40 % [0.17–0.69] | 4/10 = 40 % [0.17–0.69] |

L'hypothèse de l'artefact de rendu est **morte** : donner un canal timbral à
la vélocité a rendu l'aplatissement *plus* préféré (0/11 — l'original n'a
jamais été désigné), pas moins. Et `scramble` sort au même 40 % exactement
sous les deux rendus : pas d'effet, reproduit.

Conclusion ferme : **la dynamique MIDI ne porte pas de structure perceptible
dans ce banc d'essai**, ni en amplitude, ni en organisation, ni en volume,
ni en timbre. Reste ouverte la question d'un rendu pleinement instrumental
(vrais échantillons, mixage) — mais deux modèles de rendu concordants
suffisent pour agir.

Conséquences appliquées :

1. **Poids experts révisés** (voir `AXES_META`) : `26_dynamic_range`
   0.030 → 0.010, `28_emotional_arc` 0.050 → 0.030 (réduit, pas plancher —
   son énergie mêle tempo et densité à la vélocité, et il détecte
   `shuffle_bars` vers 0.7). La masse va aux axes validés par l'oreille :
   12 (+0.010), 16 (+0.010), 17 (+0.010), 20 (+0.010).
2. **La calibration n'utilise plus que les quatre dégradations validées**
   par défaut : optimiser contre un négatif que l'oreille contredit, c'est
   optimiser à contresens. `--all-degradations` pour l'ancien comportement.
3. Effet mesuré : accuracy experts 0.926, validation croisée 0.930 sur le
   corpus de morceaux ; 0.873 sur le pack sloopy (contre 0.823).

### Session 4 — rendu v3-instrument : la question est close

Dernier étage du banc d'essai : de **vrais échantillons**. FluidSynth +
MuseScore_General.sf3, où la vélocité ne module plus un filtre mais
déclenche des couches d'enregistrement distinctes — un piano frappé fort
n'est pas un piano doux amplifié, c'est un autre son. Vérifié par test :
deux rendus ne différant que par les vélocités produisent des signaux dont
le RMS diffère d'un facteur > 1.5.

```bash
python3 -m libretto.cli annotate corpus_ecoute --render instrument \
    --only flatten_dynamics,scramble_dynamics --out jugements4.json --seed 4
```

Le serveur rend chaque côté en WAV (à la demande, en cache) et ne sert au
navigateur que deux URL opaques — le client ne reçoit même plus les notes.
Une paire de contrôle produit deux WAV strictement identiques (testé
octet à octet). Instruments assignés par program change : piano, basse
électrique, piano électrique, cordes, batterie GM.

C'est la dernière hypothèse qui puisse réhabiliter les axes dynamiques :

- **audibles sous v3** → la dynamique s'entend sur de vrais échantillons ;
  les poids 26/28 sont restaurés et le verdict des sessions 1-3 est
  requalifié en limite des rendus de synthèse ;
- **toujours rien** → la question est close sur les trois étages du banc
  d'essai, et les poids réduits restent.

FluidSynth est un outil externe optionnel, comme REAPER : le paquet Python
reste 100 % stdlib, et sans l'outil `--render instrument` explique quoi
installer au lieu de planter.

**Résultat (30 jugements : 27 tranchés, 3 contrôles).** La grille ci-dessus
prévoyait deux issues ; la réalité a pris une troisième voie qu'elle
n'anticipait pas : **audible ET inversée**.

| dégradation | l'oreille désigne l'original | verdict |
|---|---|---|
| `flatten_dynamics` | 3/15 = 20 % [0.07–0.45] | **INVERSÉE** |
| `scramble_dynamics` | 2/12 = 17 % [0.05–0.45] | **INVERSÉE** |

Le fait nouveau n'est pas le taux, c'est le zéro : **zéro « aucune
différence » sur 27 paires réelles** (médiane d'écoute 13 s par paire,
contrôles à 67 % de « aucune différence »). Sous v1 et v2, `scramble`
stagnait à 40 % ; sous v3, chaque paire sonne distinctement — le rendu par
échantillons transmet parfaitement la dynamique — et l'oreille choisit la
version dégradée dans 22 paires sur 27. L'hypothèse « le rendu masque la
dynamique » est morte de la meilleure façon possible : la dynamique
s'entend, et c'est le dégradé qui gagne.

La trajectoire de `scramble_dynamics` raconte le mécanisme : 40 % (volume)
→ 40 % (timbre modélisé) → 17 % (échantillons réels). Plus le rendu est
réaliste, plus la permutation est préférée. Sur de vrais échantillons, des
vélocités aléatoires note à note produisent exactement ce que les stations
audio appellent *humanize* : une micro-variation dynamique qui sonne
humaine. Et l'aplatissement s'entend comme un nettoyage — timbre homogène,
rendu « produit ». Les rampes de vélocité par blocs du générateur, elles,
sonnent arbitraires.

**La conclusion se déplace donc du rendu vers le corpus** : le générateur
ne produit pas de dynamique *musicale*. Ses vélocités sont des motifs
mécaniques dont la destruction est au pire neutre, au mieux une
amélioration perçue. Les axes 26/28 ne sont pas réfutés comme idées — sur
de vraies interprétations, la dynamique porte évidemment de la structure —
mais ils sont **intestables sur ce banc d'essai** : aucune des trois
générations de rendu ni aucune des deux dégradations ne peut produire de
preuve en leur faveur, parce que la matière première n'existe pas dans le
corpus généré.

Conséquences :

1. La branche « toujours rien » de la grille s'applique *a fortiori*
   (inversée est plus forte que rien) : **les poids réduits restent**
   (26 à 0.010, 28 à 0.030) — non parce que la dynamique ne compte pas,
   mais parce qu'aucune preuve en sa faveur n'est productible ici.
2. `flatten_dynamics` et `scramble_dynamics` sont définitivement hors
   calibration par défaut : le verdict couvre désormais les trois étages
   du banc d'essai (4 sessions, 4 lots indépendants, 3 rendus).
3. La seule voie restante pour valider 26/28 : un corpus
   d'**interprétations humaines réelles** (MIDI joué, pas généré), où la
   dynamique est une intention et non un motif. Hors de portée du
   générateur — noté comme piste externe.

### Session 5 — interprétations humaines réelles : la dynamique jouée s'entend

La piste externe est outillée : `examples/fetch_maestro.py` constitue un
corpus d'extraits de **MAESTRO v3** (Hawthorne et al. 2019, CC BY-NC-SA) —
~1 200 interprétations capturées au Disklavier lors de l'International
Piano-e-Competition. Chaque vélocité y est le geste réel d'un pianiste.
C'est exactement la matière dont les sessions 1-4 ont montré l'absence
dans le corpus généré.

Choix méthodologiques, fixés avant toute écoute :

- **24 extraits de 50 s**, un par interprétation, compositeurs en
  tourniquet ; fenêtre à 30 % de la durée, glissement déterministe de
  +12 s si creux (< 60 notes). **Aucune sélection sur la variance de
  vélocité** — choisir les passages « les plus dynamiques » fabriquerait
  le résultat.
- **La pédale voyage avec l'extrait.** Le parseur capture désormais les
  contrôleurs (CC64/66/67), `slice_mididata` réémet l'état de pédale au
  début de fenêtre, et les dégradations les recopient à l'identique
  (testé). Sans cela, un côté de la paire sonnerait sec et l'autre
  résonnant — on jugerait la pédale, pas la dynamique.
- Même protocole que la session 4 : rendu v3-instrument, `flatten` +
  `scramble`, contrôles garantis, aveugle total (URL opaques).

Grille de décision, engagée avant l'écoute :

- **détectées** (IC bas > 0.5) → la dynamique *jouée* porte une structure
  audible que sa destruction abîme. Ensuite seulement, vérifier que les
  axes la *mesurent* : AUC de 26/28 sur ce corpus. Si l'AUC suit, poids
  26/28 recalibrés ; si elle ne suit pas, les axes sont mal construits —
  à réécrire, pas à repondérer.
- **rien, ou inversées encore** → même l'interprétation réelle ne fait
  pas de ces dégradations des négatifs valides ; poids réduits
  définitifs, et le banc d'essai aura épuisé matière ET rendus.

Dans les deux cas, l'AUC moteur de 26/28 sur ce corpus est mesurée —
l'accord oreille/moteur se juge des deux côtés.

Réserve notée d'avance : MAESTRO est du piano solo sans carrure fiable
(pas de vraie grille de mesures) — les axes qui exigent mesures ou
pupitres tomberont en basse confiance, c'est le rôle de l'indicateur.
La question posée à l'oreille, elle, ne porte que sur la dynamique.

**Résultat final (lot complet : 55 jugements — 46 tranchés, 2 « aucune
différence », 7 contrôles).** Pour la première fois en cinq sessions,
l'oreille désigne l'original sur des dégradations dynamiques, et les deux
intervalle par intervalle :

| dégradation | l'oreille désigne l'original | verdict |
|---|---|---|
| `flatten_dynamics` | 17/24 = 71 % [0.51–0.85] | **AUDIBLE** |
| `scramble_dynamics` | 16/22 = 73 % [0.52–0.87] | **AUDIBLE** |
| global | 33/46 = 72 % [0.57–0.83] | |

Médiane d'écoute 44 s par paire sur la première moitié, 24 s sur la
seconde — contre 13 s en session 4 : la vraie musique se juge lentement.
Au point d'étape (30 paires), `scramble` était déjà audible (83 %) et la
grille appliquée ; le lot complet confirme et ajoute `flatten`, qui passe
de « penche » (69 %, IC chevauchant 0.5) à audible (71 %, IC [0.51–0.85]).

Le contraste avec la session 4 est total, et c'est le même annotateur, le
même rendu, le même protocole — seule la matière a changé : sur des
vélocités de générateur, permuter s'entendait comme une *humanisation*
(17 %) ; sur des vélocités de pianiste, la même permutation s'entend
comme une *destruction* (73 %). L'aléa n'améliore que ce qui était déjà
arbitraire. Et l'aplatissement, « nettoyage » d'un motif mécanique (20 %),
devient l'effacement d'un phrasé (71 %).

**Réserve : les contrôles, et pourquoi elle ne renverse pas le verdict.**
Sur 7 paires de contrôle (WAV identiques octet à octet), 2 seulement ont
reçu « aucune différence » — l'annotateur tranche 5 fois des paires
indiscernables. C'est un **critère libéral** (tendance à déclarer une
différence dans le doute), défaut classique en psychoacoustique, et le
protocole est construit pour le mesurer. Mais critère n'est pas
sensibilité : un choix forcé sur une paire indiscernable est une pièce
jetée par rapport à la position de l'original — positions exactement
équilibrées (24 A / 24 B) et aveugles, et les 5 contrôles tranchés se
répartissent d'ailleurs 3 A / 2 B, sans biais de côté. Les choix forcés
tirent donc le taux de détection **vers 50 %**, jamais au-dessus : ils
diluent, ils ne gonflent pas. Le 72 % mesuré au-dessus du hasard tient
malgré ce bruit — et sous-estime probablement la sensibilité réelle
(une partie des 46 paires tranchées l'a été au hasard). La réserve
honnête est ailleurs : les IC franchissent 0.5 de peu (0.51, 0.52), et
un second annotateur au critère plus conservateur resserrerait tout —
c'est la priorité qui reste.

**Le second volet de la grille : l'AUC moteur.** Mesurée sur les 24
extraits, 3 variantes par dégradation, confiance ≥ 0.5 :

| axe | flatten | scramble | global |
|---|---|---|---|
| `26_dynamic_range` | **0.93** | **0.93** | 0.93 |
| `29_global_cohesion` | 0.78 | 0.75 | 0.77 |
| `28_emotional_arc` | 0.75 | 0.58 | 0.67 |
| `02_section_balance` | 0.43 | 0.35 | 0.39 |

L'axe 26 discrimine fortement — sur la matière qui contient enfin sa
grandeur, il la mesure. L'axe 28 suit sur `flatten` (l'aplatissement tue
les arcs), plus faiblement sur `scramble`. L'axe 02 pointe à contresens :
les traits de frontière de la segmentation pondèrent la vélocité, donc
brouiller les vélocités déplace les frontières détectées — un effet de
bord connu désormais chiffré.

**Application de la grille** (engagée avant l'écoute) : dégradations
détectées par l'oreille ET l'AUC suit → la révision de poids de la
session 3 est **annulée à l'identique** (26 : 0.010 → 0.030, 28 :
0.030 → 0.050, et 12/16/17/20 reviennent au prior — appliqué au point
d'étape sur `scramble`, le lot complet ajoute `flatten` et ne change
rien aux poids). Sa prémisse — « aucun corrélat perceptif mesurable » —
est morte sur matière réelle. Le verdict des sessions 1-4 est
requalifié : ce n'était pas « la dynamique ne compte pas », c'était
« le corpus généré n'en contient pas ». Effet mesurable : l'accord
moteur/oreille passe de 36 % (poids réduits) à 52 % sur le lot complet
(poids restaurés) — le reste de l'écart vient des axes non dynamiques
qui, sur des paires ne différant que par les vélocités, n'apportent que
du bruit, l'axe 02 en tête.

La session 5 est close (55/55). Ce qu'elle laisse : un second annotateur
pour l'accord inter-annotateur et un critère plus conservateur. Le
correctif de répartition des contrôles est appliqué dans `build_tasks` —
chaque contrôle est réinséré dans sa tranche du lot à une position
imprévisible, donc tout préfixe en contient sa juste part — maintenant
que plus aucune session ne dépend des ids.

### Session 5b — second annotateur : répliqué

L'outillage est en place ; il manque une paire d'oreilles qui ne soit pas
celle des sessions 1-5.

**Lancer la session** (même corpus, même graine — c'est ce qui rend les
lots comparables) :

```bash
python3 -m libretto.cli annotate corpus_humain --render instrument \
    --only flatten_dynamics,scramble_dynamics \
    --out jugements5b.json --seed 5 --port 8793
# --host 0.0.0.0 si l'annotateur écoute depuis une autre machine du réseau
```

Les ids et positions A/B diffèrent du lot de la session 5 (contrôles
repositionnés depuis) — sans importance : le dépouillement joint sur
(fichier, dégradation) et compare la **version désignée** (original /
dégradé / aucune), jamais la lettre.

**Dépouiller** :

```bash
python3 -m libretto.cli agreement jugements5.json jugements5b.json
```

Trois lectures : κ de Cohen sur les paires communes (reproductibilité du
jugement individuel, corrigé des penchants de réponse), accord sur les
paires tranchées par les deux, et **verdict groupé** par dégradation —
tous jugements confondus, c'est lui qui resserre les intervalles (en
comptant des jugements, pas des paires : IC légèrement optimistes,
l'avertissement est dans le rapport).

**La consigne au second annotateur — et ce qu'on ne lui dit pas.** Lui
dire : deux versions du même extrait, désigner la mieux structurée,
« je n'entends pas de différence » est une réponse valide et *utile*,
aucune limite de temps. Ne PAS lui dire : quelles dégradations existent,
les taux de la session 5, ni qu'il y a des paires identiques dans le lot
— et ne pas lui faire lire ce fichier avant sa session. Le critère
conservateur ne se prescrit pas, il se mesure (ses contrôles le diront).

**Grille de décision, engagée avant l'écoute de 5b :**

- **verdict groupé par dégradation, IC entièrement > 0.5** → consolidé
  par deux oreilles ; les poids restaurés restent, la réserve de la
  session 5 tombe.
- **IC groupé chevauchant 0.5** → « non répliqué » : l'annulation de la
  révision est elle-même annulée (retour aux poids réduits 26 : 0.010,
  28 : 0.030) et le désaccord est documenté tel quel.
- **IC groupé < 0.5** → inversé à deux voix : idem, plus enquête — un
  tel renversement signalerait un artefact de protocole.

κ est rapporté comme contexte, pas comme porte : un κ modeste avec un
groupé net signifie « jugement individuel bruité, tendance commune
réelle » — c'est le cas normal en psychoacoustique. Cas particulier
utile : si les contrôles de B sont conservateurs (> 50 % de « aucune
différence ») et que sa détection à lui seul dépasse aussi 0.5, la
réserve « critère libéral » de la session 5 est levée par
triangulation, quel que soit κ.

**Résultat (55/55, second annotateur, 53 min d'écoute, médiane 48 s par
paire).**

| | A (session 5) | B (session 5b) | groupé |
|---|---|---|---|
| `flatten_dynamics` | 71 % [0.51–0.85] | 71 % [0.51–0.85] | **71 % [0.57–0.82]** |
| `scramble_dynamics` | 73 % [0.52–0.87] | 75 % [0.55–0.88] | **74 % [0.60–0.84]** |
| contrôles « aucune différence » | 2/7 | 0/7 | |

Les deux IC groupés sont entièrement au-dessus de 0.5 : la branche
« consolidé par deux oreilles » de la grille s'applique — **les poids
restaurés restent**, aucune modification de code.

**κ = −0.03, et c'est une bonne nouvelle — la signature de
l'indépendance.** Sur les 46 paires tranchées par les deux, l'accord
observé est 0.587 ; un modèle d'erreurs strictement indépendantes
(p_A = 0.72, p_B = 0.74) prédit 0.604 — l'écart est dans le bruit. Les
erreurs des deux annotateurs ne sont pas corrélées : pas de paires
« pièges » partagées, un signal diffus sur tout le lot et deux bruits
individuels. Détail qui compte : κ ≈ 0 **réfute l'hypothèse de la
copie** (des réponses recopiées donneraient κ ≈ 1) — les deux lots sont
bien deux paires d'oreilles. Les deux se trompent ensemble sur 3 paires
seulement ; au moins un des deux est juste sur 93 % du lot.

**La réserve « critère libéral », soldée par réplication.** B ne répond
jamais « aucune différence » (0 sur 55, contrôles 0/7) — la clause de
triangulation (un B conservateur) ne s'applique pas. Mais la réserve
tombe autrement : deux critères libéraux **indépendants** ne peuvent pas
fabriquer deux détections au-dessus du hasard, parce qu'un choix forcé
sur une paire indiscernable reste une pièce jetée face à des positions
équilibrées — il dilue vers 0.5 chez chacun, et l'indépendance (κ ≈ 0)
interdit qu'un même artefact les pousse du même côté. Deux mesures
indépendantes, même verdict : c'est la définition d'une réplication.
Reste vrai qu'aucun des deux n'utilise « aucune différence » ; un
troisième annotateur conservateur affinerait la mesure du critère, mais
n'est plus nécessaire au verdict.

Accord moteur pour B : 50 % — même diagnostic que pour A (sur des paires
ne différant que par les vélocités, les axes non dynamiques n'apportent
que du bruit) ; le juge pertinent reste l'AUC par axe, inchangée (même
corpus, mêmes dégradations : 26 à 0.93).

### L'axe 02, réparé — le dernier legs de la session 5

L'AUC de `02_section_balance` sortait à 0.39-0.48 sur les paires
dynamiques du corpus réel : l'axe votait pour la dégradation. Le
mécanisme, élucidé en décomposant les paires perdues, est double :

1. **Aplatir ou brouiller les vélocités abaisse le plancher du seuil
   adaptatif de nouveauté** (médiane + MAD de la courbe) : des pics de
   chroma jusque-là sous le seuil deviennent des frontières, et les
   sections longues se scindent. Verdi : `[4, 6, 15]` → `[4, 6, 9, 6]`.
2. **L'axe notait `1 − CV`**, dont l'optimum est l'uniformité parfaite :
   la version scindée, plus régulière, gagnait. Même maladie que les
   huit axes redressés à l'audit — une propriété statistique confondue
   avec une qualité musicale : deux idées courtes puis un long
   développement est une *forme*, pas un défaut.

Réparation (l'axe seulement — le seuil du builder porte la F-mesure de
segmentation, il n'est pas touché) : **plateau** `band(CV, 0–0.65)` —
un AABA régulier (CV 0) et un couplet 16 / pont 8 / coda 4 (CV 0.54)
scorent pareil, seule la dégénérescence chute (`[1, 1, 30]`, CV 1.28) ;
et **une section unique vaut 0.5 neutre**, plus 0.0 — l'ancien zéro
faisait perdre un extrait à travers-composé contre n'importe quelle
scission de lui-même (Rachmaninov : 0.000 → 0.943).

Résultat : AUC de l'axe 02 sur les paires dynamiques = **0.50 exactement,
partout** — que des égalités de plateau. L'axe ne vote plus pour la
dégradation ; il s'abstient là où il n'a rien à dire (la dynamique est le
travail de l'axe 26, à 0.93). Sur le corpus généré : experts 0.944 →
0.934 (le faux gradient rapportait des victoires), validation croisée
0.941 → **0.944** et surapprentissage +0.003 — l'axe gagné en robustesse
ce qu'il a perdu en gradient fictif.

### 2. Autres priorités

- Un second annotateur : **fait** (session 5b) — réplication indépendante,
  κ ≈ 0 signant la décorrélation des erreurs. Un troisième annotateur au
  critère conservateur affinerait la mesure du critère ; il n'est plus
  nécessaire au verdict.
- 20-25 jugements par dégradation, pour resserrer les intervalles.
- Répondre plus souvent « je n'entends pas de différence » : une seule
  réponse de ce type sur 58 a rendu les contrôles peu concluants.

`AUDIBLE_DEGRADATIONS` recense les quatre dégradations validées par
l'oreille. Les deux dégradations dynamiques en sont définitivement
absentes : réfutées puis inversées sur les trois étages du banc d'essai
(sessions 1-4). Elles restent dans le dictionnaire pour diagnostic, via
`--all-degradations`.
