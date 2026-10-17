"""Static site renderer.

Publishes one self-contained page per franchise. The page embeds the per-source
aggregates, so the reader can switch the source scope interactively and watch the
scores recompute — the whole point of the redesign.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, http

CHARTJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"


# --------------------------------------------------------------------- vendor
def ensure_vendor() -> Path:
    target = config.OUTPUT / "vendor" / "chart.umd.js"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(http.fetch_text(CHARTJS_URL), "utf-8")
        (target.parent / "README.md").write_text(
            "# Vendored assets\n\n"
            f"- `chart.umd.js` — Chart.js 4.4.1, from {CHARTJS_URL}\n\n"
            "Vendored rather than loaded from a CDN so the published site has no\n"
            "third-party runtime dependency. Update deliberately, and note the change\n"
            "in the commit message.\n",
            "utf-8",
        )
    return target


# ------------------------------------------------------------------- helpers
def _js(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def _latest_derived(franchise_id: str, date: str, rubric_version: str) -> dict:
    directory = config.derived_dir(franchise_id, date)
    if not directory.exists():
        raise SystemExit(f"no derived output in {directory} (run: sciorfi score)")
    candidates = sorted(directory.glob(f"*-{rubric_version}.json"))
    if not candidates:
        raise SystemExit(f"no derived output for {rubric_version} in {directory} (run: sciorfi score)")
    return config.read_json(candidates[-1])


def _baseline(franchise_id: str) -> dict | None:
    path = config.DATA / "baselines" / franchise_id / "baseline-v1.json"
    return config.read_json(path) if path.exists() else None


def _rubric_html(rubric: dict) -> str:
    blocks = []
    for axis, spec in rubric["axes"].items():
        items = []
        for s in spec["subsignals"]:
            if s["coder"] == "lexicon":
                detail = f"{len(s['patterns'])} terms"
            else:
                detail = f"curated entity list ({s.get('entitySource', 'franchise')})"
            items.append(
                f"<li><code>{s['id']}</code> — coder <em>{s['coder']}</em>, {detail}, "
                f"weight {s['weight']}, saturates at {s['saturation']}</li>"
            )
        blocks.append(f"<h3>{spec['label']}</h3><p>{spec['definition']}</p><ul>{''.join(items)}</ul>")
    reserved = rubric.get("reserved", {})
    reserved_list = ", ".join(
        f"<code>{s}</code>" for s in reserved.get("sci", []) + reserved.get("fi", [])
    )
    unc = rubric["uncertainty"]
    return (
        f"<div class='rubric-grid'>{''.join(f'<div>{b}</div>' for b in blocks)}</div>"
        f"<p class='muted'>{rubric['scale']['notes']}</p>"
        f"<p class='muted'><strong>Uncertainty.</strong> {unc['method']}: "
        f"95% CI of the episode-level mean (z={unc['z']}), inflated ×{unc['thinSampleInflation']} "
        f"below {unc['thinSampleBelow']} episodes. Measured from the episode distribution, never asserted.</p>"
        f"<p class='muted'><strong>Not yet coded</strong> (rubric-v3, needs a judgement coder rather than a "
        f"lexicon): {reserved_list}</p>"
    )


def _provenance_html(derived: dict, franchise: dict, rubric: dict) -> str:
    rows = []
    for source_id in derived["scope"]["sources"]:
        spec = franchise.get("sources", {}).get(source_id, {})
        rows.append(
            f"<tr><td><code>{source_id}</code></td><td>{spec.get('category','?')}</td>"
            f"<td>{spec.get('kind','?')}</td><td>{spec.get('license','?')}</td>"
            f"<td><a href='{spec.get('homepage','#')}'>source</a></td></tr>"
        )
    return (
        "<table class='prov'><thead><tr><th>source</th><th>category</th><th>what it gives</th>"
        "<th>licence</th><th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f"<p class='muted'>snapshot <code>{derived['snapshotDate']}</code> · "
        f"rubric <code>{derived['rubricVersion']}</code> · scope <code>{derived['scopeHash']}</code> · "
        f"generated <code>{derived['generatedAt']}</code>. Only derived counts are stored in the "
        "repository; source prose is cached outside it. Attribution for every source is above.</p>"
    )


# ---------------------------------------------------------------------- page
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fcfcfb;--card:#fff;--text:#0b0b0b;--text2:#52514e;--muted:#898781;--border:rgba(11,11,11,.10);--grid:#e1e0d9;--accent:#D85A30}
@media (prefers-color-scheme:dark){:root{--bg:#17171a;--card:#202024;--text:#f0efec;--text2:#c3c2b7;--muted:#898781;--border:rgba(255,255,255,.10);--grid:#2c2c2a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:24px 16px 48px;line-height:1.5}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:21px;font-weight:500;margin:0 0 4px}
h2{font-size:15px;font-weight:600;margin:26px 0 10px}
p.sub{font-size:14px;color:var(--text2);margin:0 0 18px;max-width:70ch}
.card{background:var(--card);border:.5px solid var(--border);border-radius:12px;padding:16px}
.layout{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start}
.main{flex:1 1 560px;min-width:320px}
.side{flex:0 1 300px;min-width:250px}
.chart-box{position:relative;width:100%;height:440px}
.rowhead{display:flex;justify-content:space-between;align-items:center;margin:14px 0 8px}
.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:flex;align-items:center;gap:6px;font-size:12px;padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer}
.chip.off{opacity:.4}
.chip .dot{width:9px;height:9px;border-radius:50%}
.chip[disabled]{cursor:default}
button.ctrl{font-size:12px;padding:5px 10px;border-radius:6px;border:.5px solid var(--border);background:var(--card);color:var(--text2);cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:.5px solid var(--border);font-variant-numeric:tabular-nums}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
td.num{text-align:right}
.bar{display:inline-block;height:8px;border-radius:2px;vertical-align:middle}
.swatch{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}
.muted{font-size:12px;color:var(--muted);line-height:1.6}
details{margin-top:22px;background:var(--card);border:.5px solid var(--border);border-radius:12px;padding:0 16px}
details>summary{cursor:pointer;font-size:13.5px;font-weight:600;padding:14px 0;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:'\\25B8  ';color:var(--muted)}
details[open]>summary::before{content:'\\25BE  '}
.rubric-grid{display:flex;gap:24px;flex-wrap:wrap}
.rubric-grid>div{flex:1 1 280px}
.rubric-grid h3{font-size:13px;margin:0 0 4px}
.rubric-grid p,.rubric-grid ul{font-size:12px;color:var(--text2)}
.rubric-grid ul{padding-left:18px;line-height:1.7}
.prov td,.prov th{font-size:12px}
.prov a{color:var(--accent)}
code{font-size:.92em;background:rgba(127,127,127,.14);padding:1px 4px;border-radius:4px}
.note{margin-top:18px}
</style>
</head>
<body>
<div class="wrap">
  <h1>__H1__</h1>
  <p class="sub">__SUB__</p>

  <div class="layout">
    <div class="main card">
      <div class="chart-box"><canvas id="chart" role="img" aria-label="Bubble chart of each season's Sci score against its Fi score"></canvas></div>
      <div class="rowhead">
        <span class="label">Series</span>
        <span><button class="ctrl" id="all">All</button> <button class="ctrl" id="none">None</button></span>
      </div>
      <div class="chips" id="seriesChips"></div>
    </div>
    <div class="side">
      <div class="card">
        <div class="rowhead" style="margin-top:0"><span class="label">Source scope</span><span id="scopeCount" class="muted"></span></div>
        <div class="chips" id="sourceChips"></div>
        <p class="muted" style="margin:10px 0 0">Scores recompute from the selected sources only. This is the readable form of <code>score = f(snapshot, scope, rubric)</code>.</p>
      </div>
      <div class="card" style="margin-top:12px">
        <label class="chip" id="baselineChip"><input type="checkbox" id="baselineToggle" checked style="accent-color:var(--accent)"> show __BASELINE_LABEL__</label>
        <p class="muted" style="margin:10px 0 0">__BASELINE_NOTE__</p>
      </div>
      <div class="card" style="margin-top:12px">
        <label class="chip"><input type="checkbox" id="zoomToggle" style="accent-color:var(--accent)"> zoom to data</label>
        <p class="muted" style="margin:10px 0 0">Off by default: the full 0&ndash;10 scale makes it plain how little of it the
        measured scores occupy. Zooming rescales the axes to the visible points, which exaggerates small gaps &mdash; read it
        alongside the confidence intervals, never on its own.</p>
      </div>
    </div>
  </div>

  <h2>Season scores</h2>
  <div class="card"><table id="scoresTable"></table></div>

  <details open>
    <summary>Rubric, uncertainty and provenance</summary>
    __RUBRIC__
    <h2 style="margin-top:20px">Sources in this scope</h2>
    __PROVENANCE__
  </details>

  <p class="muted note">Bubble size is the 95% confidence interval of the season's episode-level mean — a measured quantity. Bubble position is the season score. Sci and Fi are scored from disjoint term sets, so they are independent by construction.</p>
</div>

<script src="vendor/chart.umd.js"></script>
<script>
const DATA = __DATA__;
const RUBRIC = __RUBRIC_JS__;
const BASELINE = __BASELINE__;
const CARD = (getComputedStyle(document.documentElement).getPropertyValue('--card') || '#fff').trim();

const selected = new Set(DATA.scope.sources);
const on = new Set(DATA.series.map(s => s.id));
let showBaseline = true;
let zoom = false;

function combine(season, axis) {
  const per = (season[axis] && season[axis].perSource) || {};
  const parts = Object.entries(per).filter(([k]) => selected.has(k));
  if (!parts.length) return null;
  const n = parts.reduce((a, [, p]) => a + p.n, 0);
  if (!n) return null;
  const mean = parts.reduce((a, [, p]) => a + p.mean * p.n, 0) / n;
  const density = parts.reduce((a, [, p]) => a + p.density * p.n, 0) / n;
  const ci = Math.sqrt(parts.reduce((a, [, p]) => a + (p.n * p.n) * (p.ci95 * p.ci95), 0)) / n;
  return { mean, density, ci, n, sources: parts.map(([k]) => k) };
}
const radius = ci => 3 + Math.min(1, ci / 2.5) * 21;
const f2 = v => (v === undefined || v === null) ? '-' : v.toFixed(2);
const yr = se => !se.year ? '-' : (se.yearEnd ? se.year + '\u2013' + String(se.yearEnd).slice(2) : String(se.year));

let chart = null;
function build() {
  const datasets = [];
  for (const s of DATA.series) {
    const pts = [];
    for (const season of s.seasons) {
      const sci = combine(season, 'sci'), fi = combine(season, 'fi');
      if (!sci || !fi) continue;
      pts.push({ x: sci.mean, y: fi.mean, r: radius(Math.max(sci.ci, fi.ci)),
                 meta: { s, season, sci, fi } });
    }
    datasets.push({ label: s.short, data: pts, backgroundColor: s.color + 'd9',
                    borderColor: CARD, borderWidth: 1.5, hidden: !on.has(s.id),
                    hoverBorderColor: s.color, hoverBorderWidth: 2 });
  }
  if (showBaseline && BASELINE) {
    for (const b of BASELINE.series) {
      const s = DATA.series.find(x => x.id === b.id);
      if (!s || !on.has(b.id)) continue;
      datasets.push({ label: b.short + ' baseline-v1', data: b.seasons.map(x => ({ x: x.sci, y: x.fi, r: 6 })),
                      backgroundColor: 'transparent', borderColor: '#898781', borderWidth: 1,
                      pointStyle: 'crossRot', showLine: false });
    }
  }
  return datasets;
}

const tip = {
  callbacks: {
    label: ctx => {
      const d = ctx.raw;
      if (!d.meta) return `${ctx.dataset.label}: Sci ${d.x}, Fi ${d.y}`;
      const { s, season, sci, fi } = d.meta;
      return [
        `${s.short} S${season.season}${season.year ? ' (' + yr(season) + ')' : ''} · ${season.episodeCount} eps`,
        `Sci ${f2(sci.mean)} ± ${f2(sci.ci)}  (${f2(sci.density)}/1k)`,
        `Fi  ${f2(fi.mean)} ± ${f2(fi.ci)}  (${f2(fi.density)}/1k)`,
        `n = ${sci.n} · ${sci.sources.join(' + ')}`
      ];
    }
  }
};

function visibleValues() {
  const xs = [], ys = [];
  for (const s of DATA.series) {
    if (!on.has(s.id)) continue;
    for (const season of s.seasons) {
      const sci = combine(season, 'sci'), fi = combine(season, 'fi');
      if (!sci || !fi) continue;
      xs.push(sci.mean); ys.push(fi.mean);
    }
  }
  if (showBaseline && BASELINE) {
    for (const b of BASELINE.series) {
      if (!on.has(b.id)) continue;
      for (const x of b.seasons) { xs.push(x.sci); ys.push(x.fi); }
    }
  }
  return { xs, ys };
}
function fitRange(values) {
  if (!values.length) return [0, 10];
  const lo = Math.min(...values), hi = Math.max(...values);
  const pad = Math.max(0.4, (hi - lo) * 0.3);
  return [Math.max(0, Math.floor((lo - pad) * 2) / 2), Math.min(10, Math.ceil((hi + pad) * 2) / 2)];
}
function draw() {
  if (chart) chart.destroy();
  const { xs, ys } = visibleValues();
  const xr = zoom ? fitRange(xs) : [0, 10];
  const yr = zoom ? fitRange(ys) : [0, 10];
  const styles = getComputedStyle(document.documentElement);
  const grid = styles.getPropertyValue('--grid');
  const text2 = (styles.getPropertyValue('--text2') || '#52514e').trim();
  chart = new Chart(document.getElementById('chart'), {
    type: 'bubble',
    data: { datasets: build() },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { min: xr[0], max: xr[1], grid: { color: grid },
             ticks: { color: text2 },
             title: { display: true, text: 'Sci — scientific / engineering rigour', color: text2 } },
        y: { min: yr[0], max: yr[1], grid: { color: grid },
             ticks: { color: text2 },
             title: { display: true, text: 'Fi — myth / magic / reality-bending', color: text2 } }
      },
      plugins: { legend: { display: false }, tooltip: tip }
    }
  });
}

function table() {
  const head = '<thead><tr><th>Series</th><th>Season</th><th>Year</th><th class="num">Eps</th>'
    + '<th class="num">Sci</th><th></th><th class="num">Fi</th><th></th><th class="num">n</th></tr></thead>';
  const rows = [];
  for (const s of DATA.series) {
    if (!on.has(s.id)) continue;
    for (const season of s.seasons) {
      const sci = combine(season, 'sci'), fi = combine(season, 'fi');
      if (!sci || !fi) continue;
      const bar = (v, color) => `<span class="bar" style="width:${Math.round(v * 9)}px;background:${color}"></span>`;
      rows.push(`<tr><td><span class="swatch" style="background:${s.color}"></span>${s.short}</td>`
        + `<td>${season.season}</td><td>${yr(season)}</td><td class="num">${season.episodeCount}</td>`
        + `<td class="num">${f2(sci.mean)} <span class="muted">±${f2(sci.ci)}</span></td><td>${bar(sci.mean, s.color)}</td>`
        + `<td class="num">${f2(fi.mean)} <span class="muted">±${f2(fi.ci)}</span></td><td>${bar(fi.mean, s.color)}</td>`
        + `<td class="num">${sci.n}</td></tr>`);
    }
  }
  document.getElementById('scoresTable').innerHTML = head + '<tbody>' + rows.join('') + '</tbody>';
}

function chips() {
  const sc = document.getElementById('seriesChips');
  sc.innerHTML = '';
  for (const s of DATA.series) {
    const b = document.createElement('button');
    b.className = 'chip' + (on.has(s.id) ? '' : ' off');
    b.innerHTML = `<span class="dot" style="background:${s.color}"></span>${s.short}`;
    b.title = `${s.name} · ${s.seasons.length} season(s)`;
    b.onclick = () => { on.has(s.id) ? on.delete(s.id) : on.add(s.id); refresh(); };
    sc.appendChild(b);
  }
  const sr = document.getElementById('sourceChips');
  sr.innerHTML = '';
  for (const src of DATA.scope.sources) {
    const b = document.createElement('button');
    b.className = 'chip' + (selected.has(src) ? '' : ' off');
    b.textContent = src;
    b.onclick = () => {
      selected.has(src) ? selected.delete(src) : selected.add(src);
      if (!selected.size) selected.add(src);
      refresh();
    };
    sr.appendChild(b);
  }
  document.getElementById('scopeCount').textContent = selected.size + '/' + DATA.scope.sources.length;
}

function refresh() { chips(); draw(); table(); }

document.getElementById('all').onclick = () => { DATA.series.forEach(s => on.add(s.id)); refresh(); };
document.getElementById('none').onclick = () => { on.clear(); refresh(); };
document.getElementById('baselineToggle').onchange = e => { showBaseline = e.target.checked; refresh(); };
document.getElementById('zoomToggle').onchange = e => { zoom = e.target.checked; refresh(); };
refresh();
</script>
</body>
</html>
"""


INDEX_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outputs — Sci or Fi</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.6}
h1{font-size:22px;font-weight:600}
a{color:#0969da}
.muted{font-size:12.5px;color:#666}
li{margin:6px 0}
</style>
</head>
<body>
<h1>Outputs</h1>
<p><a href="../">&larr; Back to home</a></p>
<ul>
__ITEMS__
</ul>
<p class="muted">Every page here is generated by the pipeline in <code>sciorfi/</code>. Do not edit them by hand.</p>
</body>
</html>
"""

ROOT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sci or Fi</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.6}
h1{font-size:24px;font-weight:600}
a{color:#0969da}
.muted{font-size:12.5px;color:#666}
li{margin:6px 0}
</style>
</head>
<body>
<h1>Sci or Fi</h1>
<p>How much is science, how much is myth — measured per episode from named sources, aggregated to
seasons. Scores are computed from a declared source scope, so you can see which evidence a finding
rests on.</p>
<ul>
__ITEMS__
<li><a href="output/">All outputs</a></li>
</ul>
<p class="muted">Generated by the pipeline in <code>sciorfi/</code>; methodology in
<code>docs/redesign.md</code>.</p>
</body>
</html>
"""


def render_all(franchise_id: str, date: str, rubric: dict) -> list[Path]:
    ensure_vendor()
    derived = _latest_derived(franchise_id, date, rubric["version"])
    franchise = config.franchise_meta(franchise_id)
    baseline = _baseline(franchise_id)

    sources = derived["scope"]["sources"]
    sub = (
        f"Each season is scored on two independent axes. <strong>Sci</strong> is how much the "
        f"episode text turns on technical and scientific vocabulary; <strong>Fi</strong> is how much it "
        f"turns on god-like power, prophecy and reality-bending. Scores are computed per episode from a "
        f"source scope, then averaged per season; bubble size is the measured 95% confidence interval of "
        f"that average, never a hand-set value. Currently <code>{len(sources)}</code> sources in scope "
        f"({' + '.join(sources)}); toggle them on the right and the chart recomputes."
    )
    baseline_note = (
        f"Overlay of <code>baseline-v1</code> — the original whole-season hand-scored integers, frozen before "
        f"the redesign. Shown as crosses so the gap between a single reader's holistic integer and the "
        f"measured episode aggregate is visible."
    ) if baseline else "No baseline recorded for this franchise."

    page = (
        PAGE.replace("__TITLE__", f"{franchise['name']}: Sci vs Fi, season by season")
        .replace("__H1__", f"{franchise['name']}: Sci vs Fi, season by season")
        .replace("__SUB__", sub)
        .replace("__RUBRIC__", _rubric_html(rubric))
        .replace("__PROVENANCE__", _provenance_html(derived, franchise, rubric))
        .replace("__BASELINE_LABEL__", baseline["label"] if baseline else "baseline")
        .replace("__BASELINE_NOTE__", baseline_note)
        .replace("__DATA__", _js(derived))
        .replace("__RUBRIC_JS__", _js(rubric))
        .replace("__BASELINE__", _js(baseline))
    )
    out = config.OUTPUT / f"{franchise_id}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, "utf-8")

    # index pages
    pages = sorted(p.stem for p in config.OUTPUT.glob("*.html") if p.stem != "index")

    def _label(fid: str) -> str:
        try:
            return config.franchise_meta(fid)["name"]
        except SystemExit:
            return fid

    items_out = "\n".join(f'<li><a href="{n}.html">{_label(n)}</a></li>' for n in pages)
    items_root = "\n".join(f'<li><a href="output/{n}.html">{_label(n)}</a></li>' for n in pages)
    (config.OUTPUT / "index.html").write_text(INDEX_PAGE.replace("__ITEMS__", items_out), "utf-8")
    (config.ROOT / "index.html").write_text(ROOT_PAGE.replace("__ITEMS__", items_root), "utf-8")
    return [out, config.OUTPUT / "index.html", config.ROOT / "index.html"]
