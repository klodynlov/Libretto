"""Neutral candidate auditions and durable personal choices, stdlib only.

Choices are observations, not training labels for automatic reweighting.
Candidate hashes survive temporary generation paths. Atomic replacement
preserves an existing file when writing fails; a lock serializes this server.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .midi import MidiData, tick_to_seconds

PREVIEW_SECONDS = 20.0
MAX_PREVIEW_NOTES = 4000


def preview_notes(md: MidiData) -> dict:
    """Same start, duration, and timbre rules for every candidate; no quantizing."""
    notes = []
    total = 0
    for n in sorted(md.notes, key=lambda x: x.start):
        start = tick_to_seconds(md, n.start)
        if start >= PREVIEW_SECONDS:
            break
        total += 1
        if len(notes) < MAX_PREVIEW_NOTES:
            duration = min(PREVIEW_SECONDS, tick_to_seconds(md, n.end)) - start
            notes.append([start, max(0.001, duration), n.pitch, n.velocity, n.channel == 9])
    return {"notes": notes, "duration": min(PREVIEW_SECONDS,
             max((n[0] + n[1] for n in notes), default=0.0)),
            "truncated": total > len(notes), "renderer": "synth-v1"}


class ListeningStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.comparisons: dict[str, dict] = {}

    def create(self, results: list[dict], params: dict) -> dict | None:
        distinct, seen = [], set()
        for candidate in results:
            digest = hashlib.sha256(Path(candidate["path"]).read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            distinct.append({**candidate, "sha256": digest})
        if len(distinct) < 2:
            return None
        # Forge's shortlist already selects for diversity. Keep its first
        # three distinct performances, then conceal their ranking and names.
        distinct = distinct[:3]
        random.SystemRandom().shuffle(distinct)
        comparison = {"id": uuid.uuid4().hex, "params": params,
                      "candidates": {letter: c for letter, c in zip("ABC", distinct)}}
        with self.lock:
            self.comparisons[comparison["id"]] = comparison
        return {"id": comparison["id"], "slots": list(comparison["candidates"])}

    def candidate(self, comparison_id: str, slot: str) -> dict:
        with self.lock:
            try:
                return dict(self.comparisons[comparison_id]["candidates"][slot])
            except KeyError:
                raise ValueError("comparaison ou candidat inconnu") from None

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "judgements": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("judgements"), list)):
            raise ValueError("fichier de préférences incompatible ; fichier conservé")
        return data

    def count(self) -> int:
        with self.lock:
            return len(self._load()["judgements"])

    def record(self, comparison_id: str, choice: str) -> dict:
        with self.lock:
            comp = self.comparisons.get(comparison_id)
            if comp is None:
                raise ValueError("comparaison inconnue")
            if choice not in (*comp["candidates"], "same"):
                raise ValueError("choix invalide")
            data = self._load()
            row = {"id": comparison_id, "choice": choice,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "renderer": "synth-v1", "preview_seconds": PREVIEW_SECONDS,
                   "params": comp["params"], "candidates": comp["candidates"]}
            rows = data["judgements"]
            existing = next((i for i, r in enumerate(rows) if r["id"] == comparison_id), None)
            if existing is None:
                rows.append(row)
            else:
                rows[existing] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                        dir=self.path.parent, prefix=".libretto-", delete=False) as f:
                    temp_path = Path(f.name)
                    json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.path)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            return {"saved": True, "count": len(rows), "choice": choice,
                    "candidates": {slot: {"name": c["name"], "score": c.get("score")}
                                   for slot, c in comp["candidates"].items()}}
