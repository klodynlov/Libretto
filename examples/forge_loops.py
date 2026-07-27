"""
Forge sur loops : arranger de la matière humaine, laisser le SMS trancher.

`forge.py` tire ses candidats d'un générateur synthétique ; `forge_musiclang`
d'un transformer ; `forge_acestep` d'une transcription. Il manquait la
source la plus banale d'un studio : **un pack de loops**. Les progressions,
les voicings et les riffs y sont écrits par des humains — c'est exactement
la matière que le générateur de `make_corpus` ne sait pas inventer (groupes
B et C), et c'est ce qui reste hors de portée d'une transcription (−0.18 de
score, mesuré).

Ce que Forge apporte par-dessus, c'est ce que le pack n'a pas : une
**forme**. Un pack livre huit boucles de quatre mesures, pas une chanson.
Chaque candidat tire un plan (forme, longueur des sections, effectif,
courbe d'énergie, parfois une modulation), tuile les boucles dessus, écrit
les marqueurs — puis Libretto note, et le classement fiabilité-d'abord
choisit. La recherche porte sur l'arrangement, la matière ne bouge pas.

    python3 examples/loop_index.py ~/Desktop/SAMPLES/MIDI --out /tmp/index.json
    python3 examples/forge_loops.py /tmp/index.json sortie/ 24 1 --axes --shortlist 5

Le premier argument accepte aussi directement le dossier du pack (l'index
est alors construit en mémoire).

Trois choix assumés :

  · **la tonalité vient de l'étiquette du pack**, pas de l'estimateur —
    mesuré à 0.400 sur une boucle isolée (`scripts/mesure_tonalite.py`).
    Une famille est un dossier + une tonalité + un tempo : ce qui s'arrange
    ensemble sans transposer ;
  · **les percussions se prêtent, pas les hauteurs.** Une batterie n'a pas
    de tonalité : elle peut venir d'une autre famille du même dossier à
    tempo voisin. Un instrument tonal, non — sauf emprunt explicite,
    transposé, et seulement à mode égal ;
  · **la circularité est réelle.** Optimiser un arrangement contre le juge
    qui le note ne prouve pas qu'il sonne mieux ; ça prouve qu'il est mieux
    construit *au sens des 29 axes*. Livrer une `--shortlist` à une oreille
    reste la seule validation — le harnais existe (`annotate`/`agreement`).
"""

from __future__ import annotations

import argparse
import math
import random as _random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.axes import (PARENT_MODE, PC_NAMES,  # noqa: E402
                           SenseOfMusicalStructure)
from libretto.midi import parse_midi, write_midi  # noqa: E402

from forge import (_print_axes_report, _print_report,  # noqa: E402
                   _print_shortlist, _select_and_report, judge_midi)
from loop_index import BEATS_PER_BAR, Loop, ensure_index, familles  # noqa: E402
from make_corpus import FORMS, MARKER_NAMES, ROLE_ENERGY  # noqa: E402

# Canaux MIDI : 9 est réservé aux percussions (Libretto les exclut du chroma
# et de la mélodie). Les pupitres tonaux ont chacun les leurs, ceux que
# `libretto/render.py::GM_PROGRAMS` associe à l'instrument correspondant —
# l'analyse ne lit que « canal 9 ou pas », mais une écoute rendue à
# FluidSynth joue une basse comme une basse et non comme un piano grave.
DRUMS_CHANNEL = 9
CANAUX = {"harmonie": [0, 3, 5, 6], "basse": [1, 7], "melodie": [2, 4, 8]}

# Vélocité : bornes serrées à dessein. L'axe 26 pénalise une gamme dynamique
# au-delà de 45, et une boucle poussée à 127 s'entend comme une saturation.
VEL_MIN, VEL_MAX = 25, 118

# Courbes d'énergie par section, appliquées PAR-DESSUS ROLE_ENERGY. L'arche
# est la forme que l'axe 28 récompense ; les autres existent pour que le
# peloton ne soit pas fait de clones.
ARCS = ("arche", "arche", "montee", "plat")

# Registre visé par pupitre (hauteur médiane MIDI). Les boucles d'un pack
# sont écrites chacune dans son coin : un pad peut se retrouver au-dessus de
# la mélodie, et comme Libretto lit la mélodie dans la **voix supérieure**,
# elle devient alors le sommet des accords — la ligne mesurée n'est plus une
# ligne. Recaler chaque pupitre à l'octave (multiple de 12 : la tonalité ne
# bouge pas) est ce que ferait un arrangeur, et ça rend la mélodie lisible
# pour l'analyse comme pour l'oreille. Mesuré (48 candidats, graine 3) :
# groupe C 0.570 → 0.597, meilleur score 0.885 → 0.908.
REGISTRES = {"basse": (28, 48), "harmonie": (50, 69), "melodie": (69, 88)}


@dataclass
class Plan:
    """Un arrangement tiré : tout ce qui distingue un candidat d'un autre."""
    famille: str
    forme: str
    sections: list[tuple[str, int, float]]   # (étiquette, mesures, énergie)
    pupitres: dict[str, list[Loop]]
    bpm: float
    tonique: int | None
    mode: str | None
    arc: str
    modulation: tuple[int, int]              # (section index, demi-tons), (0, 0) sinon
    emprunt: str | None                      # loop empruntée à une autre famille
    # Transposition par loop, en demi-tons. Portée par le plan et non par la
    # boucle : l'index est partagé par tous les candidats, y écrire
    # contaminerait les tirages suivants.
    transpositions: dict[str, int]


# ──────────────────────────────────────────────
# Matière : familles utilisables
# ──────────────────────────────────────────────

def familles_arrangeables(loops: list[Loop], min_pupitres: int = 2
                          ) -> dict[str, list[Loop]]:
    """Une famille s'arrange si elle a de quoi faire un morceau : au moins
    deux pupitres tonaux distincts. Une famille de huit pads ne compte que
    pour un — elle n'a pas de contrepoint à offrir, et Libretto le verrait
    (texture, polyphonie, mélodie à plat)."""
    out = {}
    for nom, membres in familles(loops).items():
        tonaux = {lp.pupitre for lp in membres} & {"harmonie", "basse", "melodie"}
        if len(tonaux) >= min_pupitres:
            out[nom] = membres
    return out


def batteries_compatibles(loops: list[Loop], famille: str, bpm: float | None,
                          tolerance: float = 0.08) -> list[Loop]:
    """Percussions jouables sous cette famille : les siennes d'abord, sinon
    celles du même dossier à tempo voisin. Une batterie n'a pas de tonalité,
    l'emprunt ne coûte rien — c'est le seul pupitre dans ce cas."""
    dossier = famille.split("|")[0]
    propres = [lp for lp in loops if lp.famille == famille and lp.pupitre == "batterie"]
    if propres:
        return propres
    return [lp for lp in loops
            if lp.pupitre == "batterie" and lp.dossier == dossier
            and (bpm is None or lp.bpm is None
                 or abs(lp.bpm - bpm) <= tolerance * bpm)]


def emprunt_melodique(loops: list[Loop], plan_famille: list[Loop], famille: str,
                      tonique: int | None, mode: str | None, bpm: float | None,
                      rng: _random.Random) -> tuple[Loop, int] | None:
    """Une mélodie d'une AUTRE famille, transposée dans la tonalité courante.

    Élargit la matière sans mentir sur ce qu'on fait : même pack, tempo
    voisin, **mode identique** (transposer un majeur dans un mineur ne
    transpose pas le mode), et tonalité des deux côtés connue — sinon
    l'intervalle de transposition est une invention.
    """
    if tonique is None or mode is None:
        return None
    pack = plan_famille[0].pack
    candidats = [lp for lp in loops
                 if lp.pupitre == "melodie" and lp.famille != famille
                 and lp.pack == pack and lp.tonique is not None
                 and PARENT_MODE.get(lp.mode) == PARENT_MODE.get(mode)
                 and lp.source_tonalite in ("fichier", "dossier")
                 and (bpm is None or lp.bpm is None
                      or abs(lp.bpm - bpm) <= 0.08 * bpm)]
    if not candidats:
        return None
    lp = rng.choice(candidats)
    # intervalle replié dans [-5, +6] : transposer d'une octave n'a pas de sens
    demi = (tonique - lp.tonique) % 12
    if demi > 6:
        demi -= 12
    return lp, demi


# ──────────────────────────────────────────────
# Tirage d'un plan
# ──────────────────────────────────────────────

def _energie(label: str, index: int, total: int, arc: str) -> float:
    """Énergie d'une section : son rôle, modulé par l'arc du morceau."""
    base = ROLE_ENERGY.get(label, 0.8)
    if total <= 1:
        return base
    pos = index / (total - 1)
    if arc == "arche":
        facteur = 0.88 + 0.24 * math.sin(math.pi * min(1.0, pos * 1.15))
    elif arc == "montee":
        facteur = 0.85 + 0.30 * pos
    else:
        facteur = 1.0
    return base * facteur


def tirer_plan(loops: list[Loop], arrangeables: dict[str, list[Loop]],
               rng: _random.Random, famille_imposee: str | None = None) -> Plan | None:
    noms = ([n for n in arrangeables if famille_imposee in n] if famille_imposee
            else list(arrangeables))
    if not noms:
        return None
    # Pondéré par la richesse : une famille de douze boucles offre plus de
    # variantes d'effectif qu'une de trois, et la sélection a besoin d'un
    # peloton qui ne soit pas fait de jumeaux.
    famille = rng.choices(noms, weights=[min(len(arrangeables[n]), 12) for n in noms])[0]
    membres = arrangeables[famille]

    pupitres: dict[str, list[Loop]] = {}
    for lp in membres:
        pupitres.setdefault(lp.pupitre, []).append(lp)
    pupitres["batterie"] = batteries_compatibles(loops, famille, membres[0].bpm)
    # La distribution est tirée une fois pour tout le morceau : les sections
    # doivent partager leurs instruments, sinon la « texture » change à
    # chaque section pour rien et le matériau ne revient jamais (axe 6).
    # D'un candidat à l'autre, en revanche, la distribution change.
    for v in pupitres.values():
        rng.shuffle(v)

    tonique = next((lp.tonique for lp in membres if lp.tonique is not None), None)
    mode = next((lp.mode for lp in membres if lp.mode is not None), None)
    bpm = next((lp.bpm for lp in membres if lp.bpm is not None), 100.0)

    emprunt = None
    transpositions: dict[str, int] = {}
    if rng.random() < 0.35:
        pris = emprunt_melodique(loops, membres, famille, tonique, mode, bpm, rng)
        if pris:
            lp, demi = pris
            pupitres.setdefault("melodie", []).insert(0, lp)
            emprunt = f"{lp.nom} ({demi:+d})"
            transpositions[lp.chemin] = demi

    forme = rng.choice(list(FORMS))
    mesures = rng.choice([4, 4, 8, 8, 16])
    labels = FORMS[forme]
    arc = rng.choice(ARCS)
    sections = [(lab, mesures, _energie(lab, i, len(labels), arc))
                for i, lab in enumerate(labels)]

    # Modulation : les axes 11 et 13 la mesurent, et un pack n'en produit
    # jamais tout seul — toutes ses boucles sont dans la même tonalité.
    modulation = (0, 0)
    if len(labels) >= 4 and rng.random() < 0.30:
        modulation = (rng.randrange(len(labels) // 2, len(labels)),
                      rng.choice([-3, -2, 2, 3]))

    return Plan(famille=famille, forme=forme, sections=sections, pupitres=pupitres,
                bpm=bpm, tonique=tonique, mode=mode, arc=arc,
                modulation=modulation, emprunt=emprunt,
                transpositions=transpositions)


# ──────────────────────────────────────────────
# Rendu
# ──────────────────────────────────────────────

def octave_de_registre(lp: Loop) -> int:
    """Décalage en octaves qui amène la boucle dans le registre de son
    pupitre. Renvoie 0 si elle y est déjà, ou si elle n'a pas de hauteur
    (percussions)."""
    bande = REGISTRES.get(lp.pupitre)
    if bande is None or lp.hauteur_mediane is None:
        return 0
    bas, haut = bande
    if bas <= lp.hauteur_mediane <= haut:
        return 0
    centre = (bas + haut) / 2
    return max(-24, min(24, round((centre - lp.hauteur_mediane) / 12) * 12))


def octaves_du_plan(plan: Plan) -> dict[str, int]:
    """Décalages d'octave de tout l'effectif, décidés une fois pour le morceau.

    Une seule règle : chaque pupitre rejoint son registre. Une seconde a été
    essayée et **retirée** — pousser tout l'accompagnement *sous* la note la
    plus grave de la mélodie, pour que la voix supérieure que lit Libretto
    soit à coup sûr la mélodie. L'hypothèse était que le groupe C s'effondre
    (0.597 contre 0.834 pour le générateur synthétique) parce que la ligne
    mesurée est un sommet d'accords. Mesuré sur 48 candidats, graine 3 :
    groupe C **inchangé** (0.597), meilleur score 0.908 → 0.899. L'hypothèse
    est fausse, la règle est partie ; ce qui reste du diagnostic est dans le
    README.
    """
    return {lp.chemin: octave_de_registre(lp)
            for pupitre in plan.pupitres.values() for lp in pupitre}


def charger(lp: Loop, canal: int, transpose: int = 0
            ) -> tuple[list[tuple[float, float, int, int, int]], float]:
    """(événements normalisés en beats, période musicale en beats).

    La période est arrondie à la mesure supérieure : un pack exporte des
    boucles de 7.94 mesures qui en valent 8, et tuiler sur la longueur brute
    ferait dériver toutes les entrées de section.
    """
    md = parse_midi(Path(lp.chemin))
    events = []
    for n in md.notes:
        start = n.start / md.ppq
        dur = max(0.05, (n.end - n.start) / md.ppq)
        pitch = n.pitch if canal == DRUMS_CHANNEL else min(126, max(1, n.pitch + transpose))
        events.append((start, dur, pitch, n.velocity, canal))
    brut = md.end_tick / md.ppq if md.ppq else 0.0
    periode = max(BEATS_PER_BAR,
                  math.ceil(brut / BEATS_PER_BAR) * BEATS_PER_BAR) if brut else BEATS_PER_BAR
    return events, float(periode)


def _effectif(plan: Plan, energie: float, rng: _random.Random) -> list[tuple[Loop, int]]:
    """Qui joue dans cette section, et sur quel canal.

    Un morceau dont l'effectif ne bouge pas est plat pour les axes 25 et 27,
    et faux pour l'oreille : une intro est dépouillée, un refrain est plein.
    Les seuils sont ceux de l'énergie de section, pas un tirage — c'est ce
    qui rend la courbe lisible d'un bout à l'autre.
    """
    choix: list[tuple[Loop, int]] = []

    def prendre(pupitre: str, n: int) -> None:
        dispo = plan.pupitres.get(pupitre) or []
        canaux = CANAUX[pupitre]
        for i, lp in enumerate(dispo[:n]):
            choix.append((lp, canaux[i % len(canaux)]))

    prendre("harmonie", 2 if energie >= 0.9 else 1)
    if energie >= 0.68:
        prendre("basse", 1)
    if energie >= 0.85:
        prendre("melodie", 2 if energie >= 1.0 else 1)
    elif energie >= 0.72 and rng.random() < 0.4:
        prendre("melodie", 1)

    perc = plan.pupitres.get("batterie") or []
    if perc and energie >= 0.62:
        n = 1 if energie < 0.72 else (2 if energie < 0.9 else min(4, len(perc)))
        for lp in perc[:n]:
            choix.append((lp, DRUMS_CHANNEL))
    return choix


def rendre(plan: Plan, path: Path, rng: _random.Random) -> int:
    """Écrit le MIDI du plan. Renvoie le nombre total de mesures."""
    cache: dict[str, tuple[list, float]] = {}
    pistes: dict[str, list] = {}
    markers: list[tuple[float, str]] = []
    tempo_changes: list[tuple[float, float]] = []
    octaves = octaves_du_plan(plan)

    beat = 0.0
    mod_index, mod_demi = plan.modulation
    for i, (label, mesures, energie) in enumerate(plan.sections):
        markers.append((beat, MARKER_NAMES.get(label, label)))
        decalage = mod_demi if mod_index and i >= mod_index else 0
        section_beats = mesures * BEATS_PER_BAR

        for lp, canal in _effectif(plan, energie, rng):
            cle = f"{lp.chemin}|{canal}"
            if cle not in cache:
                cache[cle] = charger(lp, canal,
                                     plan.transpositions.get(lp.chemin, 0)
                                     + octaves.get(lp.chemin, 0))
            events, periode = cache[cle]
            reps = max(1, round(section_beats / periode))
            for rep in range(reps):
                depart = beat + rep * periode
                for start, dur, pitch, vel, ch in events:
                    if start >= section_beats:
                        continue
                    if depart + start >= beat + section_beats:
                        continue
                    p = pitch if ch == DRUMS_CHANNEL else min(126, max(1, pitch + decalage))
                    v = min(VEL_MAX, max(VEL_MIN, round(vel * energie)))
                    pistes.setdefault(cle, []).append((depart + start, dur, p, v, ch))
        beat += section_beats

    write_midi(path, list(pistes.values()), ppq=480, bpm=plan.bpm,
               time_sig=(4, 4), markers=markers,
               tempo_changes=tempo_changes or None)
    return int(beat / BEATS_PER_BAR)


# ──────────────────────────────────────────────
# Forge
# ──────────────────────────────────────────────

def forge_loops(source: str | Path, out_dir: str | Path, n: int = 24, seed: int = 1,
                index_path: str | Path | None = None,
                min_confidence: float = SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
                min_score: float = 0.0, keep_all: bool = False,
                axes_report: bool = False, shortlist: int = 0,
                famille: str | None = None) -> dict:
    loops, data = ensure_index(Path(source), Path(index_path) if index_path else None)
    arrangeables = familles_arrangeables(loops)
    if not arrangeables:
        raise SystemExit(f"aucune famille arrangeable dans {source} — "
                         f"il faut au moins deux pupitres tonaux dans un même "
                         f"dossier, à tonalité et tempo égaux.")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates, empties, familles_vues = [], 0, set()
    for i in range(n):
        rng = _random.Random(f"forge_loops:{seed}:{i}")
        plan = tirer_plan(loops, arrangeables, rng, famille)
        if plan is None:
            raise SystemExit(f"aucune famille ne correspond à « {famille} ».")
        path = out / f"candidate_{i:03d}.mid"
        mesures = rendre(plan, path, rng)
        familles_vues.add(plan.famille)
        cand = judge_midi(path, i, form=plan.forme,
                          mode=plan.mode or "—", meter="4/4",
                          bpm=int(round(plan.bpm)), tonic=plan.tonique, bars=mesures)
        if cand is None:
            empties += 1
            continue
        candidates.append(cand)

    return _select_and_report(
        candidates, empties, out,
        header={"n_requested": n, "seed": seed, "source": str(source),
                "constraints": None,
                "pack": data.get("racine"),
                "n_loops": len(loops),
                "n_familles_arrangeables": len(arrangeables),
                "familles_tirees": sorted(familles_vues)},
        min_confidence=min_confidence, min_score=min_score,
        keep_all=keep_all, axes_report=axes_report, shortlist=shortlist)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge_loops",
        description="Forge sur pack de loops : tire N arrangements de matière "
                    "humaine, garde celui que Libretto juge le mieux construit.")
    parser.add_argument("source", help="index JSON (loop_index.py) ou dossier du pack")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=24,
                        help="nombre de candidats (défaut 24)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--index", metavar="F",
                        help="écrire/relire l'index ici quand la source est un dossier")
    parser.add_argument("--famille", metavar="MOTIF",
                        help="restreindre aux familles dont le nom contient MOTIF")
    parser.add_argument("--min-confidence", type=float,
                        default=SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--keep-all", action="store_true",
                        help="conserver tous les candidats (défaut : garder le gagnant)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe")
    parser.add_argument("--shortlist", type=int, default=0, metavar="K",
                        help="K candidats sous contrainte de diversité")
    args = parser.parse_args(argv)

    report = forge_loops(args.source, args.out_dir, n=args.n, seed=args.seed,
                         index_path=args.index,
                         min_confidence=args.min_confidence, min_score=args.min_score,
                         keep_all=args.keep_all, axes_report=args.axes,
                         shortlist=args.shortlist, famille=args.famille)
    print(f"pack : {report['n_loops']} loops · "
          f"{report['n_familles_arrangeables']} familles arrangeables · "
          f"{len(report['familles_tirees'])} tirées")
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)
    if report["winner"]:
        w = report["winner"]
        print(f"\nmatière : {w['key']} · {w['bpm']} bpm · {w['bars']} mesures")
    # Exit 2 si aucun candidat fiable : utilisable comme gate, comme forge.
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
