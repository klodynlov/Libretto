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

Lecture : synthèse Web Audio dans le navigateur, sans aucune dépendance.
Le rendu initial (v1-volume) mappait la vélocité sur le seul gain — et les
sessions 1-2 ont montré qu'ainsi rendue, la dynamique ne porte AUCUNE
structure perceptible : l'argument « on juge la structure, pas le timbre »
était faux, le timbre est précisément le canal par lequel la dynamique
s'entend. Le rendu v2-timbre la fait donc porter par une synthèse
soustractive : vélocité -> coupure du filtre (en v²), durée d'attaque et
gain, pour les voix comme pour les percussions. Chaque fichier de jugements
enregistre la version du rendu qui l'a produit.

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

# Version du rendu audio, inscrite dans chaque fichier de jugements. Deux
# rendus différents sont deux expériences différentes : les sessions 1-2
# (v1-volume, où la vélocité ne modulait que le gain) ont conclu que la
# dynamique ne s'entend pas — conclusion qui ne vaut QUE pour ce rendu.
# Mélanger dans un même fichier des jugements issus de rendus distincts les
# rendrait ininterprétables ; la reprise d'une session le refuse donc.
RENDERER = "v2-timbre"
# Rendu instrumental (annotate --render instrument) : FluidSynth + SoundFont,
# la vélocité déclenche de vraies couches d'échantillons. Dernier étage du
# banc d'essai pour la question dynamique.
RENDERER_INSTRUMENT = "v3-instrument"


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


def build_tasks(corpus_dir: str | Path, seed: int = 1, per_file: int = 2,
                only: list[str] | None = None) -> list[dict]:
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
    longue sans rien entendre.

    `only` restreint le lot à certaines dégradations : une session ciblée sur
    celles qui restent à trancher vaut mieux qu'un lot général où chacune
    n'obtient que quelques jugements."""
    rng = random.Random(seed)
    paths = sorted(p for p in Path(corpus_dir).rglob("*.mid*") if p.is_file())
    tasks: list[dict] = []
    pool: list[Path] = []
    used: dict[str, int] = {name: 0 for name in DEGRADATIONS}
    for path in paths:
        try:
            md = parse_midi(path)
        except (ValueError, OSError):
            continue
        if not md.notes:
            continue
        usable = [name for name in sorted(DEGRADATIONS) if _applicable(name, md)]
        if only:
            usable = [name for name in usable if name in only]
        if not usable:
            continue
        # les moins servies d'abord ; le tirage ne départage que les ex aequo
        rng.shuffle(usable)
        usable.sort(key=lambda name: used[name])
        for name in usable[:per_file]:
            used[name] += 1
            tasks.append({"file": path.name, "path": str(path),
                          "degradation": name})
        pool.append(path)

    # Nombre de contrôles GARANTI, et non tiré au sort fichier par fichier :
    # ce tirage pouvait n'en produire qu'un ou deux sur un lot entier, et
    # sans eux le bruit de réponse de l'annotateur n'est pas estimable — donc
    # aucun taux de détection n'est interprétable. Plancher à 4, parce qu'en
    # dessous la proportion observée ne veut rien dire non plus.
    if pool:
        n_control = max(4, round(CONTROL_RATIO * len(tasks)))
        for i in range(n_control):
            path = pool[(i * 7 + 3) % len(pool)]     # étalé sur le corpus
            tasks.append({"file": path.name, "path": str(path),
                          "degradation": "__control__"})
    rng.shuffle(tasks)
    # Répartition POSITIONNELLE des contrôles garantie, et non laissée au
    # mélange : en session 5, un mélange malchanceux a groupé 6 contrôles
    # sur 7 au-delà de la 30e tâche — l'annotateur qui s'arrête à mi-lot
    # n'en a croisé qu'un, et son bruit de réponse n'était pas estimable.
    # Chaque contrôle est réinséré dans sa tranche du lot, à une position
    # tirée DANS la tranche : tout préfixe en contient sa juste part, sans
    # que les positions soient prévisibles (un contrôle tous les k
    # exactement s'apprendrait, et l'annotateur saurait répondre « aucune
    # différence » sans écouter).
    controls = [t for t in tasks if t["degradation"] == "__control__"]
    others = [t for t in tasks if t["degradation"] != "__control__"]
    if controls:
        tasks = list(others)
        step = (len(others) + len(controls)) / len(controls)
        for k, ctrl in enumerate(controls):
            pos = round((k + rng.uniform(0.2, 0.8)) * step)
            tasks.insert(min(len(tasks), pos), ctrl)
    slots = ["A", "B"] * ((len(tasks) + 1) // 2)
    rng.shuffle(slots)
    for i, task in enumerate(tasks):
        task["id"] = i
        task["original_slot"] = slots[i]
    return tasks


def build_duel_tasks(duels_path: str | Path, seed: int = 1) -> list[dict]:
    """Lot de **duels** : deux morceaux DIFFÉRENTS, pas un morceau et sa
    version dégradée.

    Le protocole d'écoute a été construit pour valider les dégradations ;
    il vaut tel quel pour une autre question, plus directe — *le classement
    de Libretto est-il celui de l'oreille ?* On oppose deux candidats que le
    moteur sépare, et le côté « original » devient celui qu'il place devant.
    Tout le reste est inchangé et c'est le but : ordre tiré au sort, position
    équilibrée, paires de contrôle, « aucune différence » toujours possible.

    Le fichier attendu (voir `examples/forge_duels.py`) :
    `{"duels": [{"prefere": chemin, "autre": chemin, "etiquette": nom,
                 "nom": libellé, "ecart": 0.12}, …]}` — `etiquette` sert de
    clé de dépouillement, exactement comme un nom de dégradation : la
    séparer par tranche d'écart répond à « à partir de quel écart de score
    l'oreille suit-elle ? », qui est la vraie question.
    """
    data = json.loads(Path(duels_path).read_text(encoding="utf-8"))
    rng = random.Random(seed)
    tasks: list[dict] = []
    pool: list[str] = []
    for d in data["duels"]:
        if not (Path(d["prefere"]).exists() and Path(d["autre"]).exists()):
            continue
        tasks.append({"file": d.get("nom", Path(d["prefere"]).name),
                      "path": d["prefere"], "path_autre": d["autre"],
                      "degradation": d.get("etiquette", "duel"),
                      "ecart": d.get("ecart")})
        pool.append(d["prefere"])
    if not tasks:
        return []
    # Mêmes contrôles que pour les dégradations : sans eux, un taux de
    # détection élevé peut n'être qu'un biais de réponse.
    n_control = max(4, round(CONTROL_RATIO * len(tasks)))
    for i in range(n_control):
        path = pool[(i * 7 + 3) % len(pool)]
        tasks.append({"file": Path(path).name, "path": path,
                      "degradation": "__control__"})
    rng.shuffle(tasks)
    slots = ["A", "B"] * ((len(tasks) + 1) // 2)
    rng.shuffle(slots)
    for i, task in enumerate(tasks):
        task["id"] = i
        task["original_slot"] = slots[i]
    return tasks


def midi_pair(task: dict, seed: int = 1):
    """(MidiData du côté A, MidiData du côté B) — la position de l'original
    est déjà résolue, l'appelant n'a aucun moyen de la connaître."""
    md = parse_midi(task["path"])
    if task["degradation"] == "__control__":
        other = md
    elif task.get("path_autre"):
        other = parse_midi(task["path_autre"])
    else:
        rng = random.Random(f"{seed}:{task['file']}:{task['degradation']}")
        other = DEGRADATIONS[task["degradation"]](md, rng)
    return (md, other) if task["original_slot"] == "A" else (other, md)


def render_task(task: dict, seed: int = 1) -> dict:
    """Les deux versions audibles, sans indiquer laquelle est l'originale."""
    side_a, side_b = midi_pair(task, seed)
    return {"id": task["id"], "n_total": task.get("n_total", 0),
            "A": notes_in_seconds(side_a), "B": notes_in_seconds(side_b)}


def task_key(task: dict, rang: int = 0) -> str:
    """Identité **stable** d'une comparaison, indépendante de sa position.

    La reprise s'est longtemps faite sur `task_id`, c'est-à-dire sur le rang
    dans le lot. Tant qu'on rejoue le même lot, les deux coïncident ; dès
    qu'on l'agrandit — passer de 14 à 60 duels pour obtenir la puissance
    qui manquait — tous les rangs glissent, et les jugements déjà rendus se
    retrouvent collés à d'autres comparaisons. Silencieusement : rien ne
    plante, le fichier reste valide, les chiffres sont faux.

    La clé nomme donc la comparaison elle-même. `rang` ne sert qu'aux
    contrôles, qui répètent le même fichier plusieurs fois dans un lot.
    """
    if task["degradation"] == "__control__":
        return f"{task['file']}|__control__|{rang}"
    # Hors contrôle, (fichier, dégradation) est unique dans un lot — et pour
    # un duel, `file` est le nom de la paire, donc la paire elle-même.
    return f"{task['file']}|{task['degradation']}"


def _stamp_keys(tasks: list[dict]) -> None:
    """Pose `cle` sur chaque tâche, en numérotant les contrôles répétés."""
    vus: dict[str, int] = {}
    for task in tasks:
        base = task_key(task)
        rang = vus.get(base, 0)
        vus[base] = rang + 1
        task["cle"] = task_key(task, rang)


class _Judgements:
    def __init__(self, out_path: Path, tasks: list[dict], seed: int,
                 renderer: str = RENDERER):
        self.lock = threading.Lock()
        self.out_path = out_path
        self.tasks = tasks
        self.seed = seed
        self.renderer = renderer
        self.records: list[dict] = []
        if out_path.exists():
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
            # Les fichiers d'avant le versionnage du rendu sont v1-volume.
            existing = data.get("renderer", "v1-volume")
            if data.get("judgements") and existing != renderer:
                raise ValueError(
                    f"{out_path} contient des jugements du rendu « {existing} », "
                    f"le rendu actuel est « {renderer} ». Les mélanger rendrait la "
                    "session ininterprétable — reprenez avec un nouveau --out.")
            self.records = data.get("judgements", [])
        _stamp_keys(self.tasks)
        # Fichiers d'avant les clés : on la reconstruit du contenu du
        # jugement, dans l'ordre où les contrôles apparaissent — c'est
        # exactement ce que `_stamp_keys` vient de faire sur les tâches.
        vus: dict[str, int] = {}
        for r in sorted(self.records, key=lambda r: r["task_id"]):
            if "cle" in r:
                continue
            base = task_key(r)
            rang = vus.get(base, 0)
            vus[base] = rang + 1
            r["cle"] = task_key(r, rang)
        # Un jugement repris garde sa clé mais reçoit le rang qu'il occupe
        # dans CE lot : un fichier où `task_id` désigne autre chose que la
        # comparaison jugée est un piège pour le prochain lecteur. Ceux qui
        # ne concernent aucune tâche du lot courant sont conservés tels
        # quels — ils restent des jugements valides, cumulables.
        rangs = {t["cle"]: t["id"] for t in self.tasks}
        for r in self.records:
            if r["cle"] in rangs:
                r["task_id"] = rangs[r["cle"]]

    def done_keys(self) -> set[str]:
        return {r["cle"] for r in self.records}

    def next_task(self) -> dict | None:
        done = self.done_keys()
        for task in self.tasks:
            if task["cle"] not in done:
                return task
        return None

    def record(self, task_id: int, choice: str, listened: float) -> None:
        task = next((t for t in self.tasks if t["id"] == task_id), None)
        if task is None:
            raise ValueError(f"tâche inconnue : {task_id}")
        with self.lock:
            self.records = [r for r in self.records if r["cle"] != task["cle"]]
            self.records.append({
                "task_id": task_id,
                "cle": task["cle"],
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
                   "renderer": self.renderer,
                   "judgements": sorted(self.records, key=lambda r: (r["task_id"], r["cle"]))}
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
  Répondre « je n'entends pas de différence » est une réponse valide et utile.<br>
  Rendu <b>__RENDERER__</b> — la vélocité module timbre, attaque et volume.</p>
</div>
<p class="muted" id="status"></p>
</main><script>
let task=null, ctx=null, playing=[], listened=0, tStart=0;
function ac(){ if(!ctx) ctx=new (window.AudioContext||window.webkitAudioContext)(); return ctx; }
function noiseBuf(c){ const b=c.createBuffer(1, c.sampleRate*0.2, c.sampleRate);
  const d=b.getChannelData(0); for(let i=0;i<d.length;i++) d[i]=Math.random()*2-1; return b; }
function stopAll(){
  playing.forEach(n=>{try{n.stop()}catch(e){} try{n.pause()}catch(e){}});
  playing=[];
  if(tStart){ listened+=(performance.now()-tStart)/1000; tStart=0; } }
// Rendu __RENDERER__ : la vélocité module le TIMBRE, pas seulement le volume.
// Les sessions 1-2 ont montré qu'en pur volume, aucune structure dynamique
// n'est perceptible — sur un vrai instrument, frapper fort rend aussi le son
// plus brillant (filtre) et plus mordant (attaque). Synthèse soustractive :
// dent de scie -> passe-bas dont la coupure suit v², attaque en 4-32 ms.
// `schedule` est séparée de `play` pour être testable sur un AnalyserNode.
function schedule(c, dest, notes, t0){
  const nb=noiseBuf(c), nodes=[];
  for(const [st,du,pi,ve,drum] of notes){
    const t=t0+st, v=ve/127, g=c.createGain();
    if(drum){
      const s=c.createBufferSource(); s.buffer=nb;
      const f=c.createBiquadFilter(); const low = pi<=41;
      f.type = low?'lowpass':'highpass';
      f.frequency.value = low ? 120+260*v : 2000+7000*v;   // fort = brillant
      const dec=.06+.16*v;
      g.gain.setValueAtTime(.04+.20*v, t);
      g.gain.exponentialRampToValueAtTime(.0006, t+dec);
      s.connect(f); f.connect(g); g.connect(dest);
      s.start(t); s.stop(t+dec+.05); nodes.push(s);
    } else {
      const f0=440*Math.pow(2,(pi-69)/12);
      const o=c.createOscillator(); o.type='sawtooth'; o.frequency.value=f0;
      const flt=c.createBiquadFilter(); flt.type='lowpass'; flt.Q.value=1.1;
      const bright=Math.min(11000, f0*(1.6+10*v*v));       // coupure en v²
      flt.frequency.setValueAtTime(bright, t);
      flt.frequency.exponentialRampToValueAtTime(
        Math.max(f0*1.4, bright*.4), t+Math.max(.18, du*.8));
      const atk=.004+.028*(1-v);                           // doux = attaque lente
      g.gain.setValueAtTime(0,t);
      g.gain.linearRampToValueAtTime(.028+.16*Math.pow(v,1.2), t+atk);
      g.gain.exponentialRampToValueAtTime(.0006, t+Math.max(.14,du));
      o.connect(flt); flt.connect(g); g.connect(dest);
      o.start(t); o.stop(t+du+.08); nodes.push(o);
    }
  }
  return nodes;
}
function play(side){
  stopAll(); tStart=performance.now();
  if(task.wav){
    const au=new Audio(task.wav[side]); au.play(); playing=[au]; return;
  }
  const c=ac();
  playing=schedule(c, c.destination, task[side], c.currentTime+0.08);
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


class _WavCache:
    """Rendus WAV par (tâche, côté), calculés à la demande et gardés sur
    disque le temps de la session. Le nom de fichier ne contient que l'id de
    tâche et le côté : rien qui trahisse la dégradation ou l'original."""

    def __init__(self, tasks: list[dict], seed: int, binary: str, font):
        import tempfile as _tf
        self.tasks = {t["id"]: t for t in tasks}
        self.seed = seed
        self.binary = binary
        self.font = font
        self.dir = Path(_tf.mkdtemp(prefix="libretto_wav_"))
        self.lock = threading.Lock()

    def wav_for(self, task_id: int, slot: str) -> Path:
        from .render import render_wav
        if slot not in ("A", "B") or task_id not in self.tasks:
            raise ValueError(f"tâche/côté inconnu : {task_id}/{slot}")
        out = self.dir / f"{task_id}_{slot}.wav"
        with self.lock:                      # un rendu à la fois suffit
            if not out.exists():
                side_a, side_b = midi_pair(self.tasks[task_id], self.seed)
                md = side_a if slot == "A" else side_b
                render_wav(md, out, self.binary, self.font)
        return out


def _handler(store: _Judgements, seed: int, renderer: str = RENDERER,
             wav_cache: "_WavCache | None" = None):
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
                page = PAGE.replace("__RENDERER__", renderer)
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/task":
                task = store.next_task()
                if task is None:
                    self._json(200, {"done": True})
                    return
                if wav_cache is not None:
                    # mode instrument : le client ne reçoit que deux URL
                    # opaques — même pas les notes
                    payload = {"id": task["id"],
                               "wav": {"A": f"/api/audio/{task['id']}/A.wav",
                                       "B": f"/api/audio/{task['id']}/B.wav"}}
                else:
                    payload = render_task(task, seed)
                payload["n_total"] = len(store.tasks)
                payload["n_done"] = len(store.done_keys())
                self._json(200, payload)
            elif self.path.startswith("/api/audio/") and wav_cache is not None:
                try:
                    _api, _audio, tid, name = self.path.strip("/").split("/")
                    wav = wav_cache.wav_for(int(tid), name.split(".")[0])
                except (ValueError, KeyError) as exc:
                    self._json(404, {"error": str(exc)})
                    return
                self._send(200, wav.read_bytes(), "audio/wav")
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
            self._json(200, {"ok": True, "n_done": len(store.done_keys())})

    return Handler


def main(corpus_dir: str, out: str, host: str = "127.0.0.1",
         port: int | None = None, seed: int = 1, per_file: int = 2,
         only: list[str] | None = None, render: str = "synth") -> int:
    duels = Path(corpus_dir).suffix.lower() == ".json"
    if only and duels:
        print("libretto: --only ne s'applique qu'aux dégradations, pas aux duels")
        return 1
    if only:
        inconnues = sorted(set(only) - set(DEGRADATIONS))
        if inconnues:
            print(f"libretto: dégradation(s) inconnue(s) : {', '.join(inconnues)}")
            print(f"          disponibles : {', '.join(sorted(DEGRADATIONS))}")
            return 1
    tasks = (build_duel_tasks(corpus_dir, seed=seed) if duels
             else build_tasks(corpus_dir, seed=seed, per_file=per_file, only=only))
    if not tasks:
        print(f"libretto: aucun MIDI exploitable dans {corpus_dir}")
        return 1
    wav_cache = None
    renderer = RENDERER
    if render == "instrument":
        from .render import renderer_available, install_hint
        avail = renderer_available()
        if avail is None:
            print(f"libretto: {install_hint()}")
            return 1
        binary, font = avail
        renderer = f"{RENDERER_INSTRUMENT}:{font.name}"
        wav_cache = _WavCache(tasks, seed, binary, font)
        print(f"rendu instrumental : {font.name} via {binary}")
    try:
        store = _Judgements(Path(out), tasks, seed, renderer)
    except ValueError as exc:
        print(f"libretto: {exc}")
        return 1
    handler = _handler(store, seed, renderer, wav_cache)
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
    print(f"{len(tasks)} comparaisons ({n_control} paires de contrôle identiques), "
          f"rendu {renderer}")
    print(f"déjà jugées : {len(store.done_keys())}   →  jugements dans {out}")
    print(f"écoute comparée : http://{host}:{real}   (Ctrl-C pour arrêter)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ninterrompu — les jugements sont enregistrés au fur et à mesure")
    return 0
