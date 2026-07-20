"""
Libretto — rapports : texte, JSON, HTML (radar 6 groupes + détail 29 axes).
HTML auto-contenu (CSS inline, SVG), thème clair/sombre automatique.
"""

from __future__ import annotations

import json
import math

from .axes import GROUP_NAMES, SenseOfMusicalStructure


def render_text(sms: SenseOfMusicalStructure) -> str:
    return sms.summary()


def render_json(sms: SenseOfMusicalStructure, source: str = "") -> str:
    payload = sms.to_dict()
    payload["source"] = source
    payload["tool"] = "libretto-sms"
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _radar_svg(groups: dict[str, float], size: int = 340) -> str:
    cx = cy = size / 2
    radius = size / 2 - 46
    keys = sorted(groups)
    n = len(keys)
    if n < 3:
        return ""

    def point(i: int, value: float) -> tuple[float, float]:
        angle = math.radians(-90 + i * 360 / n)
        return cx + radius * value * math.cos(angle), cy + radius * value * math.sin(angle)

    rings = []
    for level in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, level) for i in range(n)))
        rings.append(f'<polygon points="{pts}" fill="none" stroke="var(--grid)" stroke-width="1"/>')
    spokes = []
    labels = []
    for i, key in enumerate(keys):
        x, y = point(i, 1.0)
        spokes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
                      f'stroke="var(--grid)" stroke-width="1"/>')
        lx, ly = point(i, 1.22)
        labels.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                      f'dominant-baseline="middle" fill="var(--fg)" font-size="13" '
                      f'font-weight="600">{key} · {groups[key]:.2f}</text>')
    data_pts = " ".join(f"{x:.1f},{y:.1f}"
                        for x, y in (point(i, max(0.02, groups[k])) for i, k in enumerate(keys)))
    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="Radar des groupes SMS">'
        + "".join(rings) + "".join(spokes)
        + f'<polygon points="{data_pts}" fill="var(--accent-soft)" '
          f'stroke="var(--accent)" stroke-width="2"/>'
        + "".join(labels) + "</svg>"
    )


def render_html(sms: SenseOfMusicalStructure, title: str = "Analyse SMS") -> str:
    data = sms.to_dict()
    groups = data["groups"]
    global_score = data["global_score"]

    confidence = data["confidence"]
    level = data["confidence_level"]
    interpretable = confidence >= 0.55

    rows = []
    for ax in data["axes"]:
        details = ", ".join(
            f"{k}: {v}" for k, v in ax["details"].items()
            if not isinstance(v, (list, dict))
        ) or "—"
        pct = round(ax["score"] * 100)
        conf = ax["confidence"]
        # Un axe sans matière est grisé : son score est un défaut, et
        # l'afficher comme les autres reviendrait à l'affirmer.
        cls = ' class="dim"' if conf < 0.5 else ""
        badge = (f'<span class="warn" title="fiabilité {conf:.2f}">⚠</span>'
                 if conf < 0.5 else "")
        rows.append(
            f'<tr{cls}><td class="mono">{ax["id"]}</td><td>{ax["name"]} {badge}</td>'
            f'<td class="mono">{ax["group"]}</td>'
            f'<td><div class="bar"><div class="fill" style="width:{pct}%"></div></div></td>'
            f'<td class="mono score">{ax["score"]:.2f}</td>'
            f'<td class="mono conf">{conf:.2f}</td>'
            f'<td class="details">{details}</td></tr>'
        )

    banner = "" if interpretable else (
        '<div class="banner"><b>Score non interprétable</b> — fiabilité '
        f'{confidence:.2f} ({level}). Ce fichier n\'offre pas de quoi renseigner '
        'les 29 axes : Libretto analyse des morceaux, et une boucle de quelques '
        'mesures n\'a ni forme, ni harmonie, ni mélodie à mesurer. '
        'Sur ce type de matériel, la validation contrastive tombe au niveau du '
        'hasard.</div>'
    )

    legend = "".join(
        f'<span class="chip"><b>{g}</b> {GROUP_NAMES[g]}</span>' for g in sorted(groups)
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Libretto — {title}</title>
<style>
:root {{
  --bg: #faf9f6; --fg: #1a1a1e; --muted: #6b6b74; --card: #ffffff;
  --grid: #d8d6cf; --accent: #7c5cff; --accent-soft: rgba(124, 92, 255, .18);
  --border: #e4e2db; --warn: #b4610d; --warn-bg: rgba(180, 97, 13, .09);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14141a; --fg: #ecebf2; --muted: #9a99a6; --card: #1d1d26;
    --grid: #34343f; --accent: #9d85ff; --accent-soft: rgba(157, 133, 255, .22);
    --border: #2b2b36; --warn: #f0a35e; --warn-bg: rgba(240, 163, 94, .12);
  }}
}}
* {{ box-sizing: border-box; margin: 0; }}
body {{ background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, "SF Pro Text", Segoe UI, sans-serif; padding: 32px 20px; }}
main {{ max-width: 980px; margin: 0 auto; }}
h1 {{ font-size: 26px; letter-spacing: -.02em; }}
h1 small {{ color: var(--muted); font-weight: 500; font-size: 15px; display: block; margin-top: 4px; }}
.hero {{ display: flex; flex-wrap: wrap; gap: 24px; align-items: center;
  background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  padding: 24px; margin: 24px 0; }}
.gauge {{ text-align: center; min-width: 180px; }}
.gauge .value {{ font-size: 56px; font-weight: 700; letter-spacing: -.03em; color: var(--accent); }}
.gauge .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 24px; }}
.chip {{ background: var(--card); border: 1px solid var(--border); border-radius: 999px;
  padding: 4px 12px; font-size: 12.5px; color: var(--muted); }}
.chip b {{ color: var(--accent); margin-right: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: var(--card);
  border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
.table-wrap {{ overflow-x: auto; border-radius: 16px; }}
th, td {{ padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--border);
  vertical-align: middle; }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }}
tr:last-child td {{ border-bottom: none; }}
.mono {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12.5px; }}
.score {{ font-weight: 700; }}
.bar {{ width: 110px; height: 8px; border-radius: 4px; background: var(--grid); overflow: hidden; }}
.fill {{ height: 100%; background: var(--accent); border-radius: 4px; }}
.details {{ color: var(--muted); font-size: 12.5px; max-width: 320px; }}
.conf {{ color: var(--muted); }}
tr.dim td {{ opacity: .45; }}
tr.dim .fill {{ background: var(--muted); }}
.warn {{ color: var(--warn); font-size: 12px; }}
.gauge .sub {{ font-size: 13px; color: var(--muted); margin-top: 6px; }}
.gauge.untrusted .value {{ color: var(--muted); }}
.banner {{ background: var(--warn-bg); border: 1px solid var(--warn);
  border-radius: 12px; padding: 14px 16px; margin: 16px 0; font-size: 14px;
  line-height: 1.5; }}
.banner b {{ color: var(--warn); }}
footer {{ color: var(--muted); font-size: 12.5px; margin-top: 24px; }}
</style>
</head>
<body>
<main>
  <h1>Libretto — Sense of Musical Structure<small>{title}</small></h1>
  <div class="hero">
    <div class="gauge{'' if interpretable else ' untrusted'}">
      <div class="value">{global_score:.2f}</div>
      <div class="label">Score global SMS</div>
      <div class="sub">fiabilité {confidence:.2f} · {level}</div>
    </div>
    <div>{_radar_svg(groups)}</div>
  </div>
  {banner}
  <div class="chips">{legend}</div>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Axe</th><th>Nom</th><th>Grp</th><th>Score</th><th></th><th>Fiab.</th><th>Détails</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  </div>
  <footer>Libretto SMS — 29 axes structurels, scores normalisés [0, 1], pondération totale = 1.0.</footer>
</main>
</body>
</html>"""
