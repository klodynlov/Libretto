# Corpus bkmaf — analyses SMS (variété créole / zouk antillais)

**55 morceaux** de variété antillaise/créole (zouk, biguine, séga-seggae, kadans),
récupérés depuis **bkmaf.com** — *La Bibliothèque Kar & Midi des Artistes Francophones*,
bibliothèque **gratuite, financée par les dons** (catalogue individuel libre, sans compte).

Deux sous-ensembles :
- `musique-creole/` — **n=44**, variété créole antillaise (« Spécial Musique Créole »).
- `zouk-pur/` — **n=11**, zouk canonique (Kassav, Zouk Machine, Malavoi, Gilles Floro).

## Ce qui est versé ici
`manifest.json` = **les analyses**, pas les MIDI. Chaque entrée = sortie de
`libretto.library.analyze_entry` :
- empreinte **29 axes SMS** (`axes`), score global, confiance ;
- **clé** estimée (tonic/mode, modes inclus : mixolydien, dorien…), marge Krumhansl-Kessler ;
- **bpm** et **mesures** estimés ; profil **émotion** (valence/énergie/tension) ;
- `sha1` du fichier (intégrité), `tags` (`creole`/`zouk`, `antilles`, `bkmaf`), chemin **relatif** (`group/basename`).

## Usage / licence
**Analyses locales uniquement.** Les MIDI (`.mid`/`.kar`) ne sont **pas redistribués**
(musique réelle) et ne sont **pas commités** — comme `corpus_humain/` (MAESTRO). Le corpus
est reproductible depuis `~/bkmaf-dl`. Régénérer le manifest :

```
python examples/build_bkmaf_manifest.py    # -> corpus_bkmaf/manifest.json
```

## À quoi ça a servi
- Confirme la cible **zouk** de klod-session une 4ᵉ fois (zouk-pur n=11 vs cible : MAE 0.065, empreinte identique sur basse mélodique / tempo verrouillé / harmonie).
- A fondé la nouvelle cible **`creole`** (n=44) de `klod-session/genre_profiles.py`.
- Plus gros lot antillais passé dans le SMS Libretto (55 vs corpus d'origine 17).
