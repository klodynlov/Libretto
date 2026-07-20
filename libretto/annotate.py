"""
Libretto — annotation humaine par comparaison A/B en aveugle.

Pourquoi
--------
Tout ce que Libretto sait de lui-même vient d'une hypothèse jamais vérifiée :
qu'un morceau dégradé (mesures permutées, segments transposés, attaques
décalées) est *réellement* moins bien structuré. La calibration, les 29 axes,
les seuils — tout en découle. Si l'oreille humaine ne perçoit pas ces
dégradations, l'édifice mesure sa propre cohérence interne et rien d'autre.

Ce module ne produit pas d'annotations : il produit les conditions pour en
recueillir. Il présente des paires, l'une originale et l'autre dégradée,
**dans un ordre aléatoire et sans dire laquelle est laquelle**, et enregistre
le choix. `libretto agreement` compare ensuite ces jugements à ceux du
moteur.

Protocole
---------
- **Aveugle et équilibré** : l'ordre de présentation est tiré au sort, donc
  l'original se trouve en A dans la moitié des cas environ.
- **Paires de contrôle** : une fraction des paires oppose un morceau à
  lui-même. Un annotateur qui répond au hasard, ou qui croit percevoir une
  différence partout, les tranchera comme les autres — ces paires mesurent
  le bruit de réponse, et sans elles un taux de détection ne veut rien dire.
- **« Aucune différence » est une réponse** : forcer un choix binaire
  fabrique de l'accord artificiel.
- **Ordre de passage aléatoire**, pour que la fatigue ne se concentre pas
  sur un type de dégradation.
- Rien n'est chronométré ni imposé : l'annotateur écoute autant qu'il veut.

Lecture : synthèse Web Audio dans le navigateur (ondes simples, bruit filtré
pour les percussions). Le rendu est rudimentaire — c'est voulu, on juge la
structure, pas le timbre — et surtout il n'ajoute aucune dépendance.

Usage : libretto annotate corpus/ --out jugements.json
"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .calibrate import DEGRADATIONS, _applicable
from .midi import MidiData, parse_midi

DRUM_CHANNEL = 9
# Part de paires identiques glissées dans le lot : elles ne mesurent pas la
# perception mais le bruit de réponse de l'annotateur.
CONTROL_RATIO = 0.15
MAX_NOTES = 4000          # au-delà, le navigateur peine à programmer les voix


def notes_in_seconds(md: MidiData) -> list[list]:
    """[start_s, dur_s, pitch, velocity, is_drum] — le tempo (et ses
    changements) est résolu ici, le navigateur n'a plus qu'à jouer."""
    tempos = sorted(md.tempos) or [(0, 120.0)]
    ppq = md.ppq or 480

    def seconds_at(tick: int) -> float:
        total = 0.0
        prev_tick, prev_bpm = 0, tempos[0][1]
        for t, bpm in tempos:
            if t >= tick:
                break
            total += (t - prev_tick) / ppq * (60.0 / prev_bpm)
            prev_tick, prev_bpm = t, bpm
        total += (tick - prev_tick) / ppq * (60.0 / prev_bpm)
        return total

    out = []
    for n in sorted(md.notes, key=lambda x: x.start)[:MAX_NOTES]:
        start = seconds_at(n.start)
        end = seconds_at(n.end)
        out.append([round(start, 4), round(max(0.05, end - start), 4),
                    n.pitch, n.velocity, 1 if n.channel == DRUM_CHANNEL else 0])
    return out


def build_tasks(corpus_dir: str | Path, seed: int = 1,
                per_file: int = 2) -> list[dict]:
    """Prépare le lot de comparaisons. Chaque tâche porte le nom de la
    dégradation et la position de l'original — que le client ne reçoit
    jamais.

    Les dégradations sont réparties **équitablement** entre les fichiers, et
    non tirées au sort indépendamment pour chacun : le tirage indépendant
    produit des lots où une dégradation sort onze fois et une autre trois,
    et l'on ne peut alors rien conclure sur les moins représentées. À chaque
    fichier on prend celles qui ont le moins servi jusque-là.

    La position de l'original est alternée puis mélangée, ce qui garantit un
    équilibre exact plutôt qu'approximatif — un déséquilibre se repère à la
    longue sans rien entendre."""
    rng = random.Random(seed)
    paths = sorted(p for p in Path(corpus_dir).rglob("*.mid*") if p.is_file())
    tasks: list[dict] = []
    used: dict[str, int] = {name: 0 for name in DEGRADATIONS}
    for path in paths:
        try:
            md = parse_midi(path)
        except (ValueError, OSError):
            continue
        if not md.notes:
            continue
        usable = [name for name in sorted(DEGRADATIONS) if _applicable(name, md)]
        if not usable:
            continue
        # les moins servies d'abord ; le tirage ne départage que les ex aequo
        rng.shuffle(usable)
        usable.sort(key=lambda name: used[name])
        for name in usable[:per_file]:
            used[name] += 1
            tasks.append({"file": path.name, "path": str(path),
                          "degradation": name})
        if rng.random() < CONTROL_RATIO:
            tasks.append({"file": path.name, "path": str(path),
                          "degradation": "__control__"})
    rng.shuffle(tasks)
    slots = ["A", "B"] * ((len(tasks) + 1) // 2)
    rng.shuffle(slots)
    for i, task in enumerate(tasks):
        task["id"] = i
        task["original_slot"] = slots[i]
    return tasks


def render_task(task: dict, seed: int = 1) -> dict:
    """Les deux versions audibles, sans indiquer laquelle est l'originale."""
    md = parse_midi(task["path"])
    original = notes_in_seconds(md)
    if task["degradation"] == "__control__":
        other = original
    else:
        rng = random.Random(f"{seed}:{task['file']}:{task['degradation']}")
        other = notes_in_seconds(DEGRADATIONS[task["degradation"]](md, rng))
    a, b = ((original, other) if task["original_slot"] == "A"
            else (other, original))
    return {"id": task["id"], "n_total": task.get("n_total", 0),
            "A": a, "B": b}


class _Judgements:
    def __init__(self, out_path: Path, tasks: list[dict], seed: int):
        self.lock = threading.Lock()
        self.out_path = out_path
        self.tasks = tasks
        self.seed = seed
        self.records: list[dict] = []
        if out_path.exists():
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
                self.records = data.get("judgements", [])
            except (ValueError, OSError):
                self.records = []

    def done_ids(self) -> set[int]:
        return {r["task_id"] for r in self.records}

    def next_task(self) -> dict | None:
        done = self.done_ids()
        for task in self.tasks:
            if task["id"] not in done:
                return task
        return None

    def record(self, task_id: int, choice: str, listened: float) -> None:
        task = next((t for t in self.tasks if t["id"] == task_id), None)
        if task is None:
            raise ValueError(f"tâche inconnue : {task_id}")
        with self.lock:
            self.records = [r for r in self.records if r["task_id"] != task_id]
            self.records.append({
                "task_id": task_id,
                "file": task["file"],
                "degradation": task["degradation"],
                "original_slot": task["original_slot"],
                "choice": choice,                    # "A" | "B" | "same"
                # Le dépouillement ne lit que ce champ : l'annotateur a-t-il
                # désigné l'original ?
                "picked_original": (choice == task["original_slot"]
                                    if choice in ("A", "B") else None),
                "listened_seconds": round(listened, 1),
            })
            self._flush()

    def _flush(self) -> None:
        payload = {"seed": self.seed, "n_tasks": len(self.tasks),
                   "judgements": sorted(self.records, key=lambda r: r["task_id"])}
        self.out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Libretto — écoute comparée</title>
<style>
:root { --bg:#faf9f6; --fg:#1a1a1e; --muted:#6b6b74; --card:#fff;
  --border:#e4e2db; --accent:#7c5cff; }
@media (prefers-color-scheme:dark){ :root{ --bg:#14141a; --fg:#ecebf2;
  --muted:#9a99a6; --card:#1d1d26; --border:#2b2b36; --accent:#9d85ff; } }
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);padding:36px 20px;
  font:15px/1.55 -apple-system,"SF Pro Text",Segoe UI,sans-serif}
main{max-width:720px;margin:0 auto}
h1{font-size:22px;letter-spacing:-.02em}
h1 small{display:block;color:var(--muted);font-size:14px;font-weight:500;margin-top:4px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:22px;margin:20px 0}
.pair{display:flex;gap:14px;margin:18px 0}
.side{flex:1;text-align:center;border:1px solid var(--border);border-radius:14px;padding:18px}
.side h2{font-size:34px;margin-bottom:10px}
button{font:inherit;border:1px solid var(--border);background:var(--card);
  color:var(--fg);border-radius:10px;padding:9px 16px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.5;cursor:default}
.choices{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
.choices button{flex:1;min-width:130px;padding:13px}
.bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin:14px 0}
.bar div{height:100%;background:var(--accent)}
.muted{color:var(--muted);font-size:13px}
kbd{border:1px solid var(--border);border-radius:5px;padding:1px 6px;font-size:12px}
</style></head><body><main>
<h1>Écoute comparée<small>Deux versions du même morceau. Laquelle est la mieux
structurée&nbsp;? Aucune indication n'est donnée sur leur origine.</small></h1>
<div class="card">
  <div class="bar"><div id="prog" style="width:0%"></div></div>
  <div class="muted" id="count">chargement…</div>
  <div class="pair">
    <div class="side"><h2>A</h2><button onclick="play('A')" id="pa">▶ écouter A</button></div>
    <div class="side"><h2>B</h2><button onclick="play('B')" id="pb">▶ écouter B</button></div>
  </div>
  <button onclick="stopAll()">■ stop</button>
  <div class="choices">
    <button class="primary" onclick="judge('A')">A est mieux structuré</button>
    <button class="primary" onclick="judge('B')">B est mieux structuré</button>
    <button onclick="judge('same')">Je n'entends pas de différence</button>
  </div>
  <p class="muted" style="margin-top:14px">Raccourcis : <kbd>a</kbd> <kbd>b</kbd>
  écouter · <kbd>1</kbd> <kbd>2</kbd> <kbd>0</kbd> répondre · <kbd>s</kbd> stop.
  Répondre « je n'entends pas de différence » est une réponse valide et utile.</p>
</div>
<p class="muted" id="status"></p>
</main><script>
let task=null, ctx=null, playing=[], listened=0, tStart=0;
function ac(){ if(!ctx) ctx=new (window.AudioContext||window.webkitAudioContext)(); return ctx; }
function noiseBuf(c){ const b=c.createBuffer(1, c.sampleRate*0.2, c.sampleRate);
  const d=b.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1; return b; }
function stopAll(){ playing.forEach(n=>{try{n.stop()}catch(e){}}); playing=[];
  if(tStart){ listened+=(performance.now()-tStart)/1000; tStart=0; } }
function play(side){
  stopAll(); const c=ac(); const t0=c.currentTime+0.08; tStart=performance.now();
  const nb=noiseBuf(c);
  for(const [st,du,pi,ve,drum] of task[side]){
    const t=t0+st, g=c.createGain(), amp=Math.min(.22, ve/127*.22);
    if(drum){ const s=c.createBufferSource(); s.buffer=nb;
      const f=c.createBiquadFilter(); f.type = pi<=41?'lowpass':'highpass';
      f.frequency.value = pi<=41?220:4000;
      g.gain.setValueAtTime(amp,t); g.gain.exponentialRampToValueAtTime(.0008,t+.14);
      s.connect(f); f.connect(g); g.connect(c.destination); s.start(t); s.stop(t+.2);
      playing.push(s);
    } else {
      const o=c.createOscillator(); o.type='triangle';
      o.frequency.value=440*Math.pow(2,(pi-69)/12);
      g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(amp,t+.012);
      g.gain.exponentialRampToValueAtTime(.0008,t+Math.max(.12,du));
      o.connect(g); g.connect(c.destination); o.start(t); o.stop(t+du+.06);
      playing.push(o);
    }
  }
}
async function load(){
  const r=await fetch('/api/task'); const d=await r.json();
  if(d.done){ document.querySelector('.card').innerHTML=
    '<b>Lot terminé.</b> Dépouillez avec <code>libretto agreement</code>.'; return; }
  task=d; listened=0; tStart=0;
  document.getElementById('count').textContent=
    `paire ${d.n_done+1} / ${d.n_total}`;
  document.getElementById('prog').style.width=(100*d.n_done/d.n_total)+'%';
}
async function judge(choice){
  stopAll();
  await fetch('/api/judge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:task.id, choice, listened})});
  document.getElementById('status').textContent='enregistré';
  await load();
}
addEventListener('keydown',e=>{ if(!task) return;
  if(e.key==='a') play('A'); else if(e.key==='b') play('B');
  else if(e.key==='s') stopAll();
  else if(e.key==='1') judge('A'); else if(e.key==='2') judge('B');
  else if(e.key==='0') judge('same'); });
load();
</script></body></html>"""


def _handler(store: _Judgements, seed: int):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/task":
                task = store.next_task()
                if task is None:
                    self._json(200, {"done": True})
                    return
                payload = render_task(task, seed)
                payload["n_total"] = len(store.tasks)
                payload["n_done"] = len(store.done_ids())
                self._json(200, payload)
            else:
                self._json(404, {"error": "inconnu"})

        def do_POST(self):
            if self.path != "/api/judge":
                self._json(404, {"error": "inconnu"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                store.record(int(data["id"]), str(data["choice"]),
                             float(data.get("listened") or 0.0))
            except (ValueError, KeyError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"ok": True, "n_done": len(store.done_ids())})

    return Handler


def main(corpus_dir: str, out: str, host: str = "127.0.0.1",
         port: int | None = None, seed: int = 1, per_file: int = 2) -> int:
    tasks = build_tasks(corpus_dir, seed=seed, per_file=per_file)
    if not tasks:
        print(f"libretto: aucun MIDI exploitable dans {corpus_dir}")
        return 1
    store = _Judgements(Path(out), tasks, seed)
    handler = _handler(store, seed)
    chosen = port if port is not None else 8788
    for candidate in ([chosen] if port is not None else [chosen, 0]):
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            break
        except OSError:
            continue
    else:
        print(f"libretto: impossible d'ouvrir un port sur {host}")
        return 1
    real = httpd.server_address[1]
    n_control = sum(1 for t in tasks if t["degradation"] == "__control__")
    print(f"{len(tasks)} comparaisons ({n_control} paires de contrôle identiques)")
    print(f"déjà jugées : {len(store.done_ids())}   →  jugements dans {out}")
    print(f"écoute comparée : http://{host}:{real}   (Ctrl-C pour arrêter)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ninterrompu — les jugements sont enregistrés au fur et à mesure")
    return 0
