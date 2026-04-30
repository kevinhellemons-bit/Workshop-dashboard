"""
Reads .tmp/workshops.db and generates dashboard.html.
"""

import sqlite3
import json
import requests
from datetime import date, timedelta
from pathlib import Path

CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
CHARTJS_CACHE = Path(__file__).parent.parent / ".tmp" / "chartjs.min.js"

DB_PATH   = Path(__file__).parent.parent / ".tmp" / "workshops.db"
OUT_PATH  = Path(__file__).parent.parent / "dashboard.html"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_data(conn):
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    week_ago  = (date.today() - timedelta(days=7)).isoformat()
    in_6weeks = (date.today() + timedelta(days=42)).isoformat()
    in_21days = (date.today() + timedelta(days=21)).isoformat()

    # All upcoming sessions (today and future)
    sessions = query(conn, """
        SELECT workshop_name, location, country, session_date, session_time,
               available_spots, booked_spots, total_capacity, occupancy_pct, price
        FROM sessions
        WHERE session_date >= ?
        ORDER BY session_date, session_time, location
    """, (today,))

    # Summary per location
    location_summary = query(conn, """
        SELECT location, country,
               COUNT(*) AS total_sessions,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_capacity,
               ROUND(SUM(booked_spots) * 100.0 / SUM(total_capacity), 1) AS occupancy_pct,
               SUM(booked_spots * price) AS revenue_forecast
        FROM sessions
        WHERE session_date >= ?
        GROUP BY location, country
        ORDER BY occupancy_pct DESC
    """, (today,))

    # Daily delta per location (today vs yesterday snapshots)
    daily_delta = query(conn, """
        SELECT t.location, t.country,
               t.total_booked - COALESCE(y.total_booked, t.total_booked) AS delta_booked
        FROM (
            SELECT location, country, SUM(booked_spots) AS total_booked
            FROM snapshots WHERE snapshot_date = ?
            GROUP BY location, country
        ) t
        LEFT JOIN (
            SELECT location, country, SUM(booked_spots) AS total_booked
            FROM snapshots WHERE snapshot_date = ?
            GROUP BY location, country
        ) y ON t.location = y.location
    """, (today, yesterday))

    # Weekly delta per location (today vs 7 days ago)
    weekly_delta = query(conn, """
        SELECT t.location, t.country,
               t.total_booked - COALESCE(w.total_booked, t.total_booked) AS delta_booked,
               ROUND((t.total_booked - COALESCE(w.total_booked, t.total_booked)) * 100.0
                     / NULLIF(COALESCE(w.total_booked, 1), 0), 1) AS delta_pct
        FROM (
            SELECT location, country, SUM(booked_spots) AS total_booked
            FROM snapshots WHERE snapshot_date = ?
            GROUP BY location, country
        ) t
        LEFT JOIN (
            SELECT location, country, SUM(booked_spots) AS total_booked
            FROM snapshots WHERE snapshot_date = ?
            GROUP BY location, country
        ) w ON t.location = w.location
    """, (today, week_ago))

    # Urgency: upcoming sessions within 21 days with >7 tables free
    urgency = query(conn, """
        SELECT workshop_name, location, session_date, session_time,
               available_spots, occupancy_pct
        FROM sessions
        WHERE session_date >= ? AND session_date <= ?
          AND available_spots > 7
        ORDER BY session_date, session_time
    """, (today, in_21days))

    # Fully booked sessions
    full_sessions = query(conn, """
        SELECT COUNT(*) AS cnt FROM sessions
        WHERE session_date >= ? AND available_spots = 0
    """, (today,))

    # Trend data (last 14 days, total booked per day)
    trend = query(conn, """
        SELECT snapshot_date, SUM(booked_spots) AS total_booked
        FROM snapshots
        WHERE snapshot_date >= ?
        GROUP BY snapshot_date
        ORDER BY snapshot_date
    """, ((date.today() - timedelta(days=14)).isoformat(),))

    # Per-workshop per location: occupancy + popularity score
    # Popularity = 60% avg occupancy + 40% % of sessions fully booked
    per_workshop = query(conn, """
        SELECT workshop_name,
               location,
               country,
               COUNT(*) AS sessions,
               ROUND(AVG(occupancy_pct), 1) AS avg_occupancy,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_capacity,
               SUM(CASE WHEN available_spots = 0 THEN 1 ELSE 0 END) AS full_sessions,
               ROUND(
                   AVG(occupancy_pct) * 0.6
                   + SUM(CASE WHEN available_spots = 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) * 0.4
               , 1) AS popularity_score
        FROM sessions
        WHERE session_date >= ?
        GROUP BY workshop_name, location
        ORDER BY location, popularity_score DESC
    """, (today,))

    # Sessions in next 6 weeks with ALL spots still free (0 booked)
    empty_sessions = query(conn, """
        SELECT workshop_name, location, country, session_date, session_time
        FROM sessions
        WHERE session_date >= ? AND session_date <= ?
          AND available_spots = 10
        ORDER BY session_date, session_time, location
    """, (today, in_6weeks))

    # Best suggestion per location: highest popularity score workshop
    # that ALSO has availability (available_spots > 0) in the next 6 weeks
    suggestions = query(conn, """
        SELECT p.location, p.workshop_name, p.popularity_score, p.avg_occupancy,
               p.full_sessions, p.sessions,
               s.session_date, s.session_time, s.available_spots
        FROM (
            SELECT location, workshop_name,
                   ROUND(AVG(occupancy_pct)*0.6 + SUM(CASE WHEN available_spots=0 THEN 1 ELSE 0 END)*100.0/COUNT(*)*0.4, 1) AS popularity_score,
                   ROUND(AVG(occupancy_pct),1) AS avg_occupancy,
                   SUM(CASE WHEN available_spots=0 THEN 1 ELSE 0 END) AS full_sessions,
                   COUNT(*) AS sessions
            FROM sessions
            WHERE session_date >= ?
            GROUP BY location, workshop_name
        ) p
        JOIN sessions s ON s.workshop_name = p.workshop_name AND s.location = p.location
        WHERE s.session_date >= ? AND s.session_date <= ?
          AND s.available_spots > 0
        GROUP BY p.location, p.workshop_name
        ORDER BY p.location, p.popularity_score DESC
    """, (today, today, in_6weeks))

    # Keep only top suggestion per location
    seen_locs = set()
    top_suggestions = []
    for r in suggestions:
        if r["location"] not in seen_locs:
            seen_locs.add(r["location"])
            top_suggestions.append(r)

    return {
        "generated_at": date.today().isoformat(),
        "sessions": sessions,
        "location_summary": location_summary,
        "daily_delta": {r["location"]: r["delta_booked"] for r in daily_delta},
        "weekly_delta": {r["location"]: r for r in weekly_delta},
        "urgency": urgency,
        "full_sessions_count": full_sessions[0]["cnt"] if full_sessions else 0,
        "trend": trend,
        "per_workshop": per_workshop,
        "empty_sessions": empty_sessions,
        "suggestions": top_suggestions,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BBQ Experience Center – Bezettingsgraad Dashboard</title>
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
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Barlow', sans-serif; font-size: 14px; min-height: 100vh; }

  /* ── Header ── */
  header { background: var(--surface); border-bottom: 2px solid var(--orange); padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
  .logo-mark { width: 44px; height: 44px; background: var(--orange); display: flex; align-items: center; justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 16px; color: #fff; flex-shrink: 0; letter-spacing: 1px; }
  .logo-text h1 { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 22px; text-transform: uppercase; letter-spacing: 1px; line-height: 1; }
  .logo-text span { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 2px; }
  .header-right { margin-left: auto; text-align: right; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
  .header-right strong { display: block; font-family: 'Barlow Condensed', sans-serif; font-size: 16px; font-weight: 700; color: var(--text); letter-spacing: 0; text-transform: none; margin-top: 2px; }
  .refresh-btn { background: var(--orange); color: #fff; border: none; border-radius: 4px; padding: 5px 14px; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 12px; letter-spacing: .5px; text-transform: uppercase; cursor: pointer; }
  .refresh-btn:disabled { opacity: .6; cursor: default; }

  /* ── Layout ── */
  main { padding: 24px; max-width: 1400px; margin: 0 auto; }
  .section-label { font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; color: var(--muted); margin: 32px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }

  /* ── KPI Stats ── */
  .stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 24px; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 16px 18px; }
  .stat-val { font-family: 'Barlow Condensed', sans-serif; font-size: 32px; font-weight: 800; line-height: 1; }
  .stat-lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-top: 5px; }
  .stat.orange .stat-val { color: var(--orange); }
  .stat.green  .stat-val { color: var(--green); }
  .stat.yellow .stat-val { color: var(--yellow); }
  .stat.red    .stat-val { color: var(--red); }
  .stat.blue   .stat-val { color: var(--blue); }

  /* ── Location Cards ── */
  .loc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
  .loc-card { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 20px; }
  .loc-card h3 { font-family: 'Barlow Condensed', sans-serif; font-size: 18px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }
  .loc-card .country { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; }
  .gauge-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
  .gauge-bar { flex: 1; height: 8px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .gauge-fill { height: 100%; border-radius: 2px; transition: width .6s; }
  .pct-label { font-family: 'Barlow Condensed', sans-serif; font-size: 28px; font-weight: 800; min-width: 64px; text-align: right; line-height: 1; }
  .loc-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .loc-meta .item { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 10px 12px; }
  .loc-meta .item .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  .loc-meta .item strong { font-family: 'Barlow Condensed', sans-serif; font-size: 16px; font-weight: 700; }
  .delta-pos { color: var(--green); font-weight: 600; }
  .delta-neg { color: var(--red);   font-weight: 600; }
  .delta-zero { color: var(--muted); }

  /* ── Tables ── */
  .table-wrap { overflow-x: auto; }
  .table-container { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; }
  thead th { background: #2a211a; padding: 11px 14px; text-align: left; font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  tbody tr { border-bottom: 1px solid var(--border); transition: background 0.1s; }
  tbody tr:hover { background: rgba(232,114,42,0.07); }
  tbody tr:last-child { border-bottom: none; }
  td { padding: 11px 14px; vertical-align: middle; }

  /* ── Badges ── */
  .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 12px; font-family: 'Barlow Condensed', sans-serif; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }
  .badge-full   { background: rgba(224,82,82,0.15);   color: var(--red); }
  .badge-high   { background: rgba(76,175,116,0.15);  color: var(--green); }
  .badge-mid    { background: rgba(232,179,42,0.15);  color: var(--yellow); }
  .badge-low    { background: rgba(90,159,212,0.15);  color: var(--blue); }

  /* ── Filter buttons ── */
  .filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .filter-btn { padding: 6px 14px; font-size: 12px; border-radius: 12px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-family: 'Barlow Condensed', sans-serif; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; transition: all .15s; }
  .filter-btn.active { border-color: var(--orange); color: var(--orange); background: rgba(232,114,42,0.1); }
  .filter-btn:hover:not(.active) { border-color: var(--muted); color: var(--text); }
  select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 3px; padding: 7px 12px; font-size: 12px; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; cursor: pointer; outline: none; text-transform: uppercase; letter-spacing: 1px; }
  select:focus { border-color: var(--orange); }

  /* ── Urgency ── */
  .urgency-list { display: flex; flex-direction: column; gap: 6px; }
  .urgency-item { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--yellow); border-radius: 4px; padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }
  .urgency-item-top { display: flex; justify-content: space-between; align-items: center; }
  .urgency-item .name { font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 15px; text-transform: uppercase; letter-spacing: .5px; }
  .urgency-item .meta { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .urgency-item .spots { font-family: 'Barlow Condensed', sans-serif; font-size: 24px; font-weight: 800; color: var(--yellow); }

  /* ── Actiepunten ── */
  .action-btns { display: flex; gap: 6px; flex-wrap: wrap; }
  .action-btn { padding: 5px 14px; font-size: 11px; border-radius: 12px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-family: 'Barlow Condensed', sans-serif; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; transition: all .15s; }
  .action-btn:hover:not(.sel-marketing):not(.sel-klantenservice):not(.sel-retail) { border-color: var(--muted); color: var(--text); }
  .action-btn.sel-marketing     { border-color: var(--blue);   color: var(--blue);   background: rgba(90,159,212,0.12); }
  .action-btn.sel-klantenservice{ border-color: var(--red);    color: var(--red);    background: rgba(224,82,82,0.12); }
  .action-btn.sel-retail        { border-color: var(--green);  color: var(--green);  background: rgba(76,175,116,0.12); }

  /* ── Zwevende actiebalk ── */
  .action-bar { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: var(--surface); border: 1px solid var(--orange); border-radius: 8px; padding: 14px 20px; display: flex; align-items: center; gap: 16px; box-shadow: 0 4px 24px rgba(0,0,0,.5); z-index: 100; transition: opacity .2s, transform .2s; }
  .action-bar.hidden { opacity: 0; pointer-events: none; transform: translateX(-50%) translateY(12px); }
  .action-bar-count { font-family: 'Barlow Condensed', sans-serif; font-size: 14px; color: var(--muted); white-space: nowrap; }
  .action-bar-count strong { color: var(--orange); font-size: 20px; margin-right: 4px; }
  .action-bar-send { padding: 9px 22px; background: var(--orange); color: #fff; border: none; border-radius: 4px; font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; transition: background .15s; white-space: nowrap; }
  .action-bar-send:hover:not(:disabled) { background: var(--orange2); }
  .action-bar-send:disabled { opacity: .6; cursor: default; }
  .action-bar-clear { padding: 9px 14px; background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; }

  /* ── Chart ── */
  .chart-box { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 20px; }
  canvas { max-height: 260px; }

  /* ── Popularity tabs ── */
  .tab-row { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
  .tab-btn { padding: 6px 18px; font-size: 12px; border-radius: 12px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-family: 'Barlow Condensed', sans-serif; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; transition: all .15s; }
  .tab-btn.active { border-color: var(--orange); color: var(--orange); background: rgba(232,114,42,0.1); }
  .tab-btn:hover:not(.active) { border-color: var(--muted); color: var(--text); }

  /* ── Empty sessions ── */
  .empty-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 8px; }
  .empty-item { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--border); border-radius: 4px; padding: 12px 14px; }
  .empty-item .ename { font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: .5px; }
  .empty-item .emeta { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .empty-item .edays { font-size: 11px; margin-top: 4px; font-weight: 600; }
  .empty-summary { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }

  /* ── Suggestions ── */
  .sug-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
  .sug-card { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--orange); border-radius: 4px; padding: 16px 18px; }
  .sug-location { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--orange); font-family: 'Barlow Condensed', sans-serif; font-weight: 700; margin-bottom: 6px; }
  .sug-name { font-family: 'Barlow Condensed', sans-serif; font-weight: 800; font-size: 17px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 8px; }
  .sug-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 10px; }
  .sug-meta .si { background: var(--bg); border: 1px solid var(--border); border-radius: 3px; padding: 8px 10px; }
  .sug-meta .si .sk { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }
  .sug-meta .si strong { font-family: 'Barlow Condensed', sans-serif; font-size: 15px; font-weight: 700; }
  .sug-next { font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: 8px; margin-top: 4px; }
  .sug-next strong { color: var(--text); }

  /* ── Responsive ── */
  @media(max-width:900px) { .stats { grid-template-columns: repeat(3,1fr); } }
  @media(max-width:600px) { .stats { grid-template-columns: repeat(2,1fr); } header { flex-wrap: wrap; } main { padding: 16px; } }
</style>
</head>
<body>

<header>
  <div class="logo-mark">BXC</div>
  <div class="logo-text">
    <h1>BBQ Experience Center</h1>
    <span>Bezettingsgraad Dashboard &mdash; Workshops</span>
  </div>
  <div class="header-right">
    <div>Bijgewerkt op<strong id="genDate"></strong></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
      <a href="?page=history" style="font-family:'Barlow Condensed',sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);text-decoration:none;border:1px solid var(--border);border-radius:4px;padding:4px 12px;" onmouseover="this.style.color='var(--text)'" onmouseout="this.style.color='var(--muted)'">&#128202; Historisch</a>
      <button class="refresh-btn" id="refreshBtn" onclick="triggerRefresh()">&#8635; Vernieuwen</button>
    </div>
  </div>
</header>

<main>

  <!-- KPI Stats -->
  <div class="stats" id="kpiGrid"></div>

  <!-- Location cards -->
  <div class="section-label">Per Vestiging</div>
  <div class="loc-grid" id="locGrid"></div>

  <!-- Trend chart -->
  <div class="section-label">Boekingstrend — Laatste 14 Dagen</div>
  <div class="chart-box">
    <canvas id="trendChart"></canvas>
  </div>

  <!-- Workshop occupancy table -->
  <div class="section-label">Bezettingsgraad per Workshop</div>
  <div class="table-container">
    <div class="table-wrap">
      <table id="workshopTable">
        <thead>
          <tr>
            <th>Workshop</th>
            <th>Sessies</th>
            <th>Gem. bezetting</th>
            <th>Totaal geboekt</th>
            <th>Totaal tafels</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- Urgency -->
  <div class="section-label">Actie Nodig — Sessies &lt; 21 Dagen met &gt;7 Vrije Tafels</div>
  <div class="urgency-list" id="urgencyList"></div>

  <!-- Empty sessions 6 weeks -->
  <div class="section-label">Helemaal Leeg — Sessies Komende 6 Weken Zonder Boekingen</div>
  <div id="emptySummary" class="empty-summary"></div>
  <div class="empty-grid" id="emptyGrid"></div>

  <!-- Suggestions per location -->
  <div class="section-label">Aanbevolen Workshop per Vestiging &mdash; Beste Kans op Basis van Populariteit &amp; Beschikbaarheid</div>
  <div class="sug-grid" id="sugGrid"></div>

  <!-- Popularity ranking -->
  <div class="section-label">Populariteitsranking per Vestiging &mdash; Score = 60% Gem. Bezetting + 40% % Volzet</div>
  <div class="tab-row">
    <button onclick="showPopTab('Roosendaal')" id="tab-Roosendaal" class="tab-btn active">Roosendaal</button>
    <button onclick="showPopTab('Nunspeet')"   id="tab-Nunspeet"   class="tab-btn">Nunspeet</button>
    <button onclick="showPopTab('Herent')"     id="tab-Herent"     class="tab-btn">Herent</button>
  </div>
  <div class="table-container">
    <div class="table-wrap">
      <table id="popularityTable">
        <thead>
          <tr>
            <th>#</th><th>Workshop</th><th>Populariteit</th>
            <th>Score</th><th>Gem. bezetting</th><th>Volzet</th><th>Sessies</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- Session table -->
  <div class="section-label">Alle Aankomende Sessies</div>
  <div class="filter-row">
    <select id="filterLoc" onchange="renderTable()">
      <option value="">Alle locaties</option>
    </select>
    <select id="filterWorkshop" onchange="renderTable()">
      <option value="">Alle workshops</option>
    </select>
    <button class="filter-btn active" data-s="" onclick="setStatusFilter(this,'')">Alles</button>
    <button class="filter-btn" data-s="vol"    onclick="setStatusFilter(this,'vol')">Volzet</button>
    <button class="filter-btn" data-s="hoog"   onclick="setStatusFilter(this,'hoog')">Hoog &ge;75%</button>
    <button class="filter-btn" data-s="midden" onclick="setStatusFilter(this,'midden')">Midden 50-74%</button>
    <button class="filter-btn" data-s="laag"   onclick="setStatusFilter(this,'laag')">Laag &lt;50%</button>
  </div>
  <div class="table-container">
    <div class="table-wrap">
      <table id="sessionTable">
        <thead>
          <tr>
            <th>Datum</th><th>Tijd</th><th>Workshop</th><th>Locatie</th>
            <th>Tafels geboekt</th><th>Tafels vrij</th><th>Bezetting</th><th>Status</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

</main>

<!-- Zwevende actiebalk -->
<div class="action-bar hidden" id="actionBar">
  <div class="action-bar-count"><strong id="actionCount">0</strong> actiepunt(en) klaar</div>
  <button class="action-bar-clear" onclick="clearActions()">Wissen</button>
  <button class="action-bar-send" id="actionSendBtn" onclick="sendActions()">Verstuur naar Slack &rarr;</button>
</div>

<script>
const DATA = __DATA__;

document.getElementById('genDate').textContent = DATA.generated_at;

// ── KPI Grid ──────────────────────────────────────────────────────────────
function renderKPIs() {
  const sessions = DATA.sessions;
  const totalSessions = sessions.length;
  const totalBooked = sessions.reduce((s, r) => s + r.booked_spots, 0);
  const totalCap = sessions.reduce((s, r) => s + r.total_capacity, 0);
  const overallPct = totalCap ? (totalBooked / totalCap * 100).toFixed(1) : 0;
  const revForecast = sessions.reduce((s, r) => s + r.booked_spots * (r.price || 0), 0);
  const fullCount = DATA.full_sessions_count;

  const kpis = [
    { label: 'Totale bezettingsgraad', value: overallPct + '%', sub: `${totalBooked.toLocaleString('nl-NL')} / ${totalCap.toLocaleString('nl-NL')} tafels geboekt`, cls: overallPct >= 70 ? 'green' : overallPct >= 40 ? 'yellow' : 'blue' },
    { label: 'Aankomende sessies',     value: totalSessions,    sub: 'workshops gepland', cls: '' },
    { label: 'Volzet',                 value: fullCount,         sub: 'sessies volledig geboekt', cls: 'green' },
    { label: 'Omzetprognose',          value: '€' + Math.round(revForecast).toLocaleString('nl-NL'), sub: 'geboekte tafels × prijs', cls: 'orange' },
    { label: 'Actie nodig',            value: DATA.urgency.length, sub: '< 21 dagen, >7 tafels vrij', cls: DATA.urgency.length > 0 ? 'red' : 'green' },
  ];

  const grid = document.getElementById('kpiGrid');
  grid.innerHTML = kpis.map(k => `
    <div class="stat ${k.cls || ''}">
      <div class="stat-val">${k.value}</div>
      <div class="stat-lbl">${k.label}</div>
      <div style="font-size:11px;color:var(--muted);margin-top:3px">${k.sub}</div>
    </div>
  `).join('');
}

// ── Location Cards ────────────────────────────────────────────────────────
function gaugeColor(pct) {
  if (pct >= 80) return '#4caf74';
  if (pct >= 50) return '#e8b32a';
  return '#5a9fd4';
}

function deltaHtml(loc) {
  const d = DATA.daily_delta[loc];
  if (d === undefined || d === null) return '<span class="delta-zero">–</span>';
  if (d > 0) return `<span class="delta-pos">+${d}</span>`;
  if (d < 0) return `<span class="delta-neg">${d}</span>`;
  return '<span class="delta-zero">0</span>';
}

function weekDeltaHtml(loc) {
  const w = DATA.weekly_delta[loc];
  if (!w) return '<span class="delta-zero">–</span>';
  const d = w.delta_booked, p = w.delta_pct;
  if (d > 0) return `<span class="delta-pos">+${d} (+${p}%)</span>`;
  if (d < 0) return `<span class="delta-neg">${d} (${p}%)</span>`;
  return '<span class="delta-zero">0</span>';
}

function renderLocations() {
  const grid = document.getElementById('locGrid');
  grid.innerHTML = DATA.location_summary.map(loc => {
    const pct = loc.occupancy_pct || 0;
    const color = gaugeColor(pct);
    const rev = '€' + Math.round(loc.revenue_forecast || 0).toLocaleString('nl-NL');
    return `
      <div class="loc-card">
        <h3>${loc.location}</h3>
        <div class="country">${loc.country} &mdash; ${loc.total_sessions} sessies</div>
        <div class="gauge-wrap">
          <div class="gauge-bar"><div class="gauge-fill" style="width:${pct}%;background:${color}"></div></div>
          <div class="pct-label" style="color:${color}">${pct}%</div>
        </div>
        <div class="loc-meta">
          <div class="item"><div class="k">Geboekt</div><strong>${loc.total_booked} / ${loc.total_capacity}</strong></div>
          <div class="item"><div class="k">Omzetprognose</div><strong>${rev}</strong></div>
          <div class="item"><div class="k">+/- Vandaag</div>${deltaHtml(loc.location)}</div>
          <div class="item"><div class="k">+/- Week</div>${weekDeltaHtml(loc.location)}</div>
        </div>
      </div>
    `;
  }).join('');
}

// ── Trend Chart ───────────────────────────────────────────────────────────
function renderTrend() {
  const labels = DATA.trend.map(r => r.snapshot_date);
  const values = DATA.trend.map(r => r.total_booked);
  new Chart(document.getElementById('trendChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Totaal geboekte tafels',
        data: values,
        borderColor: '#e8722a',
        backgroundColor: 'rgba(232,114,42,0.12)',
        fill: true,
        tension: 0.4,
        pointRadius: 5,
        pointBackgroundColor: '#e8722a',
        pointBorderColor: '#221c17',
        pointBorderWidth: 2,
      }]
    },
    options: {
      plugins: { legend: { labels: { color: '#9a8a7a', font: { family: 'Barlow Condensed', weight: '700', size: 12 } } } },
      scales: {
        x: { ticks: { color: '#9a8a7a' }, grid: { color: '#3a2e24' } },
        y: { ticks: { color: '#9a8a7a' }, grid: { color: '#3a2e24' }, beginAtZero: true }
      }
    }
  });
}

// ── Workshop Table ─────────────────────────────────────────────────────────
function renderWorkshopTable() {
  const tbody = document.querySelector('#workshopTable tbody');
  const seen = {};
  const rows = DATA.per_workshop.filter(w => {
    if (seen[w.workshop_name]) return false;
    seen[w.workshop_name] = true; return true;
  });
  tbody.innerHTML = rows.map(w => {
    const color = gaugeColor(w.avg_occupancy);
    return `<tr>
      <td>${w.workshop_name}</td>
      <td style="color:var(--muted)">${w.sessions}</td>
      <td><span style="font-family:'Barlow Condensed',sans-serif;font-size:16px;font-weight:800;color:${color}">${w.avg_occupancy}%</span></td>
      <td>${w.total_booked}</td>
      <td style="color:var(--muted)">${w.total_capacity}</td>
    </tr>`;
  }).join('');
}

// ── Popularity Ranking ────────────────────────────────────────────────────
function starsHtml(score) {
  const filled = Math.round(score / 20); // 0-5 stars
  return '★'.repeat(filled) + '☆'.repeat(5 - filled);
}

function popularityColor(score) {
  if (score >= 75) return '#4caf74';
  if (score >= 50) return '#e8b32a';
  if (score >= 25) return '#5a9fd4';
  return '#9a8a7a';
}

let activePopTab = 'Roosendaal';

function showPopTab(loc) {
  activePopTab = loc;
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.getElementById('tab-' + loc).classList.add('active');
  renderPopularity();
}

function renderPopularity() {
  const tbody = document.querySelector('#popularityTable tbody');
  const rows = DATA.per_workshop
    .filter(w => w.location === activePopTab)
    .sort((a, b) => (b.popularity_score || 0) - (a.popularity_score || 0));

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Geen data voor ${activePopTab}</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map((w, i) => {
    const score = w.popularity_score || 0;
    const color = popularityColor(score);
    const fullPct = w.sessions ? Math.round(w.full_sessions / w.sessions * 100) : 0;
    return `<tr>
      <td style="color:var(--muted);font-family:'Barlow Condensed',sans-serif;font-weight:700">${i + 1}</td>
      <td style="font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:14px;text-transform:uppercase;letter-spacing:.5px">${w.workshop_name}</td>
      <td style="color:#e8b32a;letter-spacing:3px;font-size:14px">${starsHtml(score)}</td>
      <td><span style="font-family:'Barlow Condensed',sans-serif;font-size:20px;font-weight:800;color:${color}">${score}</span><span style="color:var(--muted);font-size:11px"> /100</span></td>
      <td><span style="font-family:'Barlow Condensed',sans-serif;font-weight:700;color:${gaugeColor(w.avg_occupancy)}">${w.avg_occupancy}%</span></td>
      <td style="color:var(--muted)">${fullPct}% (${w.full_sessions}/${w.sessions})</td>
      <td style="color:var(--muted)">${w.sessions}</td>
    </tr>`;
  }).join('');
}

// ── Empty Sessions (6 weeks) ──────────────────────────────────────────────
function renderEmptySessions() {
  const grid = document.getElementById('emptyGrid');
  const summary = document.getElementById('emptySummary');
  const items = DATA.empty_sessions;

  summary.textContent = `${items.length} sessie${items.length !== 1 ? 's' : ''} in de komende 6 weken hebben nog geen enkele boeking (0/10 tafels bezet).`;

  if (!items.length) {
    grid.innerHTML = `<p style="color:var(--muted);padding:20px 0;font-family:'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:1px;font-size:13px">Geen volledig lege sessies</p>`;
    return;
  }

  grid.innerHTML = items.map(s => {
    const dateNl = new Date(s.session_date).toLocaleDateString('nl-NL', { weekday: 'short', day: 'numeric', month: 'short' });
    const daysUntil = Math.round((new Date(s.session_date) - new Date()) / 86400000);
    const urgColor = daysUntil <= 14 ? 'var(--red)' : daysUntil <= 28 ? 'var(--yellow)' : 'var(--muted)';
    return `
      <div class="empty-item">
        <div class="ename">${s.workshop_name}</div>
        <div class="emeta">${dateNl} &mdash; ${s.session_time} &mdash; ${s.location} (${s.country})</div>
        <div class="edays" style="color:${urgColor}">Over ${daysUntil} dagen</div>
        ${actionBtnsHtml(s)}
      </div>
    `;
  }).join('');
}

// ── Session Table ─────────────────────────────────────────────────────────
function badgeForPct(pct) {
  if (pct >= 100) return ['vol',   'badge-full', 'Volzet'];
  if (pct >=  75) return ['hoog',  'badge-high', 'Hoog'];
  if (pct >=  50) return ['midden','badge-mid',  'Midden'];
  return ['laag', 'badge-low', 'Laag'];
}

// ── Vernieuwen via GitHub Actions ─────────────────────────────────────────
function triggerRefresh() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = 'Bezig\u2026';
  google.script.run
    .withSuccessHandler(function(result) {
      btn.disabled = false;
      btn.innerHTML = '&#8635; Vernieuwen';
      if (result && result.ok) {
        showToast('\u2713 Update gestart — duurt ca. 2 minuten');
      } else {
        showToast('\u26a0 ' + (result ? result.error : 'Geen reactie'), true);
      }
    })
    .withFailureHandler(function(err) {
      btn.disabled = false;
      btn.innerHTML = '&#8635; Vernieuwen';
      showToast('\u26a0 Fout: ' + err.message, true);
    })
    .triggerWorkflow();
}

// ── Populate filter dropdowns dynamically ─────────────────────────────────
function populateFilters() {
  const locs = [...new Set(DATA.sessions.map(s => s.location))].sort();
  const workshops = [...new Set(DATA.sessions.map(s => s.workshop_name))].sort();
  const locSel = document.getElementById('filterLoc');
  locs.forEach(l => { const o = document.createElement('option'); o.value = l; o.textContent = l; locSel.appendChild(o); });
  const wsSel = document.getElementById('filterWorkshop');
  workshops.forEach(w => { const o = document.createElement('option'); o.value = w; o.textContent = w; wsSel.appendChild(o); });
}

let activeStatusFilter = '';
function setStatusFilter(btn, val) {
  activeStatusFilter = val;
  document.querySelectorAll('.filter-btn[data-s]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderTable();
}

function renderTable() {
  const locFilter      = document.getElementById('filterLoc').value;
  const workshopFilter = document.getElementById('filterWorkshop').value;
  const statusFilter   = activeStatusFilter;

  let rows = DATA.sessions.filter(s => {
    if (locFilter && s.location !== locFilter) return false;
    if (workshopFilter && s.workshop_name !== workshopFilter) return false;
    if (statusFilter) {
      const [key] = badgeForPct(s.occupancy_pct);
      if (key !== statusFilter) return false;
    }
    return true;
  });

  const tbody = document.querySelector('#sessionTable tbody');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:32px;font-family:'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:1px">Geen sessies gevonden</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(s => {
    const [, badgeCls, label] = badgeForPct(s.occupancy_pct);
    const dateNl = new Date(s.session_date).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
    const color  = gaugeColor(s.occupancy_pct);
    return `<tr>
      <td>${dateNl}</td>
      <td style="color:var(--muted)">${s.session_time}</td>
      <td>${s.workshop_name}</td>
      <td style="color:var(--muted)">${s.location}</td>
      <td><span style="font-family:'Barlow Condensed',sans-serif;font-weight:800;font-size:15px">${s.booked_spots}</span></td>
      <td style="color:var(--muted)">${s.available_spots}</td>
      <td><span style="font-family:'Barlow Condensed',sans-serif;font-weight:800;font-size:15px;color:${color}">${s.occupancy_pct}%</span></td>
      <td><span class="badge ${badgeCls}">${label}</span></td>
    </tr>`;
  }).join('');
}

// ── Urgency List ──────────────────────────────────────────────────────────
function renderUrgency() {
  const el = document.getElementById('urgencyList');
  if (!DATA.urgency.length) {
    el.innerHTML = `<p style="color:var(--muted);padding:20px 0;font-family:'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:1px;font-size:13px">Geen urgente sessies</p>`;
    return;
  }
  el.innerHTML = DATA.urgency.map(u => {
    const dateNl = new Date(u.session_date).toLocaleDateString('nl-NL', { weekday: 'short', day: 'numeric', month: 'short' });
    return `
      <div class="urgency-item">
        <div class="urgency-item-top">
          <div>
            <div class="name">${u.workshop_name}</div>
            <div class="meta">${dateNl} &mdash; ${u.session_time} &mdash; ${u.location}</div>
          </div>
          <div class="spots">${u.available_spots} <span style="font-size:13px;color:var(--muted)">tafels vrij</span></div>
        </div>
        ${actionBtnsHtml(u)}
      </div>
    `;
  }).join('');
}

// ── Suggestions ───────────────────────────────────────────────────────────
function renderSuggestions() {
  const grid = document.getElementById('sugGrid');
  const items = DATA.suggestions || [];
  if (!items.length) {
    grid.innerHTML = `<p style="color:var(--muted);padding:20px 0;font-family:'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:1px;font-size:13px">Onvoldoende data voor aanbevelingen</p>`;
    return;
  }
  grid.innerHTML = items.map(s => {
    const score = s.popularity_score || 0;
    const color = popularityColor(score);
    const fullPct = s.sessions ? Math.round(s.full_sessions / s.sessions * 100) : 0;
    const nextDate = new Date(s.session_date).toLocaleDateString('nl-NL', { weekday: 'short', day: 'numeric', month: 'long' });
    return `
      <div class="sug-card">
        <div class="sug-location">${s.location}</div>
        <div class="sug-name">${s.workshop_name}</div>
        <div class="sug-meta">
          <div class="si"><div class="sk">Populariteitsscore</div><strong style="color:${color}">${score} <span style="font-size:11px;color:var(--muted);font-weight:400">/100</span></strong></div>
          <div class="si"><div class="sk">Gem. bezetting</div><strong style="color:${gaugeColor(s.avg_occupancy)}">${s.avg_occupancy}%</strong></div>
          <div class="si"><div class="sk">Sessies volzet</div><strong>${fullPct}%</strong></div>
          <div class="si"><div class="sk">Tafels vrij</div><strong style="color:var(--green)">${s.available_spots}</strong></div>
        </div>
        <div class="sug-next">Eerstvolgende beschikbare sessie: <strong>${nextDate} om ${s.session_time}</strong></div>
      </div>
    `;
  }).join('');
}

// ── Actiepunten ───────────────────────────────────────────────────────
const ACTIONS = [
  { id: 'marketing',      label: 'Marketing Push',           cls: 'sel-marketing',      emoji: '[M]' },
  { id: 'klantenservice', label: 'Annuleren',                cls: 'sel-klantenservice', emoji: '[K]' },
  { id: 'retail',         label: 'Andere Workshop in Plaats',cls: 'sel-retail',         emoji: '[R]' },
];

const sessionStore = [];          // indexed array of session objects
const pending = new Map();        // idx → Set of action IDs

function registerSession(s) {
  sessionStore.push(s);
  return sessionStore.length - 1;
}

function actionBtnsHtml(s) {
  const idx = registerSession(s);
  return '<div class="action-btns">' + ACTIONS.map(function(a) {
    return '<button class="action-btn" data-idx="' + idx + '" data-action="' + a.id + '" onclick="toggleAction(this)">' + a.label + '</button>';
  }).join('') + '</div>';
}

function toggleAction(btn) {
  const idx = parseInt(btn.dataset.idx, 10);
  const actionId = btn.dataset.action;
  const act = ACTIONS.find(function(a) { return a.id === actionId; });
  if (!pending.has(idx)) pending.set(idx, new Set());
  const set = pending.get(idx);
  if (set.has(actionId)) {
    set.delete(actionId);
    btn.classList.remove(act.cls);
    if (set.size === 0) pending.delete(idx);
  } else {
    set.add(actionId);
    btn.classList.add(act.cls);
  }
  updateBar();
}

function updateBar() {
  const total = [...pending.values()].reduce((s, v) => s + v.size, 0);
  document.getElementById('actionCount').textContent = total;
  document.getElementById('actionBar').classList.toggle('hidden', total === 0);
}

function clearActions() {
  pending.clear();
  document.querySelectorAll('.action-btn').forEach(b =>
    ACTIONS.forEach(a => b.classList.remove(a.cls))
  );
  updateBar();
}

function sendActions() {
  const payload = [];
  pending.forEach((set, idx) => {
    const s = sessionStore[idx];
    if (!s) return;
    set.forEach(actionId => {
      const act = ACTIONS.find(a => a.id === actionId);
      payload.push({
        workshop: s.workshop_name, location: s.location,
        date: s.session_date, time: s.session_time,
        available_spots: s.available_spots || 0,
        action_id: actionId, action_label: act.label, action_emoji: act.emoji,
      });
    });
  });
  if (!payload.length) return;

  const btn = document.getElementById('actionSendBtn');
  btn.disabled = true;
  btn.textContent = 'Versturen\u2026';

  google.script.run
    .withSuccessHandler(function(result) {
      btn.disabled = false;
      btn.textContent = 'Verstuur naar Slack \u2192';
      if (result && result.ok && result.sent > 0) {
        clearActions();
        showToast('\u2713 ' + result.sent + ' actiepunt' + (result.sent !== 1 ? 'en' : '') + ' verstuurd naar Slack');
      } else if (result && result.ok && result.missing && result.missing.length > 0) {
        showToast('\u26a0 SLACK_BOT_TOKEN ontbreekt in Apps Script (Projectinstellingen \u2192 Script-eigenschappen)', true);
      } else if (result && !result.ok) {
        showToast('\u26a0 Fout: ' + (result.error || 'onbekend'), true);
      } else {
        showToast('\u26a0 Geen reactie van Apps Script', true);
      }
    })
    .withFailureHandler(function(err) {
      btn.disabled = false;
      btn.textContent = 'Verstuur naar Slack \u2192';
      showToast('\u26a0 Fout: ' + err.message, true);
    })
    .sendSlackActions(JSON.stringify({ actions: payload }));
}

function showToast(msg, isError = false) {
  const t = document.createElement('div');
  t.style.cssText = `position:fixed;top:24px;right:24px;background:${isError ? 'var(--red)' : 'var(--green)'};color:#fff;padding:12px 20px;border-radius:4px;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:14px;z-index:200;letter-spacing:.5px;box-shadow:0 4px 12px rgba(0,0,0,.3)`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ── Init ──────────────────────────────────────────────────────────────────
try {
  populateFilters();
  renderKPIs();
  renderLocations();
  renderTrend();
  renderWorkshopTable();
  renderTable();
  renderUrgency();
  renderEmptySessions();
  renderSuggestions();
  renderPopularity();
} catch(e) {
  document.body.insertAdjacentHTML('afterbegin',
    '<div style="background:#e05252;color:#fff;padding:16px 24px;font-family:monospace;font-size:13px;position:fixed;top:0;left:0;right:0;z-index:999">JS FOUT: ' + e.message + ' — ' + e.stack + '</div>'
  );
}
</script>
</body>
</html>"""


def get_chartjs() -> str:
    """Return Chart.js source — from local cache if available, else download."""
    if CHARTJS_CACHE.exists():
        return CHARTJS_CACHE.read_text(encoding="utf-8")
    print("  Chart.js downloaden...", end=" ", flush=True)
    js = requests.get(CHARTJS_CDN, timeout=30).text
    CHARTJS_CACHE.write_text(js, encoding="utf-8")
    print("klaar.")
    return js


def run():
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} niet gevonden. Eerst update_database.py uitvoeren.")
        raise SystemExit(1)

    conn = get_conn()
    data = build_data(conn)
    conn.close()

    chartjs = get_chartjs()
    inline_script = f"<script>{chartjs}</script>"

    html = (
        HTML_TEMPLATE
        .replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>', inline_script)
        .replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
    )

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard gegenereerd: {OUT_PATH}")
    print(f"  Sessies: {len(data['sessions'])}, Urgentie: {len(data['urgency'])}, Leeg (6w): {len(data['empty_sessions'])}")
    print(f"  Zelfstandig HTML (geen internet nodig): {OUT_PATH}")


if __name__ == "__main__":
    run()
