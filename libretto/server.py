"""
Libretto — interface web locale (stdlib pure, aucun framework).

`libretto serve` puis http://127.0.0.1:8787 (bascule auto sur un port libre
si occupé — 8765 appartient au dashboard Library Brain sur cette machine) :
- glisser-déposer des .mid → analyse SMS complète (radar + 29 axes) ;
- comparateur : les analyses de la session s'empilent, triées par score ;
- recherche par intention dans la bibliothèque (`serve --lib lib.json`) :
  « mélancolique 8 mesures ~90 bpm » classe les séquences indexées ;
- génération de séquences (`serve --generate`, ou `--corpus DIR` pour le
  modèle appris) : Forge produit des candidats et garde les mieux construits,
  chacun téléchargeable, poussable dans REAPER, indexable en un clic ;
- bouton « ▶ Reaper » : pousse le fichier dans REAPER via le pont Klody
  (:9000) et lance la lecture — sur une analyse déposée, un résultat de
  recherche, ou une séquence générée.

Le cœur (`libretto`) ne dépend PAS des générateurs (qui vivent dans
`examples/`) : la CLI fournit un rappel `generator`, le serveur l'appelle
sans le connaître — panneau masqué si absent, comme la recherche sans `--lib`.

API : GET /api/analyses · GET /api/download?path=… (fichier généré) ·
POST /api/analyze (octets MIDI, en-tête X-Filename) · POST /api/search
{"query": …} · POST /api/generate {"mode","n","shortlist","bars","seed"} ·
POST /api/library_add {"path"} (fichier généré) · POST /api/reaper {"id": N}
ou {"path": …} (généré ou entrée de la bibliothèque).
"""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .axes import GROUP_NAMES, SenseOfMusicalStructure
from .builder import build_score
from .midi import parse_midi_bytes
from .model import PC_NAMES
from .report import _radar_svg

MAX_UPLOAD = 8 * 1024 * 1024  # 8 Mio, très large pour du MIDI


class _Store:
    def __init__(self, lib_path: str | None = None,
                 generator=None, gen_modes: list[str] | None = None):
        self.lock = threading.Lock()
        self.entries: list[dict] = []
        self.raw: dict[int, bytes] = {}
        self.next_id = 1
        # Chemin du fichier d'index cherchable (None = onglet recherche masqué).
        # On le relit à chaque requête pour refléter les ajouts hors interface
        # (par ex. `forge_library.py` qui verse un run entre deux recherches).
        self.lib_path = lib_path
        # Rappel de génération (fourni par la CLI — le cœur reste sans
        # dépendance aux exemples) et modes qu'il annonce. None = panneau masqué.
        self.generator = generator
        self.gen_modes = gen_modes or []
        # Fichiers produits par la génération, autorisés au téléchargement et au
        # push Reaper. On ne sert JAMAIS un chemin arbitraire du disque : seuls
        # les fichiers de cet ensemble (et les entrées de la bibliothèque) le sont.
        self.generated: set[str] = set()
        self._gen_root: str | None = None
        self._gen_seq = 0

    def add(self, entry: dict, raw: bytes) -> dict:
        with self.lock:
            entry["id"] = self.next_id
            self.next_id += 1
            self.entries.append(entry)
            self.raw[entry["id"]] = raw
        return entry

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.entries)

    def raw_for(self, entry_id: int) -> bytes | None:
        with self.lock:
            return self.raw.get(entry_id)

    def new_workdir(self) -> Path:
        """Sous-dossier unique pour un run de génération (jamais deux runs
        concurrents dans le même dossier)."""
        with self.lock:
            if self._gen_root is None:
                self._gen_root = tempfile.mkdtemp(prefix="libretto_gen_")
            self._gen_seq += 1
            d = Path(self._gen_root) / f"run_{self._gen_seq}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def register_generated(self, paths) -> None:
        with self.lock:
            for p in paths:
                self.generated.add(str(Path(p).resolve()))

    def is_generated(self, path: str) -> bool:
        with self.lock:
            return str(Path(path).resolve()) in self.generated


def analyze_bytes(data: bytes, filename: str) -> tuple[dict, "SenseOfMusicalStructure"]:
    md = parse_midi_bytes(data, origin=filename)
    score = build_score(md)
    if not score.sections:
        raise ValueError(f"{filename} : aucune note exploitable")
    sms = SenseOfMusicalStructure(score)
    sms.calculate()
    payload = sms.to_dict()
    def _row(a: dict) -> str:
        # La classe est calculée hors f-string : un backslash dans une
        # expression f-string est une erreur de syntaxe avant Python 3.12,
        # et le projet cible 3.10+.
        cls = ' class="dim"' if a["confidence"] < 0.5 else ""
        return (
        f'<tr{cls}>'
        f'<td class="mono">{a["id"]}</td><td>{a["name"]}</td>'
        f'<td class="mono">{a["group"]}</td>'
        f'<td><div class="bar"><div class="fill" style="width:{round(a["score"] * 100)}%">'
        f'</div></div></td><td class="mono score">{a["score"]:.2f}</td>'
        f'<td class="mono conf">{a["confidence"]:.2f}</td></tr>')

    rows = "".join(_row(a) for a in payload["axes"])
    entry = {
        "name": filename,
        "global_score": payload["global_score"],
        # Le comparateur trie par score : sans la fiabilité à côté, une
        # boucle de 4 mesures peut se retrouver classée devant un morceau.
        "confidence": payload["confidence"],
        "confidence_level": payload["confidence_level"],
        "interpretable": sms.is_interpretable(),
        "diagnosis": "" if sms.is_interpretable() else sms.diagnosis(),
        "groups": payload["groups"],
        "key": f"{PC_NAMES[score.key_signature.pc]}",
        "sections": [s.label for s in score.sections],
        "n_notes": len(md.notes),
        "radar": _radar_svg(payload["groups"], size=240),
        "rows": rows,
    }
    return entry, sms


def search_library(lib_path: str, query: str, *, limit: int = 8,
                   bpm_tol: float = 15.0, bars_tol: int = 2) -> dict:
    """Cherche dans la bibliothèque et sérialise les résultats pour l'UI.
    Relit l'index à chaque appel (les ajouts hors interface comptent)."""
    from pathlib import Path

    from .library import Library, parse_query, search
    lib = Library.load(lib_path)
    q = parse_query(query, bpm_tol=bpm_tol, bars_tol=bars_tol)
    if not lib.entries:
        return {"query": q.describe(), "empty": True, "entries": [], "count": 0}
    hits = search(lib.entries, query, limit=limit,
                  bpm_tol=bpm_tol, bars_tol=bars_tol)
    rows = []
    for h in hits:
        e = h.entry
        emo = e.emotion
        rows.append({
            "path": e.path, "name": Path(e.path).name,
            "distance": h.distance, "reasons": h.reasons,
            "key": e.key, "bpm": e.bpm, "bars": e.bars,
            "global_score": e.global_score, "confidence": e.confidence,
            "confidence_level": e.confidence_level,
            "descriptors": emo.get("descriptors", []),
            "valence": emo.get("valence"), "energy": emo.get("energy"),
            "tension": emo.get("tension"), "tags": e.tags,
        })
    return {"query": q.describe(), "empty": False,
            "entries": rows, "count": len(rows)}


def push_library_path(lib_path: str, path: str, *, play: bool = True) -> dict:
    """Pousse dans REAPER une entrée de la bibliothèque, désignée par son
    chemin. Le chemin DOIT appartenir à l'index chargé — on ne pousse pas un
    fichier arbitraire du disque depuis le navigateur. La piste est nommée
    par l'intention de l'entrée, comme `library search --reaper`."""
    from .library import Library
    from .midi import parse_midi
    from .reaper import push_mididata
    lib = Library.load(lib_path)
    entry = next((e for e in lib.entries if e.path == path), None)
    if entry is None:
        raise ValueError("séquence absente de la bibliothèque")
    label = ", ".join(entry.emotion.get("descriptors", [])[:2])
    names = [label] if label else None
    return push_mididata(parse_midi(entry.path), track_names=names, play=play)


def push_generated_path(path: str, *, play: bool = True) -> dict:
    """Pousse dans REAPER un fichier fraîchement généré (pas encore indexé).
    L'appelant a déjà vérifié qu'il appartient à l'ensemble des générés."""
    from .midi import parse_midi
    from .reaper import push_mididata
    return push_mididata(parse_midi(path), track_names=["Libretto généré"], play=play)


def add_generated_to_library(lib_path: str, path: str,
                             tags: list[str] | None = None) -> dict:
    """Indexe un fichier généré dans la bibliothèque cherchable, avec son
    profil émotionnel. Renvoie un résumé pour l'UI."""
    from pathlib import Path

    from .library import Library, analyze_entry
    entry = analyze_entry(path, tags=(tags or []) + ["généré"])
    lib = Library.load(lib_path)
    is_new = lib.add(entry)
    lib.save(lib_path)
    return {"name": Path(entry.path).name, "added": is_new,
            "key": entry.key, "bpm": entry.bpm, "bars": entry.bars,
            "descriptors": entry.emotion.get("descriptors", [])}


INDEX_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Libretto — SMS</title>
<style>
:root {
  --bg:#faf9f6; --fg:#1a1a1e; --muted:#6b6b74; --card:#fff; --grid:#d8d6cf;
  --accent:#7c5cff; --accent-soft:rgba(124,92,255,.18); --border:#e4e2db;
  --ok:#2e9e6b; --err:#c94f4f;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#14141a; --fg:#ecebf2; --muted:#9a99a6; --card:#1d1d26;
    --grid:#34343f; --accent:#9d85ff; --accent-soft:rgba(157,133,255,.22);
    --border:#2b2b36; }
}
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--fg); padding:32px 20px;
  font:15px/1.55 -apple-system,"SF Pro Text",Segoe UI,sans-serif; }
main { max-width:1080px; margin:0 auto; }
h1 { font-size:24px; letter-spacing:-.02em; }
h1 small { display:block; color:var(--muted); font-size:14px; font-weight:500; margin-top:2px; }
#drop { margin:20px 0; border:2px dashed var(--grid); border-radius:16px;
  padding:34px; text-align:center; color:var(--muted); cursor:pointer;
  transition:border-color .15s, background .15s; }
#drop.hover { border-color:var(--accent); background:var(--accent-soft); color:var(--fg); }
#status { min-height:22px; font-size:13px; color:var(--muted); margin-bottom:14px; }
#status .err { color:var(--err); }
#cards { display:flex; flex-direction:column; gap:16px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:16px;
  padding:18px 20px; }
.head { display:flex; flex-wrap:wrap; gap:18px; align-items:center; }
.gscore { font-size:44px; font-weight:700; color:var(--accent); letter-spacing:-.03em;
  min-width:110px; text-align:center; }
.gscore small { display:block; font-size:11px; color:var(--muted); font-weight:500;
  text-transform:uppercase; letter-spacing:.08em; }
.meta { flex:1; min-width:220px; }
.meta .fname { font-weight:650; font-size:16px; }
.meta .sub { color:var(--muted); font-size:13px; margin-top:2px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
.chip { border:1px solid var(--border); border-radius:999px; padding:2px 10px;
  font-size:12px; color:var(--muted); }
.chip b { color:var(--accent); margin-right:3px; }
.actions { display:flex; gap:8px; }
button { font:inherit; font-size:13.5px; border:1px solid var(--border);
  background:var(--card); color:var(--fg); border-radius:10px; padding:7px 14px;
  cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button:disabled { opacity:.55; cursor:default; }
details { margin-top:12px; }
summary { cursor:pointer; color:var(--muted); font-size:13px; }
.axes-wrap { overflow-x:auto; margin-top:10px; }
table { width:100%; border-collapse:collapse; font-size:13.5px; }
td { padding:5px 8px; border-bottom:1px solid var(--border); }
tr:last-child td { border-bottom:none; }
.mono { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12px; }
.score { font-weight:700; }
.bar { width:100px; height:7px; border-radius:4px; background:var(--grid); overflow:hidden; }
.fill { height:100%; background:var(--accent); }
tr.dim td { opacity:.45; }
tr.dim .fill { background:var(--muted); }
.conf { color:var(--muted); }
.gscore.untrusted { color:var(--muted); }
.gscore .conflabel { margin-top:4px; text-transform:none; letter-spacing:0; font-size:11px; }
.banner { background:rgba(180,97,13,.10); border:1px solid #b4610d; border-radius:10px;
  padding:9px 12px; margin-top:10px; font-size:13px; line-height:1.45; }
.banner b { color:#b4610d; }
.radar svg { display:block; }
footer { color:var(--muted); font-size:12.5px; margin-top:26px; }
#searchpanel { margin:20px 0 8px; }
.searchbar { display:flex; gap:8px; }
.searchbar input { flex:1; font:inherit; padding:9px 13px; border:1px solid var(--border);
  border-radius:10px; background:var(--card); color:var(--fg); }
.searchbar input:focus { outline:none; border-color:var(--accent); }
#searchmeta { min-height:19px; font-size:12.5px; color:var(--muted); margin:9px 2px 4px; }
#searchmeta .err { color:var(--err); }
#results { display:flex; flex-direction:column; gap:10px; }
.hit { background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:11px 14px; display:flex; gap:14px; align-items:center; }
.hit .rank { font-size:19px; font-weight:700; color:var(--accent);
  min-width:24px; text-align:center; }
.hit .body { flex:1; min-width:0; }
.hit .title { font-weight:600; }
.hit .why { color:var(--muted); font-size:12.5px; margin-top:2px; }
.hit .emo { display:flex; gap:5px; flex-wrap:wrap; margin-top:6px; }
.tagpill { border:1px solid var(--border); border-radius:999px; padding:1px 9px;
  font-size:11.5px; color:var(--muted); }
.tagpill.intent { color:var(--accent); border-color:var(--accent-soft); }
#genpanel { margin:18px 0 8px; border:1px solid var(--border); border-radius:14px;
  padding:16px 18px; background:var(--card); }
#genpanel h2 { font-size:15px; letter-spacing:-.01em; margin-bottom:2px; }
#genpanel .hint { color:var(--muted); font-size:12.5px; margin-bottom:12px; }
.genform { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:flex-end; }
.genfield { display:flex; flex-direction:column; gap:3px; }
.genfield label { font-size:11px; color:var(--muted); text-transform:uppercase;
  letter-spacing:.06em; }
.genform select, .genform input { font:inherit; font-size:13.5px; padding:7px 10px;
  border:1px solid var(--border); border-radius:9px; background:var(--bg); color:var(--fg); }
.genform input[type=number] { width:80px; }
.genform .grow { flex:1; }
#genmeta { min-height:19px; font-size:12.5px; color:var(--muted); margin:11px 2px 4px; }
#genmeta .err { color:var(--err); }
#genresults { display:flex; flex-direction:column; gap:9px; }
.gen { border:1px solid var(--border); border-radius:12px; padding:10px 13px;
  display:flex; gap:13px; align-items:center; }
.gen .badge { font-size:12px; font-weight:650; padding:2px 9px; border-radius:999px;
  border:1px solid var(--border); color:var(--muted); white-space:nowrap; }
.gen .badge.win { color:#fff; background:var(--accent); border-color:var(--accent); }
.gen .body { flex:1; min-width:0; }
.gen .title { font-weight:600; }
.gen .why { color:var(--muted); font-size:12.5px; margin-top:2px; }
.gen .acts { display:flex; gap:6px; flex-wrap:wrap; }
.gen a.btn { text-decoration:none; display:inline-block; }
</style>
</head>
<body>
<main>
  <h1>Libretto — Sense of Musical Structure
    <small>génère · analyse 29 axes · cherche par intention · écoute dans Reaper</small></h1>
  <section id="genpanel" hidden>
    <h2>Générer des séquences</h2>
    <div class="hint">Libretto génère des candidats et garde les mieux
      construits (gate de fiabilité, tri fiabilité-d'abord).</div>
    <div class="genform">
      <div class="genfield"><label for="genmode">modèle</label>
        <select id="genmode"></select></div>
      <div class="genfield"><label for="genn">candidats</label>
        <input id="genn" type="number" min="1" max="64" value="12"></div>
      <div class="genfield"><label for="genshort">variantes</label>
        <input id="genshort" type="number" min="0" max="12" value="4"></div>
      <div class="genfield"><label for="genbars">mesures</label>
        <input id="genbars" type="number" min="4" max="128" value="24"></div>
      <div class="genfield"><label for="genseed">graine</label>
        <input id="genseed" type="number" min="0" value="1"></div>
      <div class="genfield"><button class="primary" id="genbtn">Générer</button></div>
    </div>
    <div id="genmeta"></div>
    <div id="genresults"></div>
  </section>
  <section id="searchpanel" hidden>
    <div class="searchbar">
      <input id="q" type="search" autocomplete="off"
             placeholder="mélancolique 8 mesures ~90 bpm en Dm">
      <button class="primary" id="searchbtn">Chercher</button>
    </div>
    <div id="searchmeta"></div>
    <div id="results"></div>
  </section>
  <div id="drop">Glisse tes .mid ici (ou clique)
    <input id="file" type="file" accept=".mid,.midi" multiple hidden></div>
  <div id="status"></div>
  <div id="cards"></div>
  <footer>Libretto SMS — 100 % local. « ▶ Reaper » utilise le pont Klody sur 127.0.0.1:9000
  (REAPER doit être lancé). Génération avec <code>serve --generate</code>,
  recherche avec <code>serve --lib&nbsp;lib.json</code>.</footer>
</main>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const cards = document.getElementById('cards');
const statusEl = document.getElementById('status');
const searchPanel = document.getElementById('searchpanel');
const qInput = document.getElementById('q');
const searchBtn = document.getElementById('searchbtn');
const searchMeta = document.getElementById('searchmeta');
const results = document.getElementById('results');
const genPanel = document.getElementById('genpanel');
const genMode = document.getElementById('genmode');
const genBtn = document.getElementById('genbtn');
const genMeta = document.getElementById('genmeta');
const genResults = document.getElementById('genresults');
let hasLibrary = false;
let genModesFilled = false;

function setStatus(msg, isErr) {
  statusEl.innerHTML = msg ? `<span class="${isErr ? 'err' : ''}">${msg}</span>` : '';
}

function esc(s) {
  return String(s).replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function groupChips(groups, names) {
  return Object.entries(groups).map(([g, v]) =>
    `<span class="chip" title="${names[g]}"><b>${g}</b>${v.toFixed(2)}</span>`).join('');
}

function render(entries, names) {
  // Les fichiers non interprétables passent derrière, quel que soit leur
  // score : les comparer aux autres n'a pas de sens.
  const sorted = [...entries].sort((a, b) =>
    (b.interpretable - a.interpretable) || (b.global_score - a.global_score));
  cards.innerHTML = sorted.map(e => `
    <div class="card">
      <div class="head">
        <div class="gscore${e.interpretable ? '' : ' untrusted'}">${e.global_score.toFixed(2)}
          <small>score SMS</small>
          <small class="conflabel">fiab. ${e.confidence.toFixed(2)} · ${e.confidence_level}</small>
        </div>
        <div class="radar">${e.radar}</div>
        <div class="meta">
          <div class="fname">${e.name}</div>
          <div class="sub">tonalité ${e.key} · ${e.n_notes} notes ·
            ${e.sections.length} sections : ${e.sections.join(' · ')}</div>
          <div class="chips">${groupChips(e.groups, names)}</div>
        </div>
        <div class="actions">
          <button class="primary" onclick="toReaper(${e.id}, this)">▶ Reaper</button>
        </div>
      </div>
      ${e.interpretable ? '' : `<div class="banner"><b>Score non interprétable</b> — ${e.diagnosis}</div>`}
      <details><summary>les 29 axes</summary>
        <div class="axes-wrap"><table><tbody>${e.rows}</tbody></table></div>
      </details>
    </div>`).join('');
}

const MODE_LABELS = {procedural: 'procédural (make_corpus)', markov: 'appris (corpus)'};

async function refresh() {
  const r = await fetch('/api/analyses');
  const data = await r.json();
  hasLibrary = data.has_library;
  searchPanel.hidden = !data.has_library;
  genPanel.hidden = !data.can_generate;
  if (data.can_generate && !genModesFilled) {
    genMode.innerHTML = (data.gen_modes || []).map(m =>
      `<option value="${esc(m)}">${esc(MODE_LABELS[m] || m)}</option>`).join('');
    genModesFilled = true;
  }
  render(data.entries, data.group_names);
}

function renderGen(data) {
  const s = data.summary || {};
  genMeta.textContent = `${s.n_generated} généré(s) · ${s.n_eligible} éligible(s)`
    + (s.n_rejected ? ` · ${s.n_rejected} recalé(s) par le gate` : '')
    + ` — ${data.results.length} livré(s)`;
  genResults.innerHTML = data.results.map(g => {
    const win = g.role === 'winner';
    const conf = g.confidence != null ? g.confidence.toFixed(2) : '?';
    const meta = [g.form ? `forme ${g.form}` : null,
                  g.score != null ? `SMS ${g.score.toFixed(2)}` : null,
                  `fiab. ${esc(g.confidence_level || '?')} ${conf}`]
                 .filter(Boolean).join(' · ');
    const dl = `/api/download?path=${encodeURIComponent(g.path)}`;
    const addBtn = hasLibrary
      ? `<button data-add="${esc(g.path)}">+ Bibliothèque</button>` : '';
    return `<div class="gen">
      <span class="badge${win ? ' win' : ''}">${win ? '🏆 gagnant' : 'variante'}</span>
      <div class="body">
        <div class="title">${esc(g.name)}</div>
        <div class="why">${esc(meta)}</div>
      </div>
      <div class="acts">
        <button class="primary" data-genpath="${esc(g.path)}">▶ Reaper</button>
        <a class="btn" href="${dl}"><button>⬇ .mid</button></a>
        ${addBtn}
      </div>
    </div>`;
  }).join('');
}

async function doGenerate() {
  genBtn.disabled = true;
  const prev = genBtn.textContent; genBtn.textContent = 'génération…';
  genMeta.textContent = 'génération en cours (quelques secondes)…';
  const body = {
    mode: genMode.value,
    n: +document.getElementById('genn').value,
    shortlist: +document.getElementById('genshort').value,
    bars: +document.getElementById('genbars').value,
    seed: +document.getElementById('genseed').value,
  };
  try {
    const r = await fetch('/api/generate', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await r.json();
    if (!r.ok) { genMeta.innerHTML = `<span class="err">${esc(data.error)}</span>`;
      genResults.innerHTML = ''; }
    else renderGen(data);
  } finally { genBtn.disabled = false; genBtn.textContent = prev; }
}

async function genToReaper(path, btn) {
  btn.disabled = true; const t = btn.textContent; btn.textContent = '…';
  const r = await fetch('/api/reaper', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
  const data = await r.json();
  if (r.ok) { btn.textContent = '▶ joue'; setStatus(
    `Reaper : ${data.total_notes} notes sur ${data.tracks.length} pistes, lecture lancée`); }
  else { btn.textContent = t; setStatus(`Reaper : ${data.error}`, true); }
  btn.disabled = false;
}

async function genToLibrary(path, btn) {
  btn.disabled = true; const t = btn.textContent; btn.textContent = '…';
  const r = await fetch('/api/library_add', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
  const data = await r.json();
  if (r.ok) { btn.textContent = data.added ? '✓ indexé' : '✓ à jour';
    setStatus(`bibliothèque : ${data.name} — ${(data.descriptors || []).join(', ')}`); }
  else { btn.textContent = t; setStatus(`bibliothèque : ${data.error}`, true); btn.disabled = false; }
}

genBtn.addEventListener('click', doGenerate);
genResults.addEventListener('click', e => {
  const rb = e.target.closest('button[data-genpath]');
  if (rb) { genToReaper(rb.dataset.genpath, rb); return; }
  const ab = e.target.closest('button[data-add]');
  if (ab) genToLibrary(ab.dataset.add, ab);
});

function renderHits(data) {
  if (data.empty) {
    searchMeta.textContent =
      'bibliothèque vide — indexe des séquences (library add / forge_library).';
    results.innerHTML = ''; return;
  }
  searchMeta.textContent = `requête : ${data.query} — ${data.count} résultat(s)`;
  if (!data.count) {
    results.innerHTML = '<div class="hit"><div class="body why">aucune séquence '
      + 'ne satisfait les contraintes (élargir la tolérance ?)</div></div>';
    return;
  }
  results.innerHTML = data.entries.map((h, i) => {
    const dist = h.distance != null ? ` · d=${h.distance.toFixed(3)}` : '';
    const desc = h.descriptors.join(', ');
    const tags = (h.tags || []).map(t => `<span class="tagpill">#${esc(t)}</span>`).join('');
    const meta = [h.key || '?', (h.bpm ? Math.round(h.bpm) + ' BPM' : '? BPM'),
                  h.bars + ' mes.'].join(' · ');
    return `<div class="hit">
      <div class="rank">${i + 1}</div>
      <div class="body">
        <div class="title">${esc(h.name)}<span class="why"> — ${esc(meta)}</span></div>
        <div class="why">${esc(h.reasons.join('  ·  '))} · fiab. ${esc(h.confidence_level)}`
      + ` · SMS ${h.global_score.toFixed(2)}${dist}</div>
        <div class="emo">${desc ? `<span class="tagpill intent">${esc(desc)}</span>` : ''}${tags}</div>
      </div>
      <button class="primary" data-path="${esc(h.path)}">▶ Reaper</button>
    </div>`;
  }).join('');
}

async function doSearch() {
  const query = qInput.value.trim();
  if (!query) return;
  searchMeta.textContent = 'recherche…';
  const r = await fetch('/api/search', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query})});
  const data = await r.json();
  if (!r.ok) {
    searchMeta.innerHTML = `<span class="err">${esc(data.error)}</span>`;
    results.innerHTML = ''; return;
  }
  renderHits(data);
}

async function toReaperPath(path, btn) {
  btn.disabled = true; btn.textContent = '…';
  const r = await fetch('/api/reaper', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
  const data = await r.json();
  if (r.ok) { btn.textContent = '▶ joue'; setStatus(
    `Reaper : ${data.total_notes} notes sur ${data.tracks.length} pistes, lecture lancée`); }
  else { btn.textContent = '▶ Reaper'; setStatus(`Reaper : ${data.error}`, true); }
  btn.disabled = false;
}

searchBtn.addEventListener('click', doSearch);
qInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
results.addEventListener('click', e => {
  const btn = e.target.closest('button[data-path]');
  if (btn) toReaperPath(btn.dataset.path, btn);
});

async function upload(files) {
  for (const f of files) {
    setStatus(`analyse de ${f.name}…`);
    const r = await fetch('/api/analyze', {
      method: 'POST', headers: {'X-Filename': encodeURIComponent(f.name)},
      body: await f.arrayBuffer()});
    if (!r.ok) { setStatus(`${f.name} : ${(await r.json()).error}`, true); continue; }
    setStatus('');
  }
  refresh();
}

async function toReaper(id, btn) {
  btn.disabled = true; btn.textContent = '…';
  const r = await fetch('/api/reaper', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id})});
  const data = await r.json();
  if (r.ok) { btn.textContent = '▶ joue'; setStatus(
    `Reaper : ${data.total_notes} notes sur ${data.tracks.length} pistes, lecture lancée`); }
  else { btn.textContent = '▶ Reaper'; setStatus(`Reaper : ${data.error}`, true); }
  btn.disabled = false;
}

drop.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => upload([...fileInput.files]));
['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('hover'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('hover'); }));
drop.addEventListener('drop', e => upload([...e.dataTransfer.files]));
refresh();
</script>
</body>
</html>"""


def make_handler(store: _Store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LibrettoSMS/0.1"

        def log_message(self, fmt, *args):  # silencieux (pas de spam stdout)
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
            elif parsed.path == "/api/analyses":
                self._json(200, {"entries": store.snapshot(),
                                 "group_names": GROUP_NAMES,
                                 "has_library": store.lib_path is not None,
                                 "can_generate": store.generator is not None,
                                 "gen_modes": store.gen_modes})
            elif parsed.path == "/api/download":
                path = (parse_qs(parsed.query).get("path") or [""])[0]
                if not store.is_generated(path):
                    self._json(404, {"error": "fichier généré inconnu"})
                    return
                data = Path(path).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/midi")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{Path(path).name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(404, {"error": "introuvable"})

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD:
                raise ValueError(f"taille de corps invalide ({length} octets)")
            return self.rfile.read(length)

        def do_POST(self):
            try:
                if self.path == "/api/analyze":
                    from urllib.parse import unquote
                    name = unquote(self.headers.get("X-Filename") or "sans-nom.mid")
                    data = self._read_body()
                    entry, _sms = analyze_bytes(data, name)
                    self._json(200, store.add(entry, data))
                elif self.path == "/api/search":
                    if store.lib_path is None:
                        self._json(400, {"error": "aucune bibliothèque chargée "
                                                  "(lancer `serve --lib lib.json`)"})
                        return
                    req = json.loads(self._read_body() or b"{}")
                    query = (req.get("query") or "").strip()
                    if not query:
                        self._json(400, {"error": "requête vide"})
                        return
                    self._json(200, search_library(
                        store.lib_path, query,
                        limit=int(req.get("limit", 8)),
                        bpm_tol=float(req.get("bpm_tol", 15.0)),
                        bars_tol=int(req.get("bars_tol", 2))))
                elif self.path == "/api/generate":
                    if store.generator is None:
                        self._json(400, {"error": "génération désactivée "
                                                  "(lancer `serve --generate`)"})
                        return
                    req = json.loads(self._read_body() or b"{}")
                    workdir = store.new_workdir()
                    result = store.generator(req, workdir)
                    store.register_generated(g["path"] for g in result["results"])
                    self._json(200, result)
                elif self.path == "/api/library_add":
                    if store.lib_path is None:
                        self._json(400, {"error": "aucune bibliothèque chargée"})
                        return
                    req = json.loads(self._read_body() or b"{}")
                    path = str(req.get("path", ""))
                    if not store.is_generated(path):
                        self._json(400, {"error": "seul un fichier généré "
                                                  "peut être indexé depuis l'UI"})
                        return
                    self._json(200, add_generated_to_library(store.lib_path, path))
                elif self.path == "/api/reaper":
                    from .reaper import BridgeError, push_mididata
                    req = json.loads(self._read_body() or b"{}")
                    try:
                        if req.get("path"):     # généré, ou entrée de la bibliothèque
                            path = str(req["path"])
                            if store.is_generated(path):
                                self._json(200, push_generated_path(path))
                            elif store.lib_path is not None:
                                self._json(200, push_library_path(store.lib_path, path))
                            else:
                                self._json(404, {"error": "chemin non autorisé"})
                        else:                   # analyse déposée en mémoire
                            raw = store.raw_for(int(req.get("id", -1)))
                            if raw is None:
                                self._json(404, {"error": "analyse inconnue"})
                                return
                            from .midi import parse_midi_bytes as _parse
                            self._json(200, push_mididata(_parse(raw)))
                    except BridgeError as exc:
                        self._json(502, {"error": str(exc)})
                else:
                    self._json(404, {"error": "introuvable"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # garde-fou : l'UI affiche l'erreur
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


# 8765 est pris par le dashboard Library Brain sur cette machine, et le range
# 808x/809x par le gateway Klody — 8787 est hors de ces plages.
DEFAULT_PORT = 8787


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT,
          lib_path: str | None = None, generator=None,
          gen_modes: list[str] | None = None) -> ThreadingHTTPServer:
    """Crée le serveur (sans le lancer) — utilisé par la CLI et les tests."""
    store = _Store(lib_path=lib_path, generator=generator, gen_modes=gen_modes)
    return ThreadingHTTPServer((host, port), make_handler(store))


def main(host: str = "127.0.0.1", port: int | None = None,
         lib_path: str | None = None, generator=None,
         gen_modes: list[str] | None = None) -> int:
    import sys
    want = DEFAULT_PORT if port is None else port
    kw = {"lib_path": lib_path, "generator": generator, "gen_modes": gen_modes}
    try:
        httpd = serve(host, want, **kw)
    except OSError as exc:
        if port is not None:
            print(f"libretto: port {want} déjà occupé ({exc.strerror}) — un autre service "
                  f"écoute dessus ; choisis un autre port (--port 0 = automatique)",
                  file=sys.stderr)
            return 1
        print(f"port {want} occupé — bascule sur un port libre", file=sys.stderr)
        httpd = serve(host, 0, **kw)
    real_port = httpd.server_address[1]
    if generator:
        print(f"génération active : {', '.join(gen_modes or [])}", file=sys.stderr)
    if lib_path:
        print(f"bibliothèque cherchable : {lib_path}", file=sys.stderr)
    print(f"Libretto SMS — interface sur http://{host}:{real_port} (Ctrl-C pour quitter)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
