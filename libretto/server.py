"""
Libretto — interface web locale (stdlib pure, aucun framework).

`libretto serve` puis http://127.0.0.1:8787 (bascule auto sur un port libre
si occupé — 8765 appartient au dashboard Library Brain sur cette machine) :
- glisser-déposer des .mid → analyse SMS complète (radar + 29 axes) ;
- comparateur : les analyses de la session s'empilent, triées par score ;
- bouton « ▶ Reaper » : pousse le fichier dans REAPER via le pont Klody
  (:9000) et lance la lecture.

API : GET /api/analyses · POST /api/analyze (corps = octets MIDI,
en-tête X-Filename) · POST /api/reaper {"id": N}. Tout reste en mémoire,
rien n'est écrit sur disque.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .axes import GROUP_NAMES, SenseOfMusicalStructure
from .builder import build_score
from .midi import parse_midi_bytes
from .model import PC_NAMES
from .report import _radar_svg

MAX_UPLOAD = 8 * 1024 * 1024  # 8 Mio, très large pour du MIDI


class _Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries: list[dict] = []
        self.raw: dict[int, bytes] = {}
        self.next_id = 1

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


def analyze_bytes(data: bytes, filename: str) -> tuple[dict, "SenseOfMusicalStructure"]:
    md = parse_midi_bytes(data, origin=filename)
    score = build_score(md)
    if not score.sections:
        raise ValueError(f"{filename} : aucune note exploitable")
    sms = SenseOfMusicalStructure(score)
    sms.calculate()
    payload = sms.to_dict()
    rows = "".join(
        f'<tr><td class="mono">{a["id"]}</td><td>{a["name"]}</td>'
        f'<td class="mono">{a["group"]}</td>'
        f'<td><div class="bar"><div class="fill" style="width:{round(a["score"] * 100)}%">'
        f'</div></div></td><td class="mono score">{a["score"]:.2f}</td></tr>'
        for a in payload["axes"])
    entry = {
        "name": filename,
        "global_score": payload["global_score"],
        "groups": payload["groups"],
        "key": f"{PC_NAMES[score.key_signature.pc]}",
        "sections": [s.label for s in score.sections],
        "n_notes": len(md.notes),
        "radar": _radar_svg(payload["groups"], size=240),
        "rows": rows,
    }
    return entry, sms


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
.radar svg { display:block; }
footer { color:var(--muted); font-size:12.5px; margin-top:26px; }
</style>
</head>
<body>
<main>
  <h1>Libretto — Sense of Musical Structure
    <small>dépose des fichiers .mid : analyse 29 axes, comparaison, écoute Reaper</small></h1>
  <div id="drop">Glisse tes .mid ici (ou clique)
    <input id="file" type="file" accept=".mid,.midi" multiple hidden></div>
  <div id="status"></div>
  <div id="cards"></div>
  <footer>Libretto SMS — 100 % local. « ▶ Reaper » utilise le pont Klody sur 127.0.0.1:9000
  (REAPER doit être lancé).</footer>
</main>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const cards = document.getElementById('cards');
const statusEl = document.getElementById('status');

function setStatus(msg, isErr) {
  statusEl.innerHTML = msg ? `<span class="${isErr ? 'err' : ''}">${msg}</span>` : '';
}

function groupChips(groups, names) {
  return Object.entries(groups).map(([g, v]) =>
    `<span class="chip" title="${names[g]}"><b>${g}</b>${v.toFixed(2)}</span>`).join('');
}

function render(entries, names) {
  const sorted = [...entries].sort((a, b) => b.global_score - a.global_score);
  cards.innerHTML = sorted.map(e => `
    <div class="card">
      <div class="head">
        <div class="gscore">${e.global_score.toFixed(2)}<small>score SMS</small></div>
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
      <details><summary>les 29 axes</summary>
        <div class="axes-wrap"><table><tbody>${e.rows}</tbody></table></div>
      </details>
    </div>`).join('');
}

async function refresh() {
  const r = await fetch('/api/analyses');
  const data = await r.json();
  render(data.entries, data.group_names);
}

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
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/analyses":
                self._json(200, {"entries": store.snapshot(), "group_names": GROUP_NAMES})
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
                elif self.path == "/api/reaper":
                    req = json.loads(self._read_body() or b"{}")
                    raw = store.raw_for(int(req.get("id", -1)))
                    if raw is None:
                        self._json(404, {"error": "analyse inconnue"})
                        return
                    from .midi import parse_midi_bytes as _parse
                    from .reaper import BridgeError, push_mididata
                    try:
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


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Crée le serveur (sans le lancer) — utilisé par la CLI et les tests."""
    store = _Store()
    return ThreadingHTTPServer((host, port), make_handler(store))


def main(host: str = "127.0.0.1", port: int | None = None) -> int:
    import sys
    want = DEFAULT_PORT if port is None else port
    try:
        httpd = serve(host, want)
    except OSError as exc:
        if port is not None:
            print(f"libretto: port {want} déjà occupé ({exc.strerror}) — un autre service "
                  f"écoute dessus ; choisis un autre port (--port 0 = automatique)",
                  file=sys.stderr)
            return 1
        print(f"port {want} occupé — bascule sur un port libre", file=sys.stderr)
        httpd = serve(host, 0)
    real_port = httpd.server_address[1]
    print(f"Libretto SMS — interface sur http://{host}:{real_port} (Ctrl-C pour quitter)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
