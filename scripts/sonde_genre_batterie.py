"""Sonde : y a-t-il du signal de GENRE dans le seul rythme de batterie ?

Usage :
    python3 scripts/sonde_genre_batterie.py "~/Desktop/MIDI Loops Packs/Toontrack - EZDrummer MIDI loops/BY TYPE"

Libretto analyse un **morceau entier** (forme, harmonie, mélodie) : sur une
boucle de batterie de deux mesures il n'y a rien de tout ça, et le README le
dit — « ni forme, ni harmonie, ni mélodie à mesurer ». Une boucle de batterie
n'est donc pas matière à SMS. La question honnête devient : ce que Libretto
**ne modélise pas encore** — le groove — porte-t-il assez d'information pour
mériter un axe ? Ce n'est pas une validation d'un détecteur existant, c'est une
sonde : mesurer s'il y a du signal avant d'écrire le code qui l'exploiterait.

Protocole minimal, volontairement bête (une empreinte rythmique + un plus-
proche-centroïde), pour que tout signal trouvé soit un plancher, pas un
plafond :

  · empreinte = onsets repliés sur une grille de 16 pas par mesure, sur trois
    voix de la batterie General MIDI (grosse caisse / caisse claire / cymbales-
    charley), normalisée — 48 dimensions, aucune hauteur, aucun tempo ;
  · classification = plus proche centroïde de genre, en validation
    leave-one-out (le loop testé est retiré de son centroïde).

Les genres viennent des dossiers `BY TYPE/<GENRE>` d'EZDrummer.

Résultat mesuré (10 genres, 70 loops/genre, graine 7) :

    plus-proche-centroïde LOO : 249/700 = 35.6 %   (hasard 10.0 %)

3,6× le hasard avec du rythme **seul** — le signal existe. Et les confusions
sont musicalement justes, pas du bruit : BLUES↔JAZZ (le shuffle commun),
DISCO→FUNK, REGGAE→LATIN, POP→HIP-HOP. Les genres voisins se mélangent, les
lointains se séparent. Conclusion : un axe de groove/genre rythmique est
fondé sur des données ; ce plancher (empreinte naïve) montre par où commencer.

100 % stdlib. Le pack est commercial (hors dépôt) : on passe son chemin en
argument, le script ne versionne rien d'autre que lui-même.
"""
from __future__ import annotations

import collections
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.midi import parse_midi  # noqa: E402

GENRES = ["HIP-HOP", "JAZZ", "REGGAE", "METAL", "LATIN", "FUNK", "DISCO", "COUNTRY", "BLUES", "POP"]
PER = 70          # loops échantillonnés par genre
STEPS = 16        # subdivisions par mesure (double-croche en 4/4)
SEED = 7
MIN_LOOPS = 20    # genre gardé au-delà de ce nombre de loops lisibles


def voice(pitch: int):
    """Classe GM -> voix (0 grosse caisse, 1 caisse claire, 2 cymbales). None sinon."""
    if pitch in (35, 36):
        return 0
    if pitch in (37, 38, 39, 40):
        return 1
    if pitch in (42, 44, 46, 49, 51, 52, 53, 55, 57, 59):
        return 2
    return None


def fingerprint(md):
    """Empreinte 3 voix × 16 pas, repliée sur la mesure, normalisée (somme 1)."""
    if not md.notes:
        return None
    ppq = md.ppq or 480
    ticks_per_bar = ppq * 4          # 4/4 : BY TYPE en est massivement
    step = ticks_per_bar / STEPS
    grid = [[0.0] * STEPS for _ in range(3)]
    n = 0
    for note in md.notes:
        v = voice(note.pitch)        # mappe par hauteur GM, quel que soit le canal
        if v is None:
            continue
        grid[v][int((note.start % ticks_per_bar) / step) % STEPS] += 1.0
        n += 1
    if n < 4:
        return None
    flat = [x for row in grid for x in row]
    s = sum(flat)
    return [x / s for x in flat] if s else None


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def centroid(vecs):
    m = len(vecs[0])
    c = [0.0] * m
    for v in vecs:
        for i in range(m):
            c[i] += v[i]
    return [x / len(vecs) for x in c]


def load(root: Path):
    rng = random.Random(SEED)
    data = collections.defaultdict(list)
    for g in GENRES:
        d = root / g
        if not d.is_dir():
            continue
        files = list(d.rglob("*.mid"))
        rng.shuffle(files)
        for p in files:
            if len(data[g]) >= PER:
                break
            try:
                md = parse_midi(p)
            except Exception:
                continue
            fp = fingerprint(md)
            if fp:
                data[g].append(fp)
        print(f"{g:9s}: {len(data[g])} loops")
    return data


def main(argv: list[str]) -> int:
    default = Path.home() / "Desktop/MIDI Loops Packs/Toontrack - EZDrummer MIDI loops/BY TYPE"
    root = Path(argv[1]).expanduser() if len(argv) > 1 else default
    if not root.is_dir():
        print(f"racine BY TYPE introuvable : {root}\n"
              f"usage : python3 scripts/sonde_genre_batterie.py <racine_BY_TYPE>", file=sys.stderr)
        return 2
    print(f"racine : {root}")
    data = load(root)

    genres = [g for g in GENRES if len(data[g]) >= MIN_LOOPS]
    X = [(g, fp) for g in genres for fp in data[g]]
    if len(genres) < 2:
        print("pas assez de genres peuplés pour une classification.", file=sys.stderr)
        return 1
    chance = 100 / len(genres)
    print(f"\ntotal {len(X)} loops, {len(genres)} genres, hasard = {chance:.1f}%")

    ok = 0
    conf = collections.Counter()
    for idx, (gtrue, fp) in enumerate(X):
        cents = {g: centroid([v for j, (gg, v) in enumerate(X) if gg == g and j != idx])
                 for g in genres}
        pred = max(genres, key=lambda g: cosine(fp, cents[g]))
        if pred == gtrue:
            ok += 1
        else:
            conf[(gtrue, pred)] += 1
    acc = 100 * ok / len(X)
    print(f"\nplus-proche-centroïde LOO : {ok}/{len(X)} = {acc:.1f}%  "
          f"(hasard {chance:.1f}%, soit {acc / chance:.1f}× le hasard)")
    print("confusions les + fréquentes :")
    for (t, p), v in sorted(conf.items(), key=lambda x: -x[1])[:10]:
        print(f"  {t:9s} -> {p:9s} : {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
