"""
Reads .tmp/workshops.db and generates history_dashboard.html.
Shows historical session occupancy per workshop per location.
"""

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import requests

CHARTJS_CDN   = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
CHARTJS_CACHE = Path(__file__).parent.parent / ".tmp" / "chartjs.min.js"

DB_PATH  = Path(__file__).parent.parent / ".tmp" / "workshops.db"
OUT_PATH = Path(__file__).parent.parent / "history_dashboard.html"


def get_chartjs() -> str:
    """Return Chart.js source — from local cache if available, else download."""
    if CHARTJS_CACHE.exists():
        return CHARTJS_CACHE.read_text(encoding="utf-8")
    print("  Chart.js downloaden...", end=" ", flush=True)
    js = requests.get(CHARTJS_CDN, timeout=30).text
    CHARTJS_CACHE.write_text(js, encoding="utf-8")
    print(f"{len(js):,} bytes gecached.")
    return js


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def q(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_data(conn):
    today = date.today().isoformat()

    # ── Past sessions (final known state) ────────────────────────────────
    past_sessions = q(conn, """
        SELECT workshop_name, location, country, session_date, session_time,
               booked_spots, available_spots, total_capacity, occupancy_pct, price
        FROM sessions
        WHERE session_date < ?
        ORDER BY session_date DESC, session_time, location
    """, (today,))

    # ── Per workshop per location: avg final occupancy ────────────────────
    by_workshop = q(conn, """
        SELECT workshop_name, location, country,
               COUNT(*) AS sessions,
               ROUND(AVG(occupancy_pct), 1) AS avg_occ,
               ROUND(AVG(booked_spots), 1) AS avg_booked,
               SUM(CASE WHEN available_spots = 0 THEN 1 ELSE 0 END) AS full_count,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_cap
        FROM sessions
        WHERE session_date < ?
        GROUP BY workshop_name, location
        ORDER BY location, avg_occ DESC
    """, (today,))

    # ── Per location summary ──────────────────────────────────────────────
    location_summary = q(conn, """
        SELECT location, country,
               COUNT(*) AS sessions,
               ROUND(AVG(occupancy_pct), 1) AS avg_occ,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_cap,
               SUM(CASE WHEN available_spots = 0 THEN 1 ELSE 0 END) AS full_count
        FROM sessions
        WHERE session_date < ?
        GROUP BY location
        ORDER BY avg_occ DESC
    """, (today,))

    # ── Monthly occupancy trend (past 6 months) ───────────────────────────
    monthly = q(conn, """
        SELECT strftime('%Y-%m', session_date) AS month,
               location,
               COUNT(*) AS sessions,
               ROUND(AVG(occupancy_pct), 1) AS avg_occ
        FROM sessions
        WHERE session_date < ?
          AND session_date >= date(?, '-6 months')
        GROUP BY month, location
        ORDER BY month, location
    """, (today, today))

    # ── Booking lead time: avg occupancy by days-before (from snapshots) ──
    # For sessions that are now past, track how occupancy evolved
    lead_time = q(conn, """
        SELECT
            CAST(julianday(sn.session_date) - julianday(sn.snapshot_date) AS INTEGER) AS days_before,
            location,
            ROUND(AVG(sn.occupancy_pct), 1) AS avg_occ,
            COUNT(DISTINCT sn.url || sn.session_date || sn.session_time) AS n_sessions
        FROM snapshots sn
        WHERE sn.session_date < ?
          AND sn.session_date >= sn.snapshot_date
          AND CAST(julianday(sn.session_date) - julianday(sn.snapshot_date) AS INTEGER) BETWEEN 0 AND 90
        GROUP BY days_before, location
        HAVING n_sessions >= 2
        ORDER BY days_before, location
    """, (today,))

    # ── Occupancy distribution buckets ───────────────────────────────────
    dist = q(conn, """
        SELECT location,
               SUM(CASE WHEN occupancy_pct = 100 THEN 1 ELSE 0 END) AS volzet,
               SUM(CASE WHEN occupancy_pct >= 75 AND occupancy_pct < 100 THEN 1 ELSE 0 END) AS hoog,
               SUM(CASE WHEN occupancy_pct >= 50 AND occupancy_pct < 75  THEN 1 ELSE 0 END) AS midden,
               SUM(CASE WHEN occupancy_pct < 50 THEN 1 ELSE 0 END) AS laag,
               COUNT(*) AS total
        FROM sessions
        WHERE session_date < ?
        GROUP BY location
        ORDER BY location
    """, (today,))

    # ── Overall totals ────────────────────────────────────────────────────
    totals = conn.execute("""
        SELECT COUNT(*) AS n, ROUND(AVG(occupancy_pct),1) AS avg_occ,
               SUM(CASE WHEN available_spots=0 THEN 1 ELSE 0 END) AS full_count,
               SUM(booked_spots) AS total_booked, SUM(total_capacity) AS total_cap
        FROM sessions WHERE session_date < ?
    """, (today,)).fetchone()

    return {
        "generated_at":     today,
        "past_sessions":    past_sessions,
        "by_workshop":      by_workshop,
        "location_summary": location_summary,
        "monthly":          monthly,
        "lead_time":        lead_time,
        "dist":             dist,
        "totals": {
            "n":           totals["n"],
            "avg_occ":     totals["avg_occ"],
            "full_count":  totals["full_count"],
            "total_booked":totals["total_booked"],
            "total_cap":   totals["total_cap"],
        },
    }


HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BBQ Experience Center – Historisch Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=Barlow:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:      #18130f;
    --surface: #221c17;
    --border:  #3a2e24;
    --orange:  #e8722a;
    --orange2: #c95e18;
    --text:    #f0e8df;
    --muted:   #9a8a7a;
    --green:   #4caf74;
    --red:     #e05252;
    --yellow:  #e8b32a;
    --blue:    #5a9fd4;
    --purple:  #9b6fd4;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Barlow', sans-serif; font-size: 14px; min-height: 100vh; }

  header { background: var(--surface); border-bottom: 2px solid var(--orange); padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
  .logo-mark { width: 44px; height: 44px; background: var(--orange); display: flex; align-items: center; justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 16px; color: #fff; flex-shrink: 0; letter-spacing: 1px; }
  .logo-text h1 { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 22px; text-transform: uppercase; letter-spacing: 1px; line-height: 1; }
  .logo-text span { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 2px; }
  .header-right { margin-left: auto; text-align: right; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .header-right strong { display: block; font-family: 'Barlow Condensed', sans-serif; font-size: 16px; font-weight: 700; color: var(--text); letter-spacing: 0; text-transform: none; margin-top: 2px; }

  main { padding: 24px; max-width: 1400px; margin: 0 auto; }
  .section-label { font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; color: var(--muted); margin: 32px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }

  .stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 24px; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 16px 18px; }
  .stat-val { font-family: 'Barlow Condensed', sans-serif; font-size: 32px; font-weight: 800; line-height: 1; }
  .stat-lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-top: 5px; }
  .stat.orange .stat-val { color: var(--orange); }
  .stat.green  .stat-val { color: var(--green); }
  .stat.yellow .stat-val { color: var(--yellow); }
  .stat.red    .stat-val { color: var(--red); }
  .stat.blue   .stat-val { color: var(--blue); }

  .loc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-bottom: 8px; }
  .loc-card { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 18px 20px; }
  .loc-card h3 { font-family: 'Barlow Condensed', sans-serif; font-size: 18px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  .loc-card .country { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 14px; }
  .gauge-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .gauge-bar { flex: 1; height: 8px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .gauge-fill { height: 100%; border-radius: 2px; }
  .pct-label { font-family: 'Barlow Condensed', sans-serif; font-size: 28px; font-weight: 800; min-width: 64px; text-align: right; line-height: 1; }
  .loc-meta { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
  .loc-meta .item { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 9px 10px; }
  .loc-meta .item .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 3px; }
  .loc-meta .item strong { font-family: 'Barlow Condensed', sans-serif; font-size: 15px; font-weight: 700; }

  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .chart-box { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 20px; }
  .chart-box h4 { font-family: 'Barlow Condensed', sans-serif; font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 14px; }
  canvas { max-height: 320px; }

  .tab-row { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
  .tab-btn { padding: 6px 18px; font-size: 12px; border-radius: 12px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-family: 'Barlow Condensed', sans-serif; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; transition: all .15s; }
  .tab-btn.active { border-color: var(--orange); color: var(--orange); background: rgba(232,114,42,0.1); }
  .tab-btn:hover:not(.active) { border-color: var(--muted); color: var(--text); }

  .filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 3px; padding: 7px 12px; font-size: 12px; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; cursor: pointer; outline: none; text-transform: uppercase; letter-spacing: 1px; }
  select:focus { border-color: var(--orange); }

  .table-wrap { overflow-x: auto; }
  .table-container { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; }
  thead th { background: #2a211a; padding: 11px 14px; text-align: left; font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  tbody tr { border-bottom: 1px solid var(--border); }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: rgba(232,114,42,0.07); }
  td { padding: 10px 14px; vertical-align: middle; }
  .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 12px; font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }
  .badge-full   { background: rgba(224,82,82,0.15);   color: var(--red); }
  .badge-high   { background: rgba(76,175,116,0.15);  color: var(--green); }
  .badge-mid    { background: rgba(232,179,42,0.15);  color: var(--yellow); }
  .badge-low    { background: rgba(90,159,212,0.15);  color: var(--blue); }

  .horiz-bar-wrap { display: flex; flex-direction: column; gap: 6px; max-height: 520px; overflow-y: auto; }
  .hbar-row { display: flex; align-items: center; gap: 10px; }
  .hbar-label { font-size: 12px; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; width: 200px; min-width: 200px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; text-align: right; color: var(--text); }
  .hbar-track { flex: 1; height: 18px; background: var(--border); border-radius: 2px; overflow: hidden; position: relative; }
  .hbar-fill { height: 100%; border-radius: 2px; transition: width .6s; }
  .hbar-pct { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); font-size: 11px; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,.5); }
  .hbar-sessions { width: 70px; font-size: 11px; color: var(--muted); text-align: right; }

  .note { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-top: 8px; }

  @media(max-width:900px) { .stats { grid-template-columns: repeat(3,1fr); } .charts-row { grid-template-columns: 1fr; } }
  @media(max-width:600px) { .stats { grid-template-columns: repeat(2,1fr); } main { padding: 16px; } .hbar-label { width: 120px; min-width: 120px; } }
</style>
</head>
<body>

<header>
  <div class="logo-mark">BXC</div>
  <div class="logo-text">
    <h1>BBQ Experience Center</h1>
    <span>Historisch Dashboard &mdash; Gerealiseerde Bezettingsgraad</span>
  </div>
  <div class="header-right">
    <div>Gegenereerd op<strong id="genDate"></strong></div>
    <div style="margin-top:6px">
      <a href="?" style="font-family:\'Barlow Condensed\',sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);text-decoration:none;border:1px solid var(--border);border-radius:4px;padding:4px 12px;" onmouseover="this.style.color=\'var(--text)\'" onmouseout="this.style.color=\'var(--muted)\'">&#8592; Actueel Dashboard</a>
    </div>
  </div>
</header>

<main>

  <div class="stats" id="kpiGrid"></div>

  <div class="section-label">Gerealiseerde Bezetting per Vestiging</div>
  <div class="loc-grid" id="locGrid"></div>

  <div class="section-label">Gemiddelde Bezettingsgraad per Workshop</div>
  <div class="tab-row">
    <button class="tab-btn active" id="tab-Roosendaal" onclick="showTab('Roosendaal')">Roosendaal</button>
    <button class="tab-btn"        id="tab-Nunspeet"   onclick="showTab('Nunspeet')">Nunspeet</button>
    <button class="tab-btn"        id="tab-Herent"     onclick="showTab('Herent')">Herent</button>
  </div>
  <div class="chart-box">
    <div id="horizBars" class="horiz-bar-wrap"></div>
  </div>

  <div class="section-label">Bezettingsverdeling &amp; Maandtrend</div>
  <div class="charts-row">
    <div class="chart-box">
      <h4>Verdeling per Bezettingsklasse</h4>
      <canvas id="distChart"></canvas>
    </div>
    <div class="chart-box">
      <h4>Gemiddelde Bezetting per Maand</h4>
      <canvas id="monthChart"></canvas>
    </div>
  </div>

  <div class="section-label">Boekingscurve — Hoe Vroeg Vullen Sessies Zich?</div>
  <div class="chart-box">
    <h4>Gemiddelde bezetting op X dagen v&oacute;&oacute;r sessiedatum (o.b.v. dagelijkse scrapes)</h4>
    <canvas id="leadChart" style="max-height:260px"></canvas>
  </div>
  <p class="note">Gebaseerd op {{SNAP_COUNT}} snapshot-metingen over {{SNAP_DAYS}} meetdagen</p>

  <div class="section-label">Alle Voltooide Sessies</div>
  <div class="filter-row">
    <select id="filterLoc2" onchange="renderPastTable()">
      <option value="">Alle locaties</option>
    </select>
    <select id="filterWorkshop2" onchange="renderPastTable()">
      <option value="">Alle workshops</option>
    </select>
  </div>
  <div class="table-container">
    <div class="table-wrap">
      <table id="pastTable">
        <thead>
          <tr>
            <th>Datum</th><th>Tijd</th><th>Workshop</th><th>Locatie</th>
            <th>Geboekt</th><th>Max</th><th>Bezetting</th><th>Status</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

</main>

<script>
const DATA = __DATA__;
document.getElementById('genDate').textContent = DATA.generated_at;

const COLORS = {
  Roosendaal: '#e8722a',
  Nunspeet:   '#5a9fd4',
  Herent:     '#4caf74',
};

function gaugeColor(pct) {
  if (pct >= 80) return '#4caf74';
  if (pct >= 50) return '#e8b32a';
  return '#5a9fd4';
}

// ── KPI ───────────────────────────────────────────────────────────────────
(function() {
  const t = DATA.totals;
  const fullPct = t.n ? Math.round(t.full_count / t.n * 100) : 0;
  const best = DATA.location_summary.sort((a,b) => b.avg_occ - a.avg_occ)[0];
  const kpis = [
    { v: t.n,          l: 'Voltooide sessies', sub: 'bijgehouden in database', cls: '' },
    { v: t.avg_occ + '%', l: 'Gem. bezetting', sub: 'over alle locaties', cls: t.avg_occ >= 80 ? 'green' : t.avg_occ >= 50 ? 'yellow' : 'blue' },
    { v: t.full_count, l: 'Volzet', sub: fullPct + '% van alle sessies', cls: 'green' },
    { v: t.total_booked, l: 'Tafels gerealiseerd', sub: 'van ' + t.total_cap + ' totaal', cls: 'orange' },
    { v: best ? best.location : '—', l: 'Beste vestiging', sub: best ? best.avg_occ + '% gem. bezetting' : '', cls: '' },
  ];
  document.getElementById('kpiGrid').innerHTML = kpis.map(k =>
    '<div class="stat ' + (k.cls||'') + '"><div class="stat-val">' + k.v + '</div><div class="stat-lbl">' + k.l + '</div><div style="font-size:11px;color:var(--muted);margin-top:3px">' + k.sub + '</div></div>'
  ).join('');
})();

// ── Location Cards ────────────────────────────────────────────────────────
(function() {
  const grid = document.getElementById('locGrid');
  grid.innerHTML = DATA.location_summary.map(loc => {
    const pct = loc.avg_occ || 0;
    const color = gaugeColor(pct);
    const fullPct = loc.sessions ? Math.round(loc.full_count / loc.sessions * 100) : 0;
    const d = DATA.dist.find(d => d.location === loc.location) || {};
    return '<div class="loc-card">' +
      '<h3>' + loc.location + '</h3>' +
      '<div class="country">' + loc.country + ' &mdash; ' + loc.sessions + ' voltooide sessies</div>' +
      '<div class="gauge-wrap"><div class="gauge-bar"><div class="gauge-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
      '<div class="pct-label" style="color:' + color + '">' + pct + '%</div></div>' +
      '<div class="loc-meta">' +
        '<div class="item"><div class="k">Volzet</div><strong>' + fullPct + '%</strong></div>' +
        '<div class="item"><div class="k">Geboekt</div><strong>' + loc.total_booked + '/' + loc.total_cap + '</strong></div>' +
        '<div class="item"><div class="k">Gem. tafels</div><strong>' + (loc.total_cap > 0 ? (loc.total_booked / loc.sessions).toFixed(1) : '—') + '/10</strong></div>' +
      '</div>' +
    '</div>';
  }).join('');
})();

// ── Horizontal bar chart per workshop ─────────────────────────────────────
let activeTab = 'Roosendaal';

function showTab(loc) {
  activeTab = loc;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + loc).classList.add('active');
  renderHorizBars();
}

function renderHorizBars() {
  const rows = DATA.by_workshop.filter(w => w.location === activeTab)
    .sort((a, b) => b.avg_occ - a.avg_occ);
  const color = COLORS[activeTab] || '#e8722a';
  const wrap = document.getElementById('horizBars');
  if (!rows.length) {
    wrap.innerHTML = '<p style="color:var(--muted);padding:20px;font-family:\'Barlow Condensed\',sans-serif;text-transform:uppercase;letter-spacing:1px">Geen historische data voor ' + activeTab + '</p>';
    return;
  }
  wrap.innerHTML = rows.map(w => {
    const w_pct = Math.min(100, w.avg_occ);
    return '<div class="hbar-row">' +
      '<div class="hbar-label" title="' + w.workshop_name + '">' + w.workshop_name + '</div>' +
      '<div class="hbar-track"><div class="hbar-fill" style="width:' + w_pct + '%;background:' + color + '"></div>' +
      '<span class="hbar-pct">' + w.avg_occ + '%</span></div>' +
      '<div class="hbar-sessions">' + w.sessions + ' sess.</div>' +
    '</div>';
  }).join('');
}
renderHorizBars();

// ── Distribution chart ────────────────────────────────────────────────────
(function() {
  const labels = ['Volzet (100%)', 'Hoog (75-99%)', 'Midden (50-74%)', 'Laag (<50%)'];
  const colors = ['#e05252', '#4caf74', '#e8b32a', '#5a9fd4'];
  const locations = DATA.dist.map(d => d.location);
  const datasets = [
    { label: 'Volzet',  data: DATA.dist.map(d => d.volzet),  backgroundColor: '#e05252' },
    { label: 'Hoog',    data: DATA.dist.map(d => d.hoog),    backgroundColor: '#4caf74' },
    { label: 'Midden',  data: DATA.dist.map(d => d.midden),  backgroundColor: '#e8b32a' },
    { label: 'Laag',    data: DATA.dist.map(d => d.laag),    backgroundColor: '#5a9fd4' },
  ];
  new Chart(document.getElementById('distChart'), {
    type: 'bar',
    data: { labels: locations, datasets },
    options: {
      plugins: { legend: { labels: { color: '#9a8a7a', font: { family: 'Barlow Condensed', weight: '700', size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#9a8a7a' }, grid: { color: '#3a2e24' } },
        y: { stacked: true, ticks: { color: '#9a8a7a' }, grid: { color: '#3a2e24' }, beginAtZero: true }
      }
    }
  });
})();

// ── Monthly trend chart ───────────────────────────────────────────────────
(function() {
  const months = [...new Set(DATA.monthly.map(r => r.month))].sort();
  const locs   = [...new Set(DATA.monthly.map(r => r.location))];
  const datasets = locs.map(loc => ({
    label: loc,
    data: months.map(m => {
      const row = DATA.monthly.find(r => r.month === m && r.location === loc);
      return row ? row.avg_occ : null;
    }),
    borderColor: COLORS[loc] || '#9a8a7a',
    backgroundColor: (COLORS[loc] || '#9a8a7a') + '22',
    fill: false,
    tension: 0.3,
    pointRadius: 5,
    pointBackgroundColor: COLORS[loc] || '#9a8a7a',
    pointBorderColor: '#221c17',
    pointBorderWidth: 2,
    spanGaps: true,
  }));
  new Chart(document.getElementById('monthChart'), {
    type: 'line',
    data: { labels: months, datasets },
    options: {
      plugins: { legend: { labels: { color: '#9a8a7a', font: { family: 'Barlow Condensed', weight: '700', size: 11 } } } },
      scales: {
        x: { ticks: { color: '#9a8a7a' }, grid: { color: '#3a2e24' } },
        y: { min: 0, max: 100, ticks: { color: '#9a8a7a', callback: v => v + '%' }, grid: { color: '#3a2e24' } }
      }
    }
  });
})();

// ── Lead time chart ───────────────────────────────────────────────────────
(function() {
  const locs = [...new Set(DATA.lead_time.map(r => r.location))];
  // Build per-location: days_before -> avg_occ
  const allDays = [...new Set(DATA.lead_time.map(r => r.days_before))].sort((a,b) => b - a);
  if (!allDays.length) {
    document.getElementById('leadChart').parentElement.innerHTML += '<p style="color:var(--muted);padding:16px;font-family:\'Barlow Condensed\',sans-serif;text-transform:uppercase;letter-spacing:1px;font-size:12px">Onvoldoende snapshot data voor boekingscurve</p>';
    return;
  }
  const datasets = locs.map(loc => ({
    label: loc,
    data: allDays.map(d => {
      const row = DATA.lead_time.find(r => r.location === loc && r.days_before === d);
      return row ? row.avg_occ : null;
    }),
    borderColor: COLORS[loc] || '#9a8a7a',
    backgroundColor: (COLORS[loc] || '#9a8a7a') + '22',
    fill: false,
    tension: 0.3,
    pointRadius: 4,
    pointBackgroundColor: COLORS[loc] || '#9a8a7a',
    pointBorderColor: '#221c17',
    pointBorderWidth: 2,
    spanGaps: false,
  }));
  new Chart(document.getElementById('leadChart'), {
    type: 'line',
    data: { labels: allDays.map(d => d + ' dg'), datasets },
    options: {
      plugins: { legend: { labels: { color: '#9a8a7a', font: { family: 'Barlow Condensed', weight: '700', size: 11 } } } },
      scales: {
        x: { reverse: false, ticks: { color: '#9a8a7a', maxTicksLimit: 20 }, grid: { color: '#3a2e24' } },
        y: { min: 0, max: 100, ticks: { color: '#9a8a7a', callback: v => v + '%' }, grid: { color: '#3a2e24' } }
      }
    }
  });
})();

// ── Past sessions table ───────────────────────────────────────────────────
function badgeCls(pct) {
  if (pct >= 100) return ['badge-full',  'Volzet'];
  if (pct >= 75)  return ['badge-high',  'Hoog'];
  if (pct >= 50)  return ['badge-mid',   'Midden'];
  return ['badge-low', 'Laag'];
}

function populateFilters() {
  const locs = [...new Set(DATA.past_sessions.map(s => s.location))].sort();
  const wss  = [...new Set(DATA.past_sessions.map(s => s.workshop_name))].sort();
  const ls = document.getElementById('filterLoc2');
  locs.forEach(l => { const o = document.createElement('option'); o.value = l; o.textContent = l; ls.appendChild(o); });
  const ws = document.getElementById('filterWorkshop2');
  wss.forEach(w => { const o = document.createElement('option'); o.value = w; o.textContent = w; ws.appendChild(o); });
}

function renderPastTable() {
  const locF = document.getElementById('filterLoc2').value;
  const wsF  = document.getElementById('filterWorkshop2').value;
  let rows = DATA.past_sessions.filter(s => {
    if (locF && s.location !== locF) return false;
    if (wsF  && s.workshop_name !== wsF) return false;
    return true;
  });
  const tbody = document.querySelector('#pastTable tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px;font-family:\'Barlow Condensed\',sans-serif;text-transform:uppercase;letter-spacing:1px">Geen sessies gevonden</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(s => {
    const [cls, lbl] = badgeCls(s.occupancy_pct);
    const color = gaugeColor(s.occupancy_pct);
    const dateNl = new Date(s.session_date).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
    return '<tr>' +
      '<td>' + dateNl + '</td>' +
      '<td style="color:var(--muted)">' + s.session_time + '</td>' +
      '<td>' + s.workshop_name + '</td>' +
      '<td style="color:var(--muted)">' + s.location + '</td>' +
      '<td><span style="font-family:\'Barlow Condensed\',sans-serif;font-weight:800;font-size:15px">' + s.booked_spots + '</span></td>' +
      '<td style="color:var(--muted)">' + s.total_capacity + '</td>' +
      '<td><span style="font-family:\'Barlow Condensed\',sans-serif;font-weight:800;font-size:15px;color:' + color + '">' + s.occupancy_pct + '%</span></td>' +
      '<td><span class="badge ' + cls + '">' + lbl + '</span></td>' +
    '</tr>';
  }).join('');
}

populateFilters();
renderPastTable();
</script>

</body>
</html>"""


def run():
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} niet gevonden.")
        raise SystemExit(1)

    conn = get_conn()
    data = build_data(conn)

    # Snapshot stats for footer note
    snap_row = conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT snapshot_date) AS days FROM snapshots WHERE session_date < date('now')").fetchone()
    snap_count = snap_row["n"] if snap_row else 0
    snap_days  = snap_row["days"] if snap_row else 0
    conn.close()

    chartjs = get_chartjs()
    inline_script = f"<script>{chartjs}</script>"

    html = (
        HTML
        .replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>', inline_script)
        .replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
        .replace("{{SNAP_COUNT}}", str(snap_count))
        .replace("{{SNAP_DAYS}}", str(snap_days))
    )

    OUT_PATH.write_text(html, encoding="utf-8")

    print(f"Historisch dashboard gegenereerd: {OUT_PATH}")
    print(f"  Voltooide sessies: {data['totals']['n']}")
    print(f"  Gem. bezetting: {data['totals']['avg_occ']}%")
    print(f"  Locaties: {len(data['location_summary'])}")


if __name__ == "__main__":
    run()
