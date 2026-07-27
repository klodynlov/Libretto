"""
Index d'un pack de loops MIDI : rôle, tonalité, tempo, longueur, famille.

Un pack du commerce est un tas de fichiers dont tout ce qui compte est dans
les noms — `Bonfire_Dm_100_Piano_Chords.mid` dit la chanson, la tonalité, le
tempo et l'instrument. Ce module lit ces noms (et les dossiers, qui portent
souvent la tonalité du kit), mesure ce qui ne s'y trouve pas (longueur,
polyphonie, tessiture), et range le tout dans un index JSON réutilisable.
`forge_loops.py` s'en sert pour arranger des morceaux.

    python3 examples/loop_index.py ~/Desktop/SAMPLES/MIDI --out /tmp/index.json

**La tonalité vient de l'étiquette, pas de l'estimateur.** Mesuré
(`scripts/mesure_tonalite.py`, voir README) : sur une boucle isolée,
Krumhansl-Kessler trouve la bonne tonique dans 40 % des cas, et ni la mise
en commun par kit ni la marge ne le sauvent. L'estimation n'est donc écrite
que pour les fichiers sans étiquette, et l'index dit toujours d'où vient
l'information (`source_tonalite`) — un consommateur qui préfère ne pas
parier peut filtrer dessus.

Le **rôle** est lu dans le nom quand il s'y trouve, et corrigé par le
contenu quand il n'y est pas : une piste dont la polyphonie moyenne dépasse
1.8 est un accompagnement harmonique, une monodie grave est une basse. Les
percussions sont repérées par le nom ou par le canal 9, et ne portent
jamais de tonalité — l'étiquette d'un snare est celle de son kit.

La **famille** est l'unité qui s'arrange ensemble : même dossier, même
tonalité, même tempo. Elle sépare toute seule `Bonfire_Dm_100`,
`Lonely_F_m_102` et `Moody_90_Bm` d'un même pack, là où le dossier les
mélange.

Aucun fichier du pack n'est copié ni modifié : l'index ne contient que des
chemins.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.axes import PC_NAMES, estimate_key  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402

BEATS_PER_BAR = 4

# ──────────────────────────────────────────────
# Étiquettes lues dans les noms
# ──────────────────────────────────────────────

# Tonique A-G, altération optionnelle, mode optionnel séparé ou collé
# (`Dm`, `F_m`, `Emin`, `Cmn`, `F#`, `C_maj`). Les gardes de part et d'autre
# évitent d'attraper l'initiale d'un mot : « Bonfire » n'est pas un si.
KEY_RE = re.compile(
    r"(?<![A-Za-z0-9#])([A-G])([#b]?)[ _\-.]?(minor|major|min|maj|mn|mi|m)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
BASE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Tempo : `100`, `96bpm`, `140.38 bpm`. Bornes larges mais pas absurdes —
# sans elles, le `02` d'un numéro de prise devient un tempo.
BPM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d{2,3}(?:\.\d+)?)\s*(bpm)?(?![A-Za-z0-9.])",
                    re.IGNORECASE)
BPM_MIN, BPM_MAX = 60.0, 200.0

# Rôles percussifs : l'étiquette du kit ne dit rien de leur hauteur.
DRUM_WORDS = (
    "kick", "snare", "hat", "hihat", "perc", "conga", "bongo", "shaker",
    "tamb", "clap", "snap", "drum", "tom", "crash", "ride", "rim", "cowbell",
    "triangle", "fill", "cym", "clave", "shekere", "udu", "djembe",
    "fall", "sweep", "riser", "impact", "fx",
)
# Rôles tonaux repérables dans le nom.
ROLE_WORDS = (
    "bass", "808", "chord", "pad", "pluck", "melod", "lead", "piano", "key",
    "guitar", "string", "brass", "sax", "flute", "arp", "marimba", "vox",
    "synth", "organ", "bell",
)
# Rôle nommé -> pupitre. Ce qui n'est pas nommé est tranché par le contenu.
PUPITRE = {
    "bass": "basse", "808": "basse",
    "chord": "harmonie", "pad": "harmonie", "piano": "harmonie",
    "key": "harmonie", "string": "harmonie", "organ": "harmonie",
    "guitar": "harmonie", "brass": "harmonie", "synth": "harmonie",
    "melod": "melodie", "lead": "melodie", "pluck": "melodie",
    "flute": "melodie", "sax": "melodie", "arp": "melodie",
    "marimba": "melodie", "vox": "melodie", "bell": "melodie",
}


def parse_key_label(text: str) -> tuple[int, str | None] | None:
    """(classe de hauteur, 'maj'|'min'|None) lue dans `text`, ou None.

    Une tonique **nue** — sans altération ni mode — n'est retenue qu'en
    capitale : `Moves_B_95` est un si, `d cymbl` est une convention de
    nommage de batterie. Renvoie None aussi quand le texte annonce deux
    toniques différentes : une étiquette contradictoire n'est pas une
    vérité terrain.
    """
    found: dict[int, str | None] = {}
    for m in KEY_RE.finditer(text):
        letter, acc, mode = m.group(1), m.group(2), m.group(3)
        if not acc and not mode and not letter.isupper():
            continue
        pc = BASE_PC[letter.upper()]
        if acc == "#":
            pc = (pc + 1) % 12
        elif acc and acc.lower() == "b":
            pc = (pc - 1) % 12
        norm = None
        if mode:
            norm = "maj" if mode.lower() in ("maj", "major") else "min"
        # un mode explicite l'emporte sur une occurrence muette de la même tonique
        if pc not in found or (norm and not found[pc]):
            found[pc] = norm
    if len(found) != 1:
        return None
    pc, mode = next(iter(found.items()))
    return pc, mode


def label_for(path: Path, root: Path) -> tuple[int, str | None, str] | None:
    """Étiquette du fichier, la mieux étayée d'abord.

    Une étiquette **avec mode** l'emporte sur une tonique nue, où qu'elles
    se trouvent — parce que ces packs nomment souvent chaque boucle par sa
    fondamentale locale à l'intérieur d'un kit dont le dossier, lui, annonce
    la tonalité : `KIT_1_PAD_SOULFUL_Eb_100BPM.mid` dans `KIT_1_Gmin_100BPM/`
    n'est pas un pad en mi bémol, c'est le VI d'un sol mineur. À force égale,
    le nom du fichier passe avant le dossier (plus précis). Le troisième
    champ dit d'où vient l'étiquette retenue.
    """
    candidates: list[tuple[int, str | None, str]] = []
    lab = parse_key_label(path.stem)
    if lab:
        candidates.append((lab[0], lab[1], "fichier"))
    for parent in path.relative_to(root).parents:
        if str(parent) in (".", ""):
            continue
        lab = parse_key_label(parent.name)
        if lab:
            candidates.append((lab[0], lab[1], "dossier"))
    if not candidates:
        return None
    with_mode = [c for c in candidates if c[1]]
    return with_mode[0] if with_mode else candidates[0]


def parse_bpm(text: str) -> float | None:
    """Tempo lu dans un nom. Un nombre suivi de « bpm » l'emporte sur un
    nombre nu — `SOSO_02_96bpm` a un numéro de prise ET un tempo."""
    marques, nus = [], []
    for m in BPM_RE.finditer(text):
        val = float(m.group(1))
        if not BPM_MIN <= val <= BPM_MAX:
            continue
        (marques if m.group(2) else nus).append(val)
    for source in (marques, nus):
        if source:
            return source[0]
    return None


def bpm_for(path: Path, root: Path) -> tuple[float | None, str | None]:
    for texte, source in [(path.stem, "fichier")] + [
            (p.name, "dossier") for p in path.relative_to(root).parents
            if str(p) not in (".", "")]:
        val = parse_bpm(texte)
        if val:
            return val, source
    return None, None


def role_of(path: Path) -> str:
    low = path.stem.lower()
    for w in DRUM_WORDS:
        if w in low:
            return "batterie"
    for w in ROLE_WORDS:
        if w in low:
            return w
    return "?"


def hist_brut(md) -> list[float]:
    """Histogramme des classes de hauteur pondéré par la durée, canal 9 exclu."""
    h = [0.0] * 12
    for n in md.notes:
        if n.channel == 9:
            continue
        h[n.pitch % 12] += max(1, n.end - n.start)
    return h


# ──────────────────────────────────────────────
# Index
# ──────────────────────────────────────────────

@dataclass
class Loop:
    chemin: str
    pack: str
    dossier: str            # dossier relatif — le « kit »
    famille: str            # dossier + tonalité + tempo : ce qui s'arrange ensemble
    nom: str
    role: str               # étiquette de nom (`chord`, `808`, `batterie`, `?`)
    pupitre: str            # basse | harmonie | melodie | batterie
    tonique: int | None
    mode: str | None
    source_tonalite: str | None   # fichier | dossier | estime
    marge_kk: float | None        # marge Krumhansl-Kessler si estimée
    bpm: float | None
    source_bpm: str | None
    beats: float
    mesures: int
    n_notes: int
    polyphonie: float
    hauteur_mediane: int | None
    # (plus grave, plus aiguë) : ce qu'il faut pour empiler deux pupitres
    # sans qu'ils se marchent dessus — voir `forge_loops.REGISTRES`.
    ambitus: list[int] | None = None
    classes: list[int] = field(default_factory=list)


def _polyphonie(md) -> float:
    """Notes par attaque distincte : 1.0 = monodie, ≥ 2 = accords."""
    onsets = {n.start for n in md.notes if n.channel != 9}
    tonales = [n for n in md.notes if n.channel != 9]
    return len(tonales) / len(onsets) if onsets else 0.0


def _pupitre(role: str, poly: float, mediane: int | None, percussif: bool) -> str:
    """Le nom d'abord, le contenu quand le nom se tait.

    Un pack sur cinq nomme ses fichiers `loop_03.mid` : sans lecture du
    contenu, un tiers de la matière serait inclassable et donc perdue.
    """
    if percussif or role == "batterie":
        return "batterie"
    if role in PUPITRE:
        return PUPITRE[role]
    if mediane is not None and mediane <= 52 and poly < 1.8:
        return "basse"
    return "harmonie" if poly >= 1.8 else "melodie"


def index_pack(root: Path, estimer: bool = True,
               marge_min: float = 0.10) -> tuple[list[Loop], Counter]:
    """Parcourt un pack et renvoie (loops, statistiques).

    `estimer` autorise Krumhansl-Kessler pour les fichiers **sans**
    étiquette, et seulement au-dessus de `marge_min` — un pari assumé,
    tracé par `source_tonalite="estime"`, jamais mélangé aux étiquettes.
    """
    stats = Counter()
    loops: list[Loop] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in (".mid", ".midi"):
            continue
        stats["total"] += 1
        try:
            md = parse_midi(path)
        except Exception:
            stats["illisibles"] += 1
            continue
        if not md.notes:
            stats["vides"] += 1
            continue

        rel = path.relative_to(root)
        role = role_of(path)
        percussif = sum(1 for n in md.notes if n.channel == 9) / len(md.notes) >= 0.8
        poly = _polyphonie(md)
        tonales = [n for n in md.notes if n.channel != 9]
        mediane = sorted(n.pitch for n in tonales)[len(tonales) // 2] if tonales else None
        pupitre = _pupitre(role, poly, mediane, percussif)

        tonique = mode = source_ton = None
        marge = None
        if pupitre != "batterie":
            lab = label_for(path, root)
            if lab:
                tonique, mode, source_ton = lab
            elif estimer and tonales:
                pc, m, _corr, marge_kk = estimate_key(hist_brut(md))
                if marge_kk >= marge_min:
                    tonique, mode, source_ton, marge = pc, m, "estime", round(marge_kk, 3)
                    stats["tonalite_estimee"] += 1
                else:
                    stats["tonalite_inconnue"] += 1
            else:
                stats["tonalite_inconnue"] += 1

        bpm, source_bpm = bpm_for(path, root)
        if bpm is None and md.tempos:
            bpm, source_bpm = round(md.tempos[0][1], 2), "midi"

        beats = md.end_tick / md.ppq if md.ppq else 0.0
        mesures = max(1, math.ceil(beats / BEATS_PER_BAR)) if beats else 1
        # Famille : ce qui peut s'arranger ensemble sans réfléchir. Le tempo
        # est arrondi — un pack écrit 100 ici et 100.02 là.
        cle_ton = f"{PC_NAMES[tonique]}{mode or ''}" if tonique is not None else "?"
        famille = f"{rel.parent}|{cle_ton}|{round(bpm) if bpm else '?'}"

        loops.append(Loop(
            chemin=str(path), pack=rel.parts[0] if len(rel.parts) > 1 else "",
            dossier=str(rel.parent), famille=famille, nom=path.stem,
            role=role, pupitre=pupitre,
            tonique=tonique, mode=mode, source_tonalite=source_ton, marge_kk=marge,
            bpm=bpm, source_bpm=source_bpm,
            beats=round(beats, 3), mesures=mesures, n_notes=len(md.notes),
            polyphonie=round(poly, 2), hauteur_mediane=mediane,
            ambitus=([min(n.pitch for n in tonales), max(n.pitch for n in tonales)]
                     if tonales else None),
            classes=sorted({n.pitch % 12 for n in tonales}),
        ))
        stats[pupitre] += 1
    return loops, stats


def save_index(loops: list[Loop], stats: Counter, root: Path, out: Path) -> None:
    out.write_text(json.dumps(
        {"racine": str(root), "stats": dict(stats),
         "loops": [asdict(v) for v in loops]},
        ensure_ascii=False, indent=1), encoding="utf-8")


def load_index(path: Path) -> tuple[list[Loop], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Loop(**d) for d in data["loops"]], data


def ensure_index(source: Path, index_path: Path | None,
                 estimer: bool = True) -> tuple[list[Loop], dict]:
    """Charge l'index s'il existe, le construit sinon. `source` peut être un
    dossier de pack ou un index déjà écrit."""
    source = Path(source).expanduser().resolve()
    if source.is_file():
        return load_index(source)
    if index_path and Path(index_path).exists():
        return load_index(Path(index_path))
    loops, stats = index_pack(source, estimer=estimer)
    data = {"racine": str(source), "stats": dict(stats),
            "loops": [asdict(v) for v in loops]}
    if index_path:
        Path(index_path).parent.mkdir(parents=True, exist_ok=True)
        save_index(loops, stats, source, Path(index_path))
    return loops, data


def familles(loops: list[Loop]) -> dict[str, list[Loop]]:
    out: dict[str, list[Loop]] = {}
    for lp in loops:
        out.setdefault(lp.famille, []).append(lp)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("pack", help="dossier du pack MIDI")
    ap.add_argument("--out", default="loops_index.json", help="index JSON de sortie")
    ap.add_argument("--sans-estimation", action="store_true",
                    help="ne pas estimer la tonalité des fichiers non étiquetés")
    args = ap.parse_args(argv)

    root = Path(args.pack).expanduser().resolve()
    loops, stats = index_pack(root, estimer=not args.sans_estimation)
    save_index(loops, stats, root, Path(args.out))

    fam = familles(loops)
    utilisables = {k: v for k, v in fam.items()
                   if len({lp.pupitre for lp in v} & {"harmonie", "basse", "melodie"}) >= 2}
    print(f"{stats['total']} fichiers → {len(loops)} indexés "
          f"({stats['illisibles']} illisibles, {stats['vides']} vides)")
    print("  pupitres : " + " · ".join(
        f"{p} {stats[p]}" for p in ("harmonie", "melodie", "basse", "batterie")))
    etiquetees = sum(1 for lp in loops if lp.source_tonalite in ("fichier", "dossier"))
    print(f"  tonalité : {etiquetees} étiquetées · {stats['tonalite_estimee']} estimées"
          f" · {stats['tonalite_inconnue']} inconnues")
    print(f"  {len(fam)} familles, dont {len(utilisables)} arrangeables "
          f"(≥ 2 pupitres tonaux)")
    for nom, v in sorted(utilisables.items(), key=lambda kv: -len(kv[1]))[:8]:
        pupitres = Counter(lp.pupitre for lp in v)
        print(f"    {len(v):3d} loops  {nom}  {dict(pupitres)}")
    print(f"index → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
