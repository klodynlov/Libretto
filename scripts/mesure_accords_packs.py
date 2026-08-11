"""Mesure de la détection d'accords contre une vérité terrain **externe**.

Usage :
    python3 scripts/mesure_accords_packs.py "~/Desktop/MIDI Loops Packs/Toontrack - EZKeys MIDI Loops"

`scripts/mesure_accords.py` mesure `_best_chord` contre le corpus **généré** :
la vérité terrain y est celle que le générateur vient d'écrire. Ce harnais-ci
la confronte à une étiquette **humaine, indépendante du moteur** — les packs
EZKeys nomment chaque accord `<FONDAMENTALE>_<QUALITE>_<HIT|RHY>.mid`
(`Gb_MAJ7TH_HIT.mid`, `A_MIN7TH_RHY.mid`). C'est la première vérification du
détecteur d'accords sur de la matière commerciale jamais vue par la
calibration (le bonus de fondamentale de `_best_chord` a été réglé sur graines
7/11/23/31 du générateur — voir sa docstring).

`HIT` = un accord bloc simultané ; `RHY` = le même accord joué en rythme sur
une mesure. On construit le chroma (durée × vélocité par classe de hauteur,
percussions exclues), on appelle `_best_chord`, on compare fondamentale ET
qualité.

Les six gabarits du moteur (maj, min, dim, dom7, maj7, min7) couvrent cinq
labels EZKeys sans ambiguïté : MAJOR→maj, MINOR→min, MAJ7TH→maj7, MIN7TH→min7,
7TH→dom7. Les autres (6TH, SUS2/4, AUG, ADD9, 9/11/13TH, DIM7TH…) sortent du
vocabulaire : le moteur n'a pas de gabarit pour eux et **doit** les ramener au
plus proche. On ne les compte pas comme des échecs — on rapporte à part vers
quoi ils dégradent, parce que la façon dont un modèle se trompe hors de son
périmètre en dit autant que son exactitude dedans.

Résultat mesuré (EZKeys, 12 fondamentales × 5 qualités connues) :

    jeu   comparés   fondamentale   qualité   fond.+qualité
    HIT     2880        100.0 %       99.1 %      99.1 %
    RHY     2858         99.7 %       99.4 %      99.3 %

Le 99 % du générateur (`scripts/mesure_accords.py`) **généralise** à du
commercial jamais vu : la calibration du bonus de fondamentale n'était pas un
surajustement aux graines. Seul résidu dans le vocabulaire : min7→min (26 des
576 min7 en HIT, 4,5 %), quand la 7e mineure est trop brève/faible dans le
voicing et que l'accord retombe sur son triade. Hors gabarit, la dégradation est saine —
DIM7TH→dim (le triade est le bon sous-ensemble), SUS/AUG→maj et 7SUS4/13TH→
dom7 avec la fondamentale presque toujours juste.

100 % stdlib. Le pack est commercial (hors dépôt) : ce script ne versionne que
le harnais, on lui passe le chemin du pack en argument.
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from libretto.builder import _best_chord  # noqa: E402
from libretto.midi import parse_midi  # noqa: E402

ROOT_PC = {
    "C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "E#": 5, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
    "BB": 10, "B": 11, "CB": 11,
}
# label EZKeys -> qualité du moteur (gabarits de builder.CHORD_TEMPLATES)
INVOCAB = {"MAJOR": "maj", "MINOR": "min", "MAJ7TH": "maj7", "MIN7TH": "min7", "7TH": "dom7"}
NAME = re.compile(r"^([A-G][b#]?)_([A-Z0-9#-]+)_(HIT|RHY)\.mid$", re.I)


def chroma_of(md) -> list[float]:
    """Poids par classe de hauteur (durée × vélocité), percussions exclues."""
    ch = [0.0] * 12
    for n in md.notes:
        if n.channel == 9:  # canal 10 GM : jamais tonal
            continue
        ch[n.pitch % 12] += (n.end - n.start) * n.velocity
    return ch


def run(root: Path, kind: str):
    inv = collections.Counter()            # (fond_ok, qual_ok) sur vocab connu
    conf = collections.Counter()           # qualité (vraie -> détectée)
    n_by_q = collections.Counter()
    root_by_q = collections.Counter()
    oov = collections.Counter()            # hors gabarit : (label, détectée, fond_ok?)
    none_n = unreadable = seen = 0
    for p in root.rglob("*.mid"):
        m = NAME.match(p.name)
        if not m:
            continue
        root_tok, qual_tok, tag = m.group(1).upper(), m.group(2).upper(), m.group(3).upper()
        if tag != kind or root_tok not in ROOT_PC:
            continue
        seen += 1
        true_pc = ROOT_PC[root_tok]
        try:
            md = parse_midi(p)
        except Exception:
            unreadable += 1
            continue
        chord = _best_chord(chroma_of(md))
        if chord is None:
            none_n += 1
            continue
        det_pc, det_q = chord.root.pc, chord.quality
        if qual_tok in INVOCAB:
            true_q = INVOCAB[qual_tok]
            r_ok = det_pc == true_pc
            inv[(r_ok, det_q == true_q)] += 1
            conf[(true_q, det_q)] += 1
            n_by_q[true_q] += 1
            root_by_q[true_q] += int(r_ok)
        else:
            oov[(qual_tok, det_q, det_pc == true_pc)] += 1
    return dict(seen=seen, none_n=none_n, unreadable=unreadable, inv=inv,
                conf=conf, n_by_q=n_by_q, root_by_q=root_by_q, oov=oov)


def report(kind: str, r: dict) -> None:
    inv, conf, oov = r["inv"], r["conf"], r["oov"]
    tot = sum(inv.values())
    print(f"\n===== {kind} — vocabulaire connu (maj/min/maj7/min7/dom7) =====")
    print(f"vus: {r['seen']} | comparés: {tot} | None: {r['none_n']} | illisibles: {r['unreadable']}")
    if tot:
        root_ok = sum(v for (ro, _q), v in inv.items() if ro)
        qual_ok = sum(v for (_ro, q), v in inv.items() if q)
        both = inv[(True, True)]
        print(f"  fondamentale exacte : {root_ok:5d}/{tot}  {100 * root_ok / tot:5.1f}%")
        print(f"  qualité exacte      : {qual_ok:5d}/{tot}  {100 * qual_ok / tot:5.1f}%")
        print(f"  fondamentale+qualité: {both:5d}/{tot}  {100 * both / tot:5.1f}%   <<<")
        print("  par qualité (fond.+qual exacts) :")
        for q in ("maj", "min", "maj7", "min7", "dom7"):
            n = r["n_by_q"][q]
            if n:
                print(f"    {q:5s} n={n:4d}  qual {100 * conf[(q, q)] / n:5.1f}%  "
                      f"fond {100 * r['root_by_q'][q] / n:5.1f}%")
        seuil = max(5, tot // 200)
        rows = [((tq, dq), v) for (tq, dq), v in conf.items() if tq != dq and v >= seuil]
        if rows:
            print("  confusions qualité (vraie -> détectée) :")
            for (tq, dq), v in sorted(rows, key=lambda x: -x[1]):
                print(f"    {tq:5s} -> {dq:5s} : {v}")
    if oov:
        print(f"  --- hors gabarit ({kind}) : dégradation par label ---")
        by_label = collections.defaultdict(collections.Counter)
        ntot, rootok = collections.Counter(), collections.Counter()
        for (lab, dq, r_ok), v in oov.items():
            by_label[lab][dq] += v
            ntot[lab] += v
            rootok[lab] += v if r_ok else 0
        for lab in sorted(ntot, key=lambda l: -ntot[l]):
            tops = ", ".join(f"{q}:{c}" for q, c in by_label[lab].most_common(2))
            print(f"    {lab:9s} n={ntot[lab]:4d}  fond {100 * rootok[lab] / ntot[lab]:4.0f}%  -> {tops}")


def main(argv: list[str]) -> int:
    default = Path.home() / "Desktop/MIDI Loops Packs/Toontrack - EZKeys MIDI Loops"
    root = Path(argv[1]).expanduser() if len(argv) > 1 else default
    if not root.is_dir():
        print(f"pack introuvable : {root}\n"
              f"usage : python3 scripts/mesure_accords_packs.py <racine_pack_EZKeys>", file=sys.stderr)
        return 2
    print(f"pack : {root}")
    for kind in ("HIT", "RHY"):
        report(kind, run(root, kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
