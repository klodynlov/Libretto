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

### 2. Autres priorités

- Un second annotateur, pour estimer l'accord inter-annotateur — inconnu à
  ce jour.
- 20-25 jugements par dégradation, pour resserrer les intervalles.
- Répondre plus souvent « je n'entends pas de différence » : une seule
  réponse de ce type sur 58 a rendu les contrôles peu concluants.

`AUDIBLE_DEGRADATIONS` recense les quatre dégradations validées par
l'oreille. Les deux dégradations dynamiques en sont définitivement
absentes : réfutées puis inversées sur les trois étages du banc d'essai
(sessions 1-4). Elles restent dans le dictionnaire pour diagnostic, via
`--all-degradations`.
