"""
Libretto — bibliothèque de séquences MIDI, cherchable par intention.

Le trou que ce module comble
----------------------------
Forge génère et trie ; `loop_index` range un pack par tonalité/tempo. Il
manquait le dernier maillon de la chaîne décrite dans le README de Forge :
une **bibliothèque** où déposer les séquences retenues, et surtout un moyen
de les **retrouver par ce qu'elles évoquent** — « une nappe mélancolique de
8 mesures autour de 90 BPM » — pour les glisser ensuite dans n'importe quel
DAW (le `.mid` est standard ; REAPER a en plus le pont `reaper.py`).

    forge / loops / transcription
              │  (.mid)
              ▼
        libretto library add   ──►  index JSON (empreinte 29 axes + émotion)
              │
              ▼
        libretto library search "mélancolique 8 mesures ~90 bpm"
              │
              ▼
        les .mid les plus proches, prêts à déposer dans Logic/Ableton/…

Ce qui est stocké, ce qui ne l'est pas
--------------------------------------
L'index ne contient que des **chemins** et des **mesures** — jamais une
copie du MIDI, exactement comme `loop_index`. Chaque entrée porte : la
tonalité et le mode estimés (avec leur source et leur marge, pour qu'un
consommateur méfiant puisse filtrer), le tempo, la longueur en mesures, le
score SMS et sa fiabilité, l'empreinte des 29 axes, et le profil émotionnel
(`emotion.py`). La recherche n'invente rien qui ne soit déjà mesuré.

Honnêteté du classement
-----------------------
Sans mot affectif, la recherche retombe sur le tri **fiabilité d'abord**
de Forge : une séquence bien construite et fiable passe devant une mieux
notée mais douteuse. Avec une intention, on classe par distance dans
l'espace (valence, énergie, tension) — puis la fiabilité départage. Le
profil émotionnel est une projection lisible des axes (voir `emotion.py`),
pas un oracle : la seule validation reste l'oreille.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .axes import PARENT_MODE, PC_NAMES, SenseOfMusicalStructure, estimate_key
from .builder import build_score
from .emotion import (key_label, profile_from_axes, target_from_words,
                      weighted_distance)
from .midi import MidiData, parse_midi

BEATS_PER_BAR = 4
LIBRARY_VERSION = 1


# ──────────────────────────────────────────────
# Extraction des métadonnées d'un MIDI
# ──────────────────────────────────────────────

def _hist(md: MidiData) -> list[float]:
    """Histogramme des classes de hauteur pondéré par la durée, canal 9
    (percussions GM) exclu — la matière de l'estimation tonale."""
    h = [0.0] * 12
    for n in md.notes:
        if n.channel == 9:
            continue
        h[n.pitch % 12] += max(1, n.end - n.start)
    return h


def _bars_and_bpm(md: MidiData, score) -> tuple[int, float | None]:
    beats_per_bar = (score.time_signature_num * 4.0 / score.time_signature_den) or 4.0
    beats = (md.end_tick / md.ppq) if md.ppq else 0.0
    bars = max(1, round(beats / beats_per_bar)) if beats else 1
    bpm: float | None = None
    if md.tempos:
        bpm = round(md.tempos[0][1], 2)
    elif score.tempo_map:
        bpm = float(score.tempo_map[0][1])
    return bars, bpm


@dataclass
class Entry:
    path: str
    sha1: str
    tonic: int | None
    mode: str | None
    key_source: str            # "override" | "estimé"
    key_margin: float | None   # marge Krumhansl-Kessler si estimée
    bpm: float | None
    bars: int
    global_score: float
    confidence: float
    confidence_level: str
    axes: dict[str, float]     # empreinte : id d'axe -> score
    emotion: dict              # EmotionProfile.to_dict()
    tags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str | None:
        return key_label(self.tonic, self.mode)

    @property
    def emotion_point(self) -> tuple[float, float, float]:
        e = self.emotion
        return (e["valence"], e["energy"], e["tension"])


def analyze_entry(path: str | Path,
                  *,
                  weights: dict[str, float] | None = None,
                  tonic: int | None = None,
                  mode: str | None = None,
                  bpm: float | None = None,
                  bars: int | None = None,
                  tags: list[str] | None = None) -> Entry:
    """Construit l'entrée de bibliothèque d'un fichier MIDI.

    Les champs tonalité/mode/tempo/mesures sont **estimés** par défaut, mais
    peuvent être IMPOSÉS par le générateur qui les connaît (Forge sait qu'il
    a demandé « 8 mesures en ré mineur »). Un override est marqué
    `key_source="override"` et ne porte pas de marge d'estimation.
    """
    p = Path(path)
    data = p.read_bytes()
    md = parse_midi(p)
    score = build_score(md)
    if not score.sections:
        raise ValueError(f"aucune note exploitable dans {p.name}")

    sms = SenseOfMusicalStructure(score, weights=weights)
    sms.calculate()

    est_bars, est_bpm = _bars_and_bpm(md, score)
    if mode is None or tonic is None:
        pc, est_mode, _corr, margin = estimate_key(_hist(md))
        e_tonic = pc if tonic is None else tonic
        e_mode = est_mode if mode is None else mode
        key_source = "override" if (tonic is not None and mode is not None) else "estimé"
        key_margin = None if key_source == "override" else round(margin, 4)
    else:
        e_tonic, e_mode, key_source, key_margin = tonic, mode, "override", None

    use_bpm = bpm if bpm is not None else est_bpm
    use_bars = bars if bars is not None else est_bars
    density = _mean_density(score)

    prof = profile_from_axes(sms, mode=e_mode or "maj", bpm=use_bpm, density=density)

    return Entry(
        path=str(p.resolve()),
        sha1=hashlib.sha1(data).hexdigest(),
        tonic=e_tonic, mode=e_mode, key_source=key_source, key_margin=key_margin,
        bpm=use_bpm, bars=use_bars,
        global_score=round(sms.get_score(), 4),
        confidence=round(sms.confidence(), 4),
        confidence_level=sms.confidence_level(),
        axes={a.id: round(a.score, 4) for a in sms.axes},
        emotion=prof.to_dict(),
        tags=list(tags or []),
    )


def _mean_density(score) -> float:
    vals = [s.note_density for s in score.sections if s.note_density > 0]
    return sum(vals) / len(vals) if vals else 0.0


# ──────────────────────────────────────────────
# Bibliothèque (fichier JSON)
# ──────────────────────────────────────────────

class Library:
    """Index JSON de séquences MIDI. Clé d'unicité : chemin absolu (un
    même fichier ré-ajouté est mis à jour, pas dupliqué)."""

    def __init__(self, entries: list[Entry] | None = None):
        self.entries: list[Entry] = entries or []

    # ---- persistance ----

    @classmethod
    def load(cls, path: str | Path) -> "Library":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = json.loads(p.read_text(encoding="utf-8"))
        entries = [Entry(**e) for e in raw.get("entries", [])]
        return cls(entries)

    def save(self, path: str | Path) -> None:
        payload = {
            "version": LIBRARY_VERSION,
            "entries": [asdict(e) for e in self.entries],
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- mutation ----

    def add(self, entry: Entry) -> bool:
        """Ajoute ou remplace (par chemin). True si nouvelle entrée."""
        for i, e in enumerate(self.entries):
            if e.path == entry.path:
                # on préserve les tags posés à la main
                entry.tags = sorted(set(entry.tags) | set(e.tags))
                self.entries[i] = entry
                return False
        self.entries.append(entry)
        return True

    # ---- recherche ----

    def search(self, query: str, *, limit: int = 5,
               bpm_tol: float = 15.0, bars_tol: int = 2) -> list["SearchHit"]:
        return search(self.entries, query, limit=limit,
                      bpm_tol=bpm_tol, bars_tol=bars_tol)


# ──────────────────────────────────────────────
# Analyse d'une requête et classement
# ──────────────────────────────────────────────

# Mots de tempo → BPM cible (utilisés seulement si aucun nombre n'est donné),
# avec une tolérance large : ce sont des intentions, pas des mesures.
TEMPO_WORDS = {
    "lent": 70.0, "lente": 70.0, "posé": 75.0, "pose": 75.0,
    "modéré": 100.0, "modere": 100.0, "moderé": 100.0, "mid": 100.0,
    "rapide": 140.0, "vif": 140.0, "enlevé": 150.0, "enleve": 150.0,
}
TEMPO_WORD_TOL = 28.0

_BARS_RE = re.compile(r"(\d{1,3})\s*(mesures?|bars?|barres?)", re.IGNORECASE)
_BPM_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*bpm", re.IGNORECASE)
# tonique A-G, altération optionnelle, mode optionnel collé ou séparé
_KEY_RE = re.compile(
    r"(?<![A-Za-z])([A-G])(#|b|♯|♭)?\s*"
    r"(maj(?:eur|or)?|min(?:eur|or)?|m|dorien|dorian|mixolydien|mixolydian)?"
    r"(?![A-Za-z])")

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def tonic_to_pc(text: str) -> int:
    """« D », « F# », « Bb » -> classe de hauteur (0-11). Lève ValueError
    sur une entrée qui n'est pas une note. Utilisé par l'option --tonic."""
    m = re.fullmatch(r"\s*([A-Ga-g])(#|b|♯|♭)?\s*", text or "")
    if not m:
        raise ValueError(f"tonique invalide : {text!r} (attendu A-G, ex. D, F#, Bb)")
    pc = _LETTER_PC[m.group(1).upper()]
    acc = m.group(2)
    if acc in ("#", "♯"):
        pc = (pc + 1) % 12
    elif acc in ("b", "♭"):
        pc = (pc - 1) % 12
    return pc


def _parse_key(text: str) -> tuple[int, str] | tuple[int, None] | None:
    """(classe de hauteur, mode|None) trouvé dans une requête, ou None.

    Une lettre nue « A-G » sans altération ni mode n'est PAS retenue : trop
    de faux positifs (le « a » de « lente »). Il faut une altération ou un
    mode explicite pour parier sur une tonalité."""
    for m in _KEY_RE.finditer(text):
        letter, acc, mode = m.group(1), m.group(2), m.group(3)
        if not acc and not mode:
            continue
        pc = _LETTER_PC[letter]
        if acc in ("#", "♯"):
            pc = (pc + 1) % 12
        elif acc in ("b", "♭"):
            pc = (pc - 1) % 12
        norm = None
        if mode:
            ml = mode.lower()
            if ml in ("m", "min", "mineur", "minor"):
                norm = "min"
            elif ml.startswith("maj") or ml in ("major",):
                norm = "maj"
            elif ml.startswith("dorien") or ml.startswith("dorian"):
                norm = "dorien"
            elif ml.startswith("mixolyd"):
                norm = "mixolydien"
        return pc, norm
    return None


@dataclass
class Query:
    """Contraintes extraites d'une requête libre."""
    emotion_target: tuple[float, float, float] | None = None
    emotion_words: list[str] = field(default_factory=list)
    bpm: float | None = None
    bpm_tol: float = 15.0
    bars: int | None = None
    tonic: int | None = None
    mode: str | None = None

    def describe(self) -> str:
        bits = []
        if self.emotion_words:
            bits.append("émotion=" + "+".join(self.emotion_words))
        if self.bpm is not None:
            bits.append(f"~{round(self.bpm)} BPM (±{round(self.bpm_tol)})")
        if self.bars is not None:
            bits.append(f"{self.bars} mesures")
        if self.tonic is not None:
            bits.append(key_label(self.tonic, self.mode) or PC_NAMES[self.tonic])
        return ", ".join(bits) if bits else "(aucune contrainte)"


def parse_query(text: str, *, bpm_tol: float = 15.0, bars_tol: int = 2) -> Query:
    q = Query(bpm_tol=bpm_tol)

    mb = _BARS_RE.search(text)
    if mb:
        q.bars = int(mb.group(1))
    # BPM : d'abord « NN bpm », sinon un nombre nu 40-240 hors segment mesures
    consumed = text[mb.start():mb.end()] if mb else ""
    rest = text.replace(consumed, " ", 1) if consumed else text
    mp = _BPM_RE.search(rest)
    if mp:
        q.bpm = float(mp.group(1))
    else:
        for num in re.findall(r"(?<![A-Za-z\d.])(\d{2,3})(?![A-Za-z\d.])", rest):
            v = float(num)
            if 40 <= v <= 240:
                q.bpm = v
                break

    key = _parse_key(text)
    if key:
        q.tonic, q.mode = key[0], key[1]

    emo = target_from_words(text)
    if emo:
        q.emotion_target, q.emotion_words = emo

    # intention de tempo par mot, seulement si aucun nombre n'a été donné
    if q.bpm is None:
        from .emotion import _fold
        words = set(_fold(text).split())
        for w, val in TEMPO_WORDS.items():
            if _fold(w) in words:
                q.bpm, q.bpm_tol = val, max(q.bpm_tol, TEMPO_WORD_TOL)
                break
    return q


@dataclass
class SearchHit:
    entry: Entry
    distance: float | None      # distance affective (None si pas d'intention)
    reasons: list[str] = field(default_factory=list)


_LEVEL_RANK = {"élevée": 0, "moyenne": 1, "faible": 2, "insuffisante": 3}


def _passes_filters(e: Entry, q: Query, bars_tol: int) -> bool:
    if q.tonic is not None and e.tonic != q.tonic:
        return False
    if q.mode is not None and e.mode is not None:
        # on compare au mode parent : un thème « mineur » accepte un dorien
        if PARENT_MODE.get(e.mode, e.mode) != PARENT_MODE.get(q.mode, q.mode):
            return False
    if q.bpm is not None:
        if e.bpm is None or abs(e.bpm - q.bpm) > q.bpm_tol:
            return False
    if q.bars is not None and abs(e.bars - q.bars) > bars_tol:
        return False
    return True


def search(entries: list[Entry], text: str, *, limit: int = 5,
           bpm_tol: float = 15.0, bars_tol: int = 2) -> list[SearchHit]:
    """Classe les entrées pour une requête libre.

    Filtres durs : tonalité, mode, tempo (±tol), longueur (±tol). Classement
    des survivants : par distance affective si une intention est exprimée
    (fiabilité en départage), sinon fiabilité d'abord puis score — le contrat
    de Forge."""
    q = parse_query(text, bpm_tol=bpm_tol, bars_tol=bars_tol)
    pool = [e for e in entries if _passes_filters(e, q, bars_tol)]

    hits: list[SearchHit] = []
    for e in pool:
        if q.emotion_target is not None:
            dist = weighted_distance(e.emotion_point, q.emotion_target)
        else:
            dist = None
        hits.append(SearchHit(entry=e, distance=dist,
                              reasons=_reasons(e, q)))

    if q.emotion_target is not None:
        hits.sort(key=lambda h: (h.distance,
                                 _LEVEL_RANK.get(h.entry.confidence_level, 9),
                                 -h.entry.global_score))
    else:
        hits.sort(key=lambda h: (_LEVEL_RANK.get(h.entry.confidence_level, 9),
                                 -h.entry.global_score))
    return hits[:limit]


def _reasons(e: Entry, q: Query) -> list[str]:
    out = []
    if q.emotion_words:
        out.append("émotion « " + ", ".join(e.emotion.get("descriptors", [])) + " »")
    if q.bpm is not None and e.bpm is not None:
        out.append(f"{round(e.bpm)} BPM")
    if q.bars is not None:
        out.append(f"{e.bars} mesures")
    if e.key:
        out.append(e.key)
    return out
