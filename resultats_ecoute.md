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

## Suite proposée

1. Remplacer `flatten_dynamics` par une **randomisation** des vélocités
   plutôt qu'un aplatissement. Hypothèse musicale : l'incohérence dynamique
   s'entend comme un défaut, là où l'uniformité passe pour de la production.
   À valider par une seconde session — ne pas l'adopter sur la seule
   intuition.
2. Refaire une session avec un second annotateur pour estimer l'accord
   inter-annotateur.
3. Étendre à 20-25 jugements par dégradation pour resserrer les intervalles.

En attendant, `flatten_dynamics` est conservée mais signalée dans
`calibrate.py`, et les poids calibrés qui en dépendent sont à considérer
comme provisoires.
