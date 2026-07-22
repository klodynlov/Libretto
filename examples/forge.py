"""
Forge — sélection structurelle : générer N ébauches, garder celle que
Libretto juge la mieux construite.

L'idée
------
Un système génératif produit du matériau ; Libretto ne compose rien, il
*juge*. Forge branche le second sur le premier : le score SMS devient une
**fonction de fitness**. On tire N candidats, on les note, et on garde le
meilleur — mais « meilleur » est défini fiabilité d'abord (voir plus bas).
C'est exactement le trou que les modèles génératifs laissent ouvert — ils
sont forts sur le grain et le timbre, faibles sur la forme longue (couplets,
refrains, arc émotionnel), et c'est précisément ce que les 6 groupes d'axes
mesurent.

    ┌───────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐
    │ générateur│ → │  → MIDI  │ → │  LIBRETTO  │ → │ sélection│
    │ (ébauches)│   │  (.mid)  │   │ SMS + fiab.│   │ du meilleur
    └───────────┘   └──────────┘   └────────────┘   └──────────┘

Le générateur ici est celui de `make_corpus` (procédural, stdlib, graine).
Dans une vraie chaîne « Forge » on remplacerait cette brique par une
transcription (basic-pitch) d'un rendu audio, ou par la sortie MIDI d'un
modèle — le reste ne bouge pas, puisque Forge ne parle que MIDI.

La règle de sélection : fiabilité d'abord
-----------------------------------------
Sélectionner sur `get_score()` seul reviendrait à comparer des chiffres qui
ne veulent pas tous dire la même chose (cf. README, « Fiabilité »). Deux
garde-fous :

1. **Gate.** Un candidat dont la fiabilité est sous `--min-confidence` (0.55
   par défaut) est écarté AVANT le classement : son score ne se lit pas, le
   prendre comme gagnant serait choisir un chiffre en l'air. Même contrat que
   `analyze --min-confidence`.
2. **Tri par tranche, puis score.** Parmi les éligibles, on classe D'ABORD
   par tranche de fiabilité (« élevée » ≥ 0.75 avant « moyenne » ≥ 0.55),
   puis par score À L'INTÉRIEUR de la tranche. Un morceau très bien noté mais
   moyennement fiable ne bat donc pas un morceau à peine moins noté mais
   pleinement fiable — parce que son score, lui, est plus digne de foi. Les
   seuils de tranche ne sont pas arbitraires : ce sont ceux que le README
   calibre sur 200 fichiers (élevée/moyenne ≈ 0.94 d'accuracy). On ne trie
   PAS sur la confiance brute, qui laisserait un 0.68 à confiance 1.00
   l'emporter sur un 0.88 à confiance 0.99 — absurde.

Le piège assumé : la circularité
--------------------------------
`make_corpus` varie *délibérément au-delà* de la zone de confort des axes
(formes à travers-composé, mesures impaires, arcs plats). Sélectionner le SMS
maximal rappelle donc mécaniquement l'esthétique inscrite dans les bandes de
tolérance — pop, en arche, carrée. Forge le montre au lieu de le cacher : le
rapport compare la distribution des formes du peloton de tête à celle de tous
les candidats. Si le top-K est plus pauvre, ce n'est pas un bug de Forge,
c'est ce que « optimiser un score » fait à la diversité. Et `--shortlist K`
est la réponse : une sélection round-robin par forme (voir
`diverse_shortlist`) qui garantit min(K, formes distinctes) formes dans le
peloton livré, avec le coût en score affiché sans fard.

Commander, pas seulement tirer
------------------------------
Sans contrainte, chaque ébauche tire tonalité, mode, tempo, métrique et
longueur au sort : la graine est la seule poignée, et elle ne dit rien. Les
options `--tonic/--mode/--bpm/--meter/--bars` IMPOSENT ces champs — on
demande « 16 mesures en fa mineur » au lieu d'espérer que ça sorte. Ce qui
reste tiré (motifs, progressions, arc, effectif, forme quand `--bars` ne la
dicte pas) reste l'espace de recherche : sans lui, N candidats seraient N
clones et la sélection n'aurait plus d'objet.

Le gate ne bouge pas pour autant : une commande contraignante peut ne
produire aucun candidat fiable. C'est un résultat — la structure demandée
n'est pas notable — pas une panne à contourner en baissant le seuil.

Usage
-----
    python3 examples/forge.py sortie/ [n=24] [seed=1]
        [--min-confidence 0.55] [--min-score 0.0]
        [--keep-all] [--axes] [--shortlist K] [--reaper]
        [--from-dir candidats/]   # noter des MIDI venus d'ailleurs
                                  # (n, seed et contraintes sont alors ignorés)
        [--tonic F] [--mode min] [--bpm 72] [--meter 4/4] [--bars 16]
        [--swing|--no-swing] [--syncopation 0.45] [--drums|--no-drums]

Sortie : `sortie/forge_winner.mid` (le gagnant), `sortie/forge_report.json`
(le classement complet), et un tableau lisible sur stdout. Avec `--axes`,
le rapport explique POURQUOI ce gagnant, axe par axe : pour chacun des 29
axes, son score contre la moyenne du peloton éligible, et le levier
(écart × poids) — dont la somme vaut exactement l'avance SMS du gagnant.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from libretto.axes import AXES_META, SenseOfMusicalStructure  # noqa: E402
from libretto.builder import build_score  # noqa: E402
from libretto.midi import parse_midi, write_midi  # noqa: E402

# make_corpus n'est pas un package : on l'importe comme module voisin, le
# `sys.path` ci-dessus rend les deux imports (racine + examples/) possibles.
import random as _random  # noqa: E402
from make_corpus import (  # noqa: E402
    FORMS,
    METERS,
    SCALES,
    Style,
    random_style,
    render,
    total_bars,
)

# Ordre des tranches de fiabilité (haut = plus fiable). Sert de clé de tri
# primaire : on préfère toujours une tranche plus sûre, et le score ne
# départage qu'à l'intérieur d'une même tranche. Les libellés viennent de
# SenseOfMusicalStructure.confidence_level().
_TIER_RANK = {"élevée": 3, "moyenne": 2, "faible": 1, "insuffisante": 0}


# --------------------------------------------------------------------------- #
# Contraintes — commander le générateur au lieu de le laisser tirer au sort   #
# --------------------------------------------------------------------------- #

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Anglais ET français : la demande vient d'un humain ou d'un LLM (« Fa
# mineur »), pas d'un fichier MIDI. Les altérations se lisent en suffixe.
_LETTERS = {
    "c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11,
    "do": 0, "re": 2, "ré": 2, "mi": 4, "fa": 5, "sol": 7, "la": 9, "si": 11,
}
_MODE_ALIASES = {
    "maj": "maj", "major": "maj", "majeur": "maj", "ionien": "maj", "ionian": "maj",
    "min": "min", "minor": "min", "mineur": "min", "aeolien": "min", "aeolian": "min",
    "dorien": "dorien", "dorian": "dorien",
    "mixolydien": "mixolydien", "mixolydian": "mixolydien",
}

# Borne du balayage `forms_matching_bars` : au-delà, une « section » de 32
# mesures n'est plus une section.
_MAX_BARS_PER_SECTION = 32


def parse_tonic(value: str | int) -> int:
    """Classe de hauteur 0-11 depuis « F », « Fa# », « Sib », « ré », ou 0-11."""
    if isinstance(value, int) or str(value).strip().isdigit():
        n = int(value)
        if not 0 <= n <= 11:
            raise ValueError(f"tonique hors bornes : {value} (attendu 0-11)")
        return n
    raw = str(value).strip().lower().replace("♯", "#").replace("♭", "b")
    shift = 0
    # « b » seul = si bécarre ; « bb » = si bémol. On ne retire une altération
    # que si le reste est encore un nom de note — sinon on mange la note même.
    while raw and raw not in _LETTERS and raw[-1] in "#b":
        shift += 1 if raw[-1] == "#" else -1
        raw = raw[:-1]
    if raw not in _LETTERS:
        raise ValueError(
            f"tonique inconnue : {value!r} (attendu C..B, Do..Si, ou 0-11)")
    return (_LETTERS[raw] + shift) % 12


def parse_mode(value: str) -> str:
    """Mode canonique de `SCALES` depuis « minor », « mineur », « min »…"""
    key = str(value).strip().lower()
    mode = _MODE_ALIASES.get(key, key)
    if mode not in SCALES:
        raise ValueError(
            f"mode inconnu : {value!r} (attendu {', '.join(sorted(SCALES))})")
    return mode


def parse_meter(value: str) -> tuple[int, int, float, float]:
    """Métrique de `METERS` depuis « 4/4 ». Refuse ce que le générateur ne
    sait pas rendre plutôt que d'inventer une pulsation."""
    try:
        num, den = (int(p) for p in str(value).strip().split("/", 1))
    except ValueError as exc:
        raise ValueError(f"métrique illisible : {value!r} (attendu « 4/4 »)") from exc
    for m in METERS:
        if (m[0], m[1]) == (num, den):
            return m
    known = sorted({f"{m[0]}/{m[1]}" for m in METERS})
    raise ValueError(f"métrique non gérée : {value!r} (connues : {', '.join(known)})")


def forms_matching_bars(bars: int) -> list[tuple[str, int]]:
    """Couples (forme, mesures par section) qui font EXACTEMENT `bars` mesures.

    Passe par `total_bars`, donc l'écourtement des intros et outros est
    compté : `bars_per_section` est une consigne, pas un décompte."""
    return [(form, per)
            for form in FORMS
            for per in range(2, _MAX_BARS_PER_SECTION + 1)
            if total_bars(form, per) == bars]


def _achievable_bars(around: int, span: int = 8) -> list[int]:
    """Longueurs atteignables au voisinage — pour que l'erreur soit utile."""
    lo, hi = max(1, around - span), around + span
    return sorted(b for b in range(lo, hi + 1) if forms_matching_bars(b))


@dataclass(frozen=True)
class Constraints:
    """Ce qu'on IMPOSE au générateur ; `None` = laissé au tirage.

    Contraindre RESTREINT l'espace de recherche sans l'annuler : motifs,
    progressions, arc d'énergie, effectif, modulation et — tant que `bars` ne
    la dicte pas — la forme restent tirés. N candidats diffèrent donc encore,
    et la sélection garde un sens. Tout contraindre produirait N clones et
    Forge n'aurait plus rien à choisir.
    """
    tonic: int | None = None
    mode: str | None = None
    bpm: float | None = None
    meter: tuple[int, int, float, float] | None = None
    bars: int | None = None
    swing: bool | None = None
    syncopation: float | None = None
    drums: bool | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))

    def as_dict(self) -> dict:
        """Ce qui a été imposé, lisible — le rapport doit permettre de vérifier
        la commande, pas seulement de constater le résultat."""
        out: dict[str, Any] = {}
        if self.tonic is not None:
            out["tonic"] = self.tonic
            out["tonic_name"] = PITCH_NAMES[self.tonic]
        for name in ("mode", "bpm", "bars", "swing", "syncopation", "drums"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.meter is not None:
            out["meter"] = f"{self.meter[0]}/{self.meter[1]}"
        return out


def apply_constraints(style: Style, cons: Constraints,
                      rng: _random.Random) -> Style:
    """Écrase les champs imposés du style tiré. Lève ValueError si `bars` est
    inatteignable — mieux vaut refuser que rendre 15 mesures pour 16."""
    changes: dict[str, Any] = {}
    for field, attr in (("tonic", "tonic"), ("mode", "mode"), ("meter", "meter"),
                        ("swing", "swing"), ("syncopation", "syncopation"),
                        ("drums", "with_drums")):
        value = getattr(cons, field)
        if value is not None:
            changes[attr] = value
    if cons.bpm is not None:
        # Un tempo imposé interdit la dérive : sinon la contrainte ne tient
        # qu'au premier temps et le pont part ailleurs.
        changes["bpm"] = float(cons.bpm)
        changes["tempo_drift"] = False
    if cons.bars is not None:
        options = forms_matching_bars(cons.bars)
        if not options:
            near = _achievable_bars(cons.bars)
            raise ValueError(
                f"aucune forme ne fait exactement {cons.bars} mesures "
                f"(intros et outros comptées de moitié). Atteignables au "
                f"voisinage : {near}")
        # Tiré parmi les couples valides : la contrainte fixe la LONGUEUR,
        # pas la forme — le peloton garde de la variété structurelle.
        form, per = options[rng.randrange(len(options))]
        changes["form"] = form
        changes["bars_per_section"] = per
    return replace(style, **changes)


@dataclass
class Candidate:
    """Un candidat noté : le fichier, son style, et le verdict de Libretto."""
    index: int
    path: Path
    form: str
    # None pour un MIDI venu d'ailleurs : sa tonique et sa longueur voulue
    # ne sont connues de personne (cf. `judge_midi`).
    tonic: int | None
    mode: str
    meter: str
    bpm: int
    bars: int | None
    score: float
    confidence: float
    level: str
    interpretable: bool
    groups: dict[str, float]
    # Les 29 axes bruts (id, nom, groupe, poids, score, confiance) : la
    # matière du mode --axes. Gardés en mémoire pour tous les candidats —
    # c'est ce qui permet de comparer le gagnant au peloton — mais absents
    # de as_dict() pour ne pas gonfler le leaderboard du rapport JSON.
    axes: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "file": self.path.name,
            "form": self.form,
            # La tonalité et la longueur SORTENT du rapport : sans elles, un
            # appelant ne peut ni vérifier une contrainte ni filtrer après
            # coup — il ne lui reste que la graine, qui ne dit rien.
            "tonic": self.tonic,
            "key": (f"{PITCH_NAMES[self.tonic]} {self.mode}"
                    if self.tonic is not None else "—"),
            "mode": self.mode,
            "meter": self.meter,
            "bpm": self.bpm,
            "bars": self.bars,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "level": self.level,
            "interpretable": self.interpretable,
            "groups": {g: round(v, 3) for g, v in self.groups.items()},
        }


def _label_signature(sections) -> str:
    """Forme d'un MIDI externe, dérivée de ce que Libretto détecte : la suite
    des initiales de sections (intro-verse-chorus-verse → « IVCV »). C'est la
    forme JUGÉE, pas la forme voulue — pour un candidat venu d'un modèle
    génératif, personne ne connaît la seconde. Sert de clé de diversité à
    `diverse_shortlist`, exactement comme `style.form` côté make_corpus."""
    return "".join((s.label[:1] or "?").upper() for s in sections)


def judge_midi(path: Path, index: int, *, form: str | None = None,
               mode: str | None = None, meter: str | None = None,
               bpm: int | None = None, tonic: int | None = None,
               bars: int | None = None) -> Candidate | None:
    """Fait noter un fichier MIDI par Libretto et le constitue en Candidate.

    Les métadonnées (forme, mode, métrique, bpm) sont fournies quand la
    source les connaît (make_corpus a la vérité terrain) ; sinon elles sont
    dérivées de l'analyse elle-même — c'est ce qui permet de brancher
    N'IMPORTE QUELLE source de MIDI sur Forge. Renvoie None si le fichier
    n'a aucune section analysable.

    `tonic` et `bars` restent à None pour un MIDI venu d'ailleurs : personne
    n'en connaît la tonique, et l'inventer serait pire que l'avouer (même
    convention que `mode="—"`)."""
    score_obj = build_score(parse_midi(path))
    if not score_obj.sections:
        return None

    sms = SenseOfMusicalStructure(score_obj)
    sms.calculate()
    return Candidate(
        index=index,
        path=path,
        form=form if form is not None else _label_signature(score_obj.sections),
        tonic=tonic,
        bars=bars,
        mode=mode if mode is not None else "—",
        meter=meter if meter is not None else
              f"{score_obj.time_signature_num}/{score_obj.time_signature_den}",
        bpm=bpm if bpm is not None else int(score_obj.sections[0].tempo),
        score=sms.get_score(),
        confidence=sms.confidence(),
        level=sms.confidence_level(),
        interpretable=sms.is_interpretable(),
        groups=sms.group_scores(),
        axes=[{"id": a.id, "name": a.name,
               "group": AXES_META[int(a.id[:2])][3],
               "weight": a.weight,
               "score": a.score,
               "confidence": a.confidence}
              for a in sms.axes],
    )


def generate_and_score(index: int, seed: int, out_dir: Path,
                       constraints: Constraints | None = None) -> Candidate | None:
    """Tire un candidat déterministe, l'écrit en MIDI, le fait noter par
    Libretto. Renvoie None si le candidat n'a produit aucune note exploitable
    (le générateur peut, sur certaines graines, sortir une pièce vide de
    sections analysables — on l'écarte plutôt que de le classer)."""
    rng = _random.Random(f"forge:{seed}:{index}")
    style: Style = random_style(rng)
    if constraints is not None and not constraints.is_empty():
        style = apply_constraints(style, constraints, rng)
    tracks, markers, tempo_changes, bars, _truth, _roles = render(style, rng)
    num, den, _bar_beats, _pulse = style.meter

    path = out_dir / f"candidate_{index:03d}.mid"
    write_midi(path, tracks, ppq=480, bpm=style.bpm,
               time_sig=(num, den), markers=markers or None,
               tempo_changes=tempo_changes or None)

    # `make_corpus` connaît la vérité terrain : on la transmet plutôt que de
    # la laisser deviner par l'analyse (tonique et longueur comprises).
    cand = judge_midi(path, index, form=style.form, mode=style.mode,
                      meter=f"{num}/{den}", bpm=int(style.bpm),
                      tonic=style.tonic, bars=bars)
    if cand is None:
        path.unlink(missing_ok=True)
    return cand


def forge(out_dir: str | Path, n: int = 24, seed: int = 1,
          min_confidence: float = SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
          min_score: float = 0.0, keep_all: bool = False,
          axes_report: bool = False, shortlist: int = 0,
          constraints: Constraints | None = None) -> dict:
    """Génère n candidats, les note, sélectionne le meilleur — fiabilité
    d'abord (tranche puis score), après le gate `min_confidence`.

    `constraints` impose tonalité, mode, tempo, métrique ou longueur : la
    sélection porte alors sur ce qui reste libre (matériau, forme, arc). Le
    gate ne bouge pas — une commande contraignante peut très bien ne produire
    aucun candidat fiable, et c'est un résultat, pas une panne.

    Renvoie un rapport sérialisable. Le gagnant est copié en
    `forge_winner.mid`. Sans `keep_all`, les candidats non retenus sont
    effacés (un pipeline garde le gagnant, pas les 23 brouillons).
    Avec `axes_report`, le rapport détaille le gagnant axe par axe contre
    le peloton des autres éligibles (voir `_axes_report`).
    Avec `shortlist=k`, sélectionne aussi k candidats SOUS CONTRAINTE DE
    DIVERSITÉ (round-robin par forme, voir `diverse_shortlist`), copiés en
    `forge_short_XX.mid` — la réponse à la collapse de diversité que le
    rapport documente."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates: list[Candidate] = []
    empties = 0
    for i in range(n):
        cand = generate_and_score(i, seed, out, constraints)
        if cand is None:
            empties += 1
            continue
        candidates.append(cand)

    return _select_and_report(
        candidates, empties, out,
        header={"n_requested": n, "seed": seed, "source": "make_corpus",
                "constraints": (constraints.as_dict()
                                if constraints and not constraints.is_empty()
                                else None)},
        min_confidence=min_confidence, min_score=min_score,
        keep_all=keep_all, axes_report=axes_report, shortlist=shortlist)


def forge_from_dir(candidates_dir: str | Path, out_dir: str | Path,
                   min_confidence: float = SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
                   min_score: float = 0.0, axes_report: bool = False,
                   shortlist: int = 0) -> dict:
    """Le même Forge, sur des candidats venus d'AILLEURS : un dossier de
    fichiers MIDI — la sortie d'un modèle génératif, des transcriptions
    (basic-pitch), n'importe quoi. C'est le point de branchement universel :
    Forge ne parle que MIDI, la brique génératrice est libre.

    Différences avec `forge()` : l'ordre des candidats est l'ordre trié des
    noms de fichiers (déterministe), la « forme » est la signature de
    sections jugée par Libretto (voir `_label_signature`), et les fichiers
    source ne sont JAMAIS effacés — ils ne nous appartiennent pas. Le
    gagnant et la shortlist restent copiés dans `out_dir`.

    ⚠ Si les MIDI viennent d'une TRANSCRIPTION audio (basic-pitch…) : la
    fiabilité ne détecte pas les artefacts de transcription — elle peut
    même monter, car le transcripteur produit beaucoup de notes et la
    confiance mesure la quantité de matière, pas sa provenance. Le gate
    `min_confidence` ne protège donc pas d'une mauvaise transcription, et
    le classement fin n'est que partiellement préservé (mesuré : Spearman
    +0.1 à +0.6, score −0.18 — voir `transcription_roundtrip.py`). Sur du
    transcrit, utilisez le résultat en triage grossier + `shortlist`, pas
    en verdict."""
    src = Path(candidates_dir)
    paths = sorted(p for p in src.iterdir()
                   if p.suffix.lower() in (".mid", ".midi"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates: list[Candidate] = []
    empties = 0
    for i, path in enumerate(paths):
        cand = judge_midi(path, i)
        if cand is None:
            empties += 1
            continue
        candidates.append(cand)

    return _select_and_report(
        candidates, empties, out,
        # Pas de contraintes ici : on NOTE des MIDI existants, on ne les
        # génère pas — rien à imposer à un fichier déjà écrit.
        header={"n_requested": len(paths), "seed": None, "source": str(src),
                "constraints": None},
        min_confidence=min_confidence, min_score=min_score,
        keep_all=True, axes_report=axes_report, shortlist=shortlist)


def _select_and_report(candidates: list[Candidate], empties: int, out: Path, *,
                       header: dict, min_confidence: float, min_score: float,
                       keep_all: bool, axes_report: bool, shortlist: int) -> dict:
    """Le cœur de Forge, indépendant de la provenance des candidats : gate,
    classement fiabilité-d'abord, copie du gagnant et de la shortlist,
    rapport sérialisé."""
    # Gate fiabilité AVANT le classement : un score non interprétable ne
    # concourt pas, quelle que soit sa valeur.
    eligible = [c for c in candidates
                if c.confidence >= min_confidence and c.score >= min_score]
    rejected_conf = [c for c in candidates if c.confidence < min_confidence]
    rejected_score = [c for c in candidates
                      if c.confidence >= min_confidence and c.score < min_score]

    # Fiabilité prioritaire : tranche d'abord (« élevée » avant « moyenne »),
    # score seulement à égalité de tranche. Un score plus haut mais moins
    # fiable ne l'emporte pas — son chiffre est moins digne de foi.
    ranked = sorted(eligible,
                    key=lambda c: (_TIER_RANK.get(c.level, 0), c.score),
                    reverse=True)
    winner = ranked[0] if ranked else None

    winner_path = None
    if winner is not None:
        winner_path = out / "forge_winner.mid"
        winner_path.write_bytes(winner.path.read_bytes())

    # Shortlist diverse : copiée AVANT l'effacement des brouillons — ces
    # fichiers sont un livrable au même titre que le gagnant.
    short: list[Candidate] = []
    if shortlist > 0 and ranked:
        short = diverse_shortlist(ranked, shortlist)
        for pos, c in enumerate(short, 1):
            (out / f"forge_short_{pos:02d}.mid").write_bytes(c.path.read_bytes())

    # Le gagnant est copié sous `forge_winner.mid` ; sans --keep-all on efface
    # tous les brouillons, l'original compris (un pipeline garde le gagnant,
    # pas les N ébauches).
    if not keep_all:
        for c in candidates:
            c.path.unlink(missing_ok=True)

    report = {
        **header,
        "n_generated": len(candidates),
        "n_empty_skipped": empties,
        "gates": {"min_confidence": min_confidence, "min_score": min_score},
        "n_eligible": len(eligible),
        "n_rejected_confidence": len(rejected_conf),
        "n_rejected_score": len(rejected_score),
        "winner": winner.as_dict() if winner else None,
        "winner_file": winner_path.name if winner_path else None,
        "leaderboard": [c.as_dict() for c in ranked],
        "rejected_low_confidence": [c.as_dict() for c in
                                    sorted(rejected_conf, key=lambda c: c.confidence)],
        "diversity": _diversity_report(candidates, ranked),
    }
    if axes_report:
        report["axes_report"] = _axes_report(ranked)
    if shortlist > 0:
        report["shortlist"] = _shortlist_report(ranked, short, out) if short else None
    (out / "forge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def diverse_shortlist(ranked: list, k: int, form_of=None) -> list:
    """Sélection sous contrainte de diversité : round-robin par forme.

    On parcourt le classement (fiabilité d'abord) en n'acceptant un candidat
    que si sa forme n'a pas atteint le cap courant ; quand une passe complète
    n'ajoute plus personne, on relève le cap. Cap 1 : le meilleur de chaque
    forme, dans l'ordre du mérite. Cap 2 : les seconds. Etc.

    Propriétés (verrouillées par les tests) :
    - tant qu'une forme n'est pas représentée, la place suivante lui revient :
      la shortlist compte min(k, formes distinctes) formes — jamais moins ;
    - à contrainte égale, c'est toujours le mieux classé qui passe — la
      diversité choisit QUI concourt, le mérite garde l'ordre ; le premier
      élu est donc toujours le gagnant lui-même ;
    - déterministe, et générique : `form_of` permet de l'appliquer aussi bien
      aux `Candidate` qu'aux dicts du leaderboard (forge_sweep s'en sert).
    """
    if form_of is None:
        form_of = lambda c: c.form  # noqa: E731
    picked_idx: list[int] = []
    taken: set[int] = set()
    counts: dict[str, int] = {}
    cap = 1
    while len(picked_idx) < k:
        progressed = False
        for i, c in enumerate(ranked):
            if len(picked_idx) >= k:
                break
            if i in taken:
                continue
            form = form_of(c)
            if counts.get(form, 0) < cap:
                picked_idx.append(i)
                taken.add(i)
                counts[form] = counts.get(form, 0) + 1
                progressed = True
        if not progressed:
            break  # moins de k candidats en tout : on rend ce qu'on a
        cap += 1
    return [ranked[i] for i in picked_idx]


def _shortlist_report(ranked: list[Candidate], short: list[Candidate],
                      out: Path) -> dict:
    """La shortlist diverse, et ce qu'elle coûte — contre le vrai contrefactuel :
    le top-k SANS contrainte (les k premiers du classement fiabilité d'abord),
    c'est-à-dire ce que Forge livrerait si on ne lui demandait pas de diversité.
    Le coût peut être négatif : la contrainte peut repêcher un score élevé
    d'une tranche plus basse. On rapporte le chiffre tel quel."""
    k = len(short)
    unconstrained = ranked[:k]
    mean = lambda cs: sum(c.score for c in cs) / len(cs)  # noqa: E731

    picks = []
    for pos, c in enumerate(short, 1):
        entry = c.as_dict()
        entry["rank"] = ranked.index(c) + 1  # rang dans le classement libre
        entry["file_out"] = f"forge_short_{pos:02d}.mid"
        picks.append(entry)

    return {
        "k": k,
        "picks": picks,
        "distinct_forms": len({c.form for c in short}),
        "unconstrained_distinct_forms": len({c.form for c in unconstrained}),
        "mean_score": round(mean(short), 4),
        "unconstrained_mean_score": round(mean(unconstrained), 4),
        "score_cost_mean": round(mean(unconstrained) - mean(short), 4),
    }


def _axes_report(ranked: list[Candidate]) -> dict | None:
    """Pourquoi CE gagnant — axe par axe.

    Pour chacun des 29 axes : le score du gagnant, la moyenne du peloton
    (les autres éligibles), l'écart, et le **levier** (écart × poids) — la
    part de l'avance globale que l'axe explique. Comme les poids somment à
    1.0, la somme des leviers vaut exactement l'écart entre le SMS du
    gagnant et le SMS moyen du peloton : la décomposition est complète,
    rien ne se cache dans un résidu.

    Les lignes restent dans l'ordre canonique des axes (stable pour un
    consommateur JSON) ; c'est l'affichage qui trie par levier."""
    if not ranked:
        return None
    winner, field_ = ranked[0], ranked[1:]
    # Alignement par id, pas par index : ne dépend pas de l'ordre interne
    # de calculate().
    field_by_id = [{a["id"]: a["score"] for a in c.axes} for c in field_]

    rows = []
    for ax in winner.axes:
        others = [d[ax["id"]] for d in field_by_id if ax["id"] in d]
        mean = sum(others) / len(others) if others else None
        delta = (ax["score"] - mean) if mean is not None else None
        rows.append({
            "id": ax["id"],
            "name": ax["name"],
            "group": ax["group"],
            "weight": ax["weight"],
            "winner_score": round(ax["score"], 4),
            "winner_confidence": round(ax["confidence"], 4),
            "field_mean": round(mean, 4) if mean is not None else None,
            "delta": round(delta, 4) if delta is not None else None,
            "leverage": round(delta * ax["weight"], 6) if delta is not None else None,
        })
    return {
        "winner_file": winner.path.name,
        "field_size": len(field_),
        "axes": rows,
    }


def _diversity_report(all_c: list[Candidate], ranked: list[Candidate],
                      top: int = 5) -> dict:
    """Compare la variété des formes du peloton de tête à celle de l'ensemble.
    Rend visible la collapse de diversité qu'induit toute sélection sur un
    score unique — le point d'honnêteté du README sur la circularité."""
    def dist(cs: list[Candidate]) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in cs:
            out[c.form] = out.get(c.form, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    head = ranked[:top]
    return {
        "top_n": len(head),
        "forms_overall": dist(all_c),
        "forms_in_top": dist(head),
        "distinct_forms_overall": len({c.form for c in all_c}),
        "distinct_forms_in_top": len({c.form for c in head}),
    }


def _print_report(report: dict) -> None:
    origin = (f"graine {report['seed']}" if report.get("seed") is not None
              else f"source {report.get('source', '?')}")
    print(f"Forge — {report['n_generated']} candidats notés "
          f"({origin}, {report['n_empty_skipped']} vides écartés)")
    cons = report.get("constraints")
    if cons:
        shown = ", ".join(f"{k}={v}" for k, v in cons.items() if k != "tonic")
        print(f"contraintes imposées : {shown}")
    g = report["gates"]
    print(f"gates : fiabilité ≥ {g['min_confidence']:g}, score ≥ {g['min_score']:g}  "
          f"→ {report['n_eligible']} éligibles, "
          f"{report['n_rejected_confidence']} recalés (fiabilité), "
          f"{report['n_rejected_score']} recalés (score)")

    win = report["winner"]
    if not win:
        print("\n⚠ Aucun candidat fiable : rien à sélectionner. "
              "Augmentez n, ou baissez --min-confidence en sachant ce que ça "
              "coûte (README, « Fiabilité »).")
        return

    print(f"\n🏆 GAGNANT → {report['winner_file']}")
    # `bars` est None pour un MIDI venu d'ailleurs : on n'affiche pas
    # « None mes. », on se tait sur ce qu'on ne sait pas.
    longueur = f" · {win['bars']} mes." if win.get("bars") else ""
    print(f"   {win['form']} · {win['key']} · {win['meter']} · "
          f"{win['bpm']} bpm{longueur}")
    print(f"   score {win['score']:.3f}  ·  fiabilité {win['confidence']:.3f} "
          f"({win['level']})")

    print("\nClassement (éligibles — tranche de fiabilité d'abord, puis score) :")
    print(f"  {'#':>2}  {'tranche':<11} {'score':>6}  {'fiab.':>6}  "
          f"{'forme':<18} {'tonalité':<15} {'métr.':>5} {'mes.':>5}")
    for rank, c in enumerate(report["leaderboard"][:10], 1):
        flag = "  ←" if c["file"] == report["winner_file"] else ""
        print(f"  {rank:>2}  {c['level']:<11} {c['score']:>6.3f}  {c['confidence']:>6.3f}  "
              f"{c['form']:<18} {c['key']:<15} {c['meter']:>5} "
              f"{(c['bars'] if c['bars'] else '—'):>5}{flag}")

    d = report["diversity"]
    print(f"\nDiversité des formes : {d['distinct_forms_overall']} sur l'ensemble, "
          f"{d['distinct_forms_in_top']} dans le top {d['top_n']}.")
    if d["distinct_forms_in_top"] < d["distinct_forms_overall"]:
        print("  → optimiser le SMS resserre le peloton de tête sur les formes que "
              "les bandes de tolérance récompensent. Attendu, pas accidentel "
              "(README, circularité) : --shortlist K sélectionne sous "
              "contrainte de diversité, pas sur le score seul.")


def _print_shortlist(report: dict) -> None:
    """La shortlist diverse, et son prix affiché sans fard : formes gagnées
    contre le top-k libre, score moyen cédé (ou gagné — ça arrive : la
    contrainte peut repêcher un score élevé d'une tranche plus basse)."""
    if "shortlist" not in report:
        return
    sl = report["shortlist"]
    if not sl:
        print("\nShortlist diverse : aucun candidat éligible, rien à sélectionner.")
        return

    print(f"\nShortlist sous contrainte de diversité (k={sl['k']}, "
          f"round-robin par forme — le mérite garde l'ordre) :")
    print(f"  {'fichier':<19} {'rang':>4}  {'tranche':<11} {'score':>6}  "
          f"{'fiab.':>6}  forme")
    for p in sl["picks"]:
        print(f"  {p['file_out']:<19} {p['rank']:>4}  {p['level']:<11} "
              f"{p['score']:>6.3f}  {p['confidence']:>6.3f}  {p['form']}")

    cost = sl["score_cost_mean"]
    print(f"  → {sl['distinct_forms']} formes distinctes contre "
          f"{sl['unconstrained_distinct_forms']} dans le top-{sl['k']} libre ; "
          f"score moyen {sl['mean_score']:.3f} contre "
          f"{sl['unconstrained_mean_score']:.3f} "
          f"({'coût' if cost >= 0 else 'gain'} {abs(cost):.3f}).")


def _print_axes_report(report: dict) -> None:
    """Tableau lisible du rapport axe par axe : trié par levier décroissant,
    pour lire de haut en bas où l'avance se construit puis où elle s'érode.
    Le JSON, lui, garde l'ordre canonique des axes."""
    ar = report.get("axes_report")
    if not ar:
        return

    rows = ar["axes"]
    if ar["field_size"] == 0:
        # Un seul éligible : pas de peloton, on montre le profil brut.
        print("\nRapport axe par axe — seul éligible, profil brut :")
        for r in rows:
            flag = "" if r["winner_confidence"] >= 0.5 else \
                f"  ⚠ fiabilité {r['winner_confidence']:.2f}"
            print(f"  [{r['id']}] {r['name']}: {r['winner_score']:.3f}{flag}")
        return

    print(f"\nRapport axe par axe — le gagnant contre le peloton "
          f"({ar['field_size']} autres éligibles), trié par levier "
          f"(écart × poids) :")
    print(f"  {'axe':<33} {'gagnant':>7} {'peloton':>7} {'Δ':>7} {'levier':>8}")
    for r in sorted(rows, key=lambda r: r["leverage"], reverse=True):
        flag = "" if r["winner_confidence"] >= 0.5 else \
            f"  ⚠ fiabilité {r['winner_confidence']:.2f}"
        print(f"  {r['id']:<33} {r['winner_score']:>7.3f} {r['field_mean']:>7.3f} "
              f"{r['delta']:>+7.3f} {r['leverage']:>+8.4f}{flag}")

    total = sum(r["leverage"] for r in rows)
    gains = sorted((r for r in rows if r["leverage"] > 0),
                   key=lambda r: -r["leverage"])[:3]
    losses = sorted((r for r in rows if r["leverage"] < 0),
                    key=lambda r: r["leverage"])[:3]
    print(f"\n  Avance SMS sur le peloton : {total:+.4f} "
          f"(la somme des leviers — décomposition exacte).")
    if gains:
        print("  Elle se construit surtout sur : "
              + ", ".join(f"{r['name']} ({r['leverage']:+.4f})" for r in gains))
    if losses:
        print("  Elle s'érode sur : "
              + ", ".join(f"{r['name']} ({r['leverage']:+.4f})" for r in losses))


def _maybe_push_reaper(winner_file: Path) -> None:
    from libretto.reaper import BridgeError, push_mididata
    try:
        result = push_mididata(parse_midi(winner_file))
    except (ValueError, BridgeError) as exc:
        print(f"forge: REAPER indisponible : {exc}", file=sys.stderr)
        return
    print(f"→ poussé dans REAPER {result['reaper']} : {result['total_notes']} notes, "
          f"lecture lancée")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — sélection structurelle : génère N ébauches, "
                    "garde celle que Libretto juge la mieux construite.")
    parser.add_argument("out_dir", help="dossier de sortie")
    parser.add_argument("n", nargs="?", type=int, default=24,
                        help="nombre de candidats (défaut 24)")
    parser.add_argument("seed", nargs="?", type=int, default=1,
                        help="graine déterministe (défaut 1)")
    parser.add_argument("--min-confidence", type=float,
                        default=SenseOfMusicalStructure.INTERPRETABLE_CONFIDENCE,
                        help="fiabilité minimale pour concourir (défaut 0.55)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="score SMS minimal pour concourir (défaut 0.0)")
    parser.add_argument("--keep-all", action="store_true",
                        help="conserver tous les candidats (défaut : garder le gagnant)")
    parser.add_argument("--axes", action="store_true",
                        help="détailler le gagnant axe par axe : son score, la "
                             "moyenne du peloton éligible, l'écart et le levier "
                             "(écart × poids)")
    parser.add_argument("--shortlist", type=int, default=0, metavar="K",
                        help="sélectionner aussi K candidats sous contrainte de "
                             "diversité (round-robin par forme), copiés en "
                             "forge_short_XX.mid — coût en score affiché")
    parser.add_argument("--from-dir", metavar="DIR",
                        help="noter les MIDI de ce dossier au lieu de générer "
                             "(sortie d'un modèle, transcriptions… — n et seed "
                             "sont ignorés, les fichiers source jamais effacés)")
    parser.add_argument("--reaper", action="store_true",
                        help="pousser le gagnant dans REAPER via le pont Klody")

    grp = parser.add_argument_group(
        "contraintes",
        "imposer au générateur au lieu de tirer au sort ; non fourni = tiré")
    grp.add_argument("--tonic", help="tonique : « F », « Fa# », « Sib », ou 0-11")
    grp.add_argument("--mode", help=f"mode : {', '.join(sorted(SCALES))} "
                                    "(alias « minor »/« mineur » acceptés)")
    grp.add_argument("--bpm", type=float, help="tempo imposé (désactive la dérive)")
    grp.add_argument("--meter", help="métrique, ex. « 4/4 »")
    grp.add_argument("--bars", type=int,
                     help="longueur totale EXACTE en mesures (forme choisie parmi "
                          "celles qui tombent juste)")
    grp.add_argument("--swing", dest="swing", action="store_true", default=None,
                     help="attaques décalées sur les temps faibles")
    grp.add_argument("--no-swing", dest="swing", action="store_false",
                     help="interdire le swing")
    grp.add_argument("--syncopation", type=float,
                     help="proportion de temps forts omis (0.0 à 1.0)")
    grp.add_argument("--drums", dest="drums", action="store_true", default=None,
                     help="forcer une piste de batterie")
    grp.add_argument("--no-drums", dest="drums", action="store_false",
                     help="interdire la batterie")
    args = parser.parse_args(argv)

    # Les contraintes sont validées AVANT de générer quoi que ce soit : une
    # commande impossible doit coûter un message, pas N rendus MIDI.
    try:
        constraints = Constraints(
            tonic=parse_tonic(args.tonic) if args.tonic is not None else None,
            mode=parse_mode(args.mode) if args.mode is not None else None,
            bpm=args.bpm,
            meter=parse_meter(args.meter) if args.meter is not None else None,
            bars=args.bars,
            swing=args.swing,
            syncopation=args.syncopation,
            drums=args.drums,
        )
        if constraints.bars is not None and not forms_matching_bars(constraints.bars):
            raise ValueError(
                f"aucune forme ne fait exactement {constraints.bars} mesures "
                f"(intros et outros comptées de moitié). Atteignables au "
                f"voisinage : {_achievable_bars(constraints.bars)}")
    except ValueError as exc:
        parser.error(str(exc))

    if args.from_dir:
        # `--from-dir` NOTE des MIDI déjà écrits : contraindre un générateur
        # qui ne tourne pas n'aurait aucun sens. On refuse au lieu d'ignorer
        # en silence — une contrainte muette est pire qu'une contrainte
        # impossible.
        if not constraints.is_empty():
            parser.error("--from-dir note des MIDI existants : les contraintes "
                         "(--tonic/--mode/--bpm/--meter/--bars/…) ne s'appliquent "
                         "qu'à la génération.")
        report = forge_from_dir(args.from_dir, args.out_dir,
                                min_confidence=args.min_confidence,
                                min_score=args.min_score,
                                axes_report=args.axes, shortlist=args.shortlist)
    else:
        report = forge(args.out_dir, n=args.n, seed=args.seed,
                       min_confidence=args.min_confidence, min_score=args.min_score,
                       keep_all=args.keep_all, axes_report=args.axes,
                       shortlist=args.shortlist, constraints=constraints)
    _print_report(report)
    _print_shortlist(report)
    _print_axes_report(report)

    if args.reaper and report["winner_file"]:
        _maybe_push_reaper(Path(args.out_dir) / report["winner_file"])

    # Exit 2 si aucun candidat fiable : Forge est utilisable comme gate.
    return 0 if report["winner"] else 2


if __name__ == "__main__":
    sys.exit(main())
