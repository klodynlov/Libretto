#!/usr/bin/env python3
"""Reproducteur du corpus bkmaf : verse les analyses SMS des MIDI ~/bkmaf-dl
dans `corpus_bkmaf/manifest.json`.

Chaque entrée = `libretto.library.analyze_entry` (empreinte 29 axes + émotion +
clé/bpm/mesures + sha1), chemin RELATIF au corpus. Les MIDI ne sont ni copiés ni
commités (musique réelle, cf. corpus_bkmaf/PROVENANCE.md) — le corpus vit dans
`~/bkmaf-dl` et se récupère depuis bkmaf.com (gratuit, dons).

    python examples/build_bkmaf_manifest.py [CORPUS_DIR]
"""
import sys, os, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from libretto.library import analyze_entry

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.path.expanduser("~/bkmaf-dl"))
OUTDIR = REPO / "corpus_bkmaf"
GROUP_TAGS = {"musique-creole": "creole", "zouk-pur": "zouk"}


def main() -> int:
    if not ROOT.is_dir():
        print(f"corpus introuvable : {ROOT}", file=sys.stderr); return 2
    files = sorted(p for p in ROOT.rglob("*") if p.suffix.lower() in (".mid", ".kar"))
    entries, skipped = [], []
    for i, p in enumerate(files):
        rel = p.relative_to(ROOT)
        group = rel.parts[0] if len(rel.parts) > 1 else "(racine)"
        tags = [GROUP_TAGS.get(group, group), "antilles", "bkmaf"]
        try:
            e = analyze_entry(p, tags=tags)
        except Exception as ex:
            skipped.append({"file": str(rel), "why": str(ex)[:120]})
            print(f"  [skip] {p.name[:50]} — {ex}"); continue
        entries.append({
            "file": str(rel), "group": group, "sha1": e.sha1,
            "key": e.key, "tonic": e.tonic, "mode": e.mode,
            "key_source": e.key_source, "key_margin": e.key_margin,
            "bpm": round(e.bpm, 1) if e.bpm else None, "bars": e.bars,
            "global_score": e.global_score,
            "confidence": e.confidence, "confidence_level": e.confidence_level,
            "axes": e.axes, "emotion": e.emotion, "tags": e.tags,
        })
        print(f"  [{int((i+1)/len(files)*100):3d}%] {group:14s} {e.key or '?':11s} "
              f"g={e.global_score:.3f} {p.name[:36]}")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "corpus": "bkmaf — La Bibliothèque Kar & Midi des Artistes Francophones",
        "source": "bkmaf.com (gratuit, financé par dons ; catalogue individuel libre)",
        "n": len(entries), "n_skipped": len(skipped),
        "note": "MIDI non redistribués (musique réelle) ; reproductible depuis ~/bkmaf-dl. "
                "Analyses = analyze_entry (SMS 29 axes + émotion + clé/bpm/mesures).",
        "entries": entries, "skipped": skipped,
    }
    (OUTDIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"\nmanifest -> {OUTDIR/'manifest.json'}  ({len(entries)} entrées, {len(skipped)} skip)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
