"""
Genereert een statische HTML email digest (geen JavaScript) en slaat op als .tmp/email_digest.html.
Geschikt voor directe verzending via Gmail – volledig leesbaar zonder bijlage te downloaden.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH  = Path(__file__).parent.parent / ".tmp" / "workshops.db"
OUT_PATH = Path(__file__).parent.parent / ".tmp" / "email_digest.html"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def gauge_color(pct):
    if pct >= 75: return "#2e7d32"
    if pct >= 50: return "#f57c00"
    return "#1565c0"


def delta_html(d):
    if d is None: return '<span style="color:#999">–</span>'
    if d > 0:     return f'<span style="color:#2e7d32;font-weight:600">+{d}</span>'
    if d < 0:     return f'<span style="color:#c62828;font-weight:600">{d}</span>'
    return '<span style="color:#999">0</span>'


def build_data(conn):
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    week_ago  = (date.today() - timedelta(days=7)).isoformat()
    in_6weeks = (date.today() + timedelta(days=42)).isoformat()
    in_14days = (date.today() + timedelta(days=14)).isoformat()

    sessions = query(conn, """
        SELECT workshop_name, location, country, session_date, session_time,
               available_spots, booked_spots, total_capacity, occupancy_pct, price
        FROM sessions WHERE session_date >= ? ORDER BY session_date, session_time, location
    """, (today,))

    location_summary = query(conn, """
        SELECT location, country,
               COUNT(*) AS total_sessions,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_capacity,
               ROUND(SUM(booked_spots)*100.0/SUM(total_capacity),1) AS occupancy_pct,
               SUM(booked_spots*price) AS revenue_forecast
        FROM sessions WHERE session_date >= ?
        GROUP BY location, country ORDER BY occupancy_pct DESC
    """, (today,))

    daily_delta = {r["location"]: r["delta_booked"] for r in query(conn, """
        SELECT t.location, t.total_booked - COALESCE(y.total_booked, t.total_booked) AS delta_booked
        FROM (SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location) t
        LEFT JOIN (SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location) y
        ON t.location=y.location
    """, (today, yesterday))}

    weekly_delta = {r["location"]: r for r in query(conn, """
        SELECT t.location,
               t.total_booked - COALESCE(w.total_booked, t.total_booked) AS delta_booked,
               ROUND((t.total_booked - COALESCE(w.total_booked, t.total_booked))*100.0/NULLIF(COALESCE(w.total_booked,1),0),1) AS delta_pct
        FROM (SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location) t
        LEFT JOIN (SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location) w
        ON t.location=w.location
    """, (today, week_ago))}

    urgency = query(conn, """
        SELECT workshop_name, location, session_date, session_time, available_spots, occupancy_pct
        FROM sessions WHERE session_date >= ? AND session_date <= ? AND available_spots > 10
        ORDER BY session_date, session_time
    """, (today, in_14days))

    empty_sessions = query(conn, """
        SELECT workshop_name, location, country, session_date, session_time
        FROM sessions WHERE session_date >= ? AND session_date <= ? AND available_spots = 20
        ORDER BY session_date, session_time, location
    """, (today, in_6weeks))

    suggestions_raw = query(conn, """
        SELECT p.location, p.workshop_name, p.popularity_score, p.avg_occupancy,
               p.full_sessions, p.sessions,
               s.session_date, s.session_time, s.available_spots
        FROM (
            SELECT location, workshop_name,
                   ROUND(AVG(occupancy_pct)*0.6+SUM(CASE WHEN available_spots=0 THEN 1 ELSE 0 END)*100.0/COUNT(*)*0.4,1) AS popularity_score,
                   ROUND(AVG(occupancy_pct),1) AS avg_occupancy,
                   SUM(CASE WHEN available_spots=0 THEN 1 ELSE 0 END) AS full_sessions,
                   COUNT(*) AS sessions
            FROM sessions WHERE session_date >= ? GROUP BY location, workshop_name
        ) p
        JOIN sessions s ON s.workshop_name=p.workshop_name AND s.location=p.location
        WHERE s.session_date >= ? AND s.session_date <= ? AND s.available_spots > 0
        GROUP BY p.location, p.workshop_name
        ORDER BY p.location, p.popularity_score DESC
    """, (today, today, in_6weeks))

    seen = set()
    suggestions = []
    for r in suggestions_raw:
        if r["location"] not in seen:
            seen.add(r["location"])
            suggestions.append(r)

    total_booked = sum(s["booked_spots"] for s in sessions)
    total_cap    = sum(s["total_capacity"] for s in sessions)
    overall_pct  = round(total_booked / total_cap * 100, 1) if total_cap else 0
    full_count   = sum(1 for s in sessions if s["available_spots"] == 0)

    return {
        "today": today,
        "sessions": sessions,
        "location_summary": location_summary,
        "daily_delta": daily_delta,
        "weekly_delta": weekly_delta,
        "urgency": urgency,
        "empty_sessions": empty_sessions,
        "suggestions": suggestions,
        "overall_pct": overall_pct,
        "total_booked": total_booked,
        "total_cap": total_cap,
        "full_count": full_count,
    }


def render_stat(label, value, color="#e8722a"):
    return f"""
    <td style="padding:12px 16px;text-align:center;border-right:1px solid #eee">
      <div style="font-size:26px;font-weight:800;color:{color};line-height:1">{value}</div>
      <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-top:4px">{label}</div>
    </td>"""


def render_location_card(loc, daily_delta, weekly_delta):
    pct   = loc["occupancy_pct"] or 0
    color = gauge_color(pct)
    dd    = daily_delta.get(loc["location"])
    wd    = weekly_delta.get(loc["location"], {})
    rev   = f"€{round(loc['revenue_forecast'] or 0):,}".replace(",", ".")
    bar_w = min(int(pct), 100)

    wd_txt = "–"
    if wd:
        d, p = wd.get("delta_booked"), wd.get("delta_pct")
        if d and d > 0:   wd_txt = f'<span style="color:#2e7d32;font-weight:600">+{d} (+{p}%)</span>'
        elif d and d < 0: wd_txt = f'<span style="color:#c62828;font-weight:600">{d} ({p}%)</span>'
        else:              wd_txt = '<span style="color:#999">0</span>'

    return f"""
    <td style="width:33%;padding:8px;vertical-align:top">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;border-top:3px solid {color}">
        <tr><td style="padding:14px 16px">
          <div style="font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#1a1a1a">{loc["location"]}</div>
          <div style="font-size:11px;color:#999;margin-bottom:10px">{loc["country"]} &mdash; {loc["total_sessions"]} sessies</div>
          <!-- gauge -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px">
            <tr>
              <td style="padding-right:10px">
                <div style="height:8px;background:#eee;border-radius:4px;overflow:hidden">
                  <div style="height:8px;width:{bar_w}%;background:{color};border-radius:4px"></div>
                </div>
              </td>
              <td style="width:52px;text-align:right;font-size:22px;font-weight:800;color:{color};white-space:nowrap">{pct}%</td>
            </tr>
          </table>
          <!-- meta -->
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="width:50%;padding:4px 6px 4px 0;font-size:11px;color:#666">Geboekt</td>
              <td style="width:50%;padding:4px 0 4px 6px;font-size:11px;color:#666">Omzetprognose</td>
            </tr>
            <tr>
              <td style="padding:0 6px 8px 0;font-size:13px;font-weight:700;color:#1a1a1a">{loc["total_booked"]} / {loc["total_capacity"]}</td>
              <td style="padding:0 0 8px 6px;font-size:13px;font-weight:700;color:#1a1a1a">{rev}</td>
            </tr>
            <tr>
              <td style="padding:4px 6px 0 0;font-size:11px;color:#666">+/- Vandaag</td>
              <td style="padding:4px 0 0 6px;font-size:11px;color:#666">+/- Week</td>
            </tr>
            <tr>
              <td style="padding:2px 6px 0 0;font-size:13px;font-weight:600">{delta_html(dd)}</td>
              <td style="padding:2px 0 0 6px;font-size:13px">{wd_txt}</td>
            </tr>
          </table>
        </td></tr>
      </table>
    </td>"""


MONTHS_NL = ["jan","feb","mrt","apr","mei","jun","jul","aug","sep","okt","nov","dec"]
MONTHS_NL_FULL = ["januari","februari","maart","april","mei","juni",
                  "juli","augustus","september","oktober","november","december"]

def fmt_date(iso, full=False):
    try:
        d = date.fromisoformat(iso)
        m = MONTHS_NL_FULL[d.month - 1] if full else MONTHS_NL[d.month - 1]
        return f"{d.day} {m}"
    except Exception:
        return iso

def render_html(data):
    d = date.today()
    today_nl = f"{d.day} {MONTHS_NL_FULL[d.month-1]} {d.year}"

    # ── Stats row ──
    overall_color = "#2e7d32" if data["overall_pct"] >= 70 else "#f57c00" if data["overall_pct"] >= 40 else "#1565c0"
    stats_row = (
        render_stat("Totale bezetting", f"{data['overall_pct']}%", overall_color) +
        render_stat("Aankomende sessies", len(data["sessions"]), "#555") +
        render_stat("Volzet", data["full_count"], "#2e7d32") +
        render_stat("Actie nodig", len(data["urgency"]), "#c62828" if data["urgency"] else "#2e7d32")
    )

    # ── Location cards ──
    loc_cards = "".join(render_location_card(loc, data["daily_delta"], data["weekly_delta"])
                        for loc in data["location_summary"])

    # ── Suggestions ──
    sug_rows = ""
    for s in data["suggestions"]:
        score = s["popularity_score"] or 0
        color = gauge_color(s["avg_occupancy"])
        next_nl = fmt_date(s["session_date"])
        sug_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#1a1a1a">{s["location"]}</td>
          <td style="padding:10px 14px;font-size:13px;color:#333">{s["workshop_name"]}</td>
          <td style="padding:10px 14px;font-size:15px;font-weight:800;color:{color};text-align:center">{score}</td>
          <td style="padding:10px 14px;font-size:13px;color:#555;text-align:center">{s["avg_occupancy"]}%</td>
          <td style="padding:10px 14px;font-size:13px;color:#555">{next_nl} &mdash; {s["session_time"]}</td>
        </tr>"""

    # ── Urgency ──
    urg_rows = ""
    for u in data["urgency"]:
        try:
            dt = date.fromisoformat(u["session_date"])
            days = (dt - date.today()).days
        except Exception:
            dt = None
            days = 0
        date_nl = fmt_date(u["session_date"])
        color = "#c62828" if days <= 7 else "#f57c00"
        urg_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px;font-size:13px;font-weight:700;color:#1a1a1a">{u["workshop_name"]}</td>
          <td style="padding:10px 14px;font-size:12px;color:#666">{date_nl} &mdash; {u["session_time"]}</td>
          <td style="padding:10px 14px;font-size:12px;color:#666">{u["location"]}</td>
          <td style="padding:10px 14px;font-size:15px;font-weight:800;color:{color};text-align:center">{u["available_spots"]} vrij</td>
          <td style="padding:10px 14px;font-size:12px;color:{color};text-align:center">over {days}d</td>
        </tr>"""
    if not urg_rows:
        urg_rows = '<tr><td colspan="5" style="padding:14px;color:#999;text-align:center;font-size:13px">Geen urgente sessies</td></tr>'

    # ── Empty sessions ──
    empty_rows = ""
    for s in data["empty_sessions"]:
        try:
            days = (date.fromisoformat(s["session_date"]) - date.today()).days
        except Exception:
            days = 0
        date_nl = fmt_date(s["session_date"])
        color = "#c62828" if days <= 14 else "#f57c00" if days <= 28 else "#888"
        empty_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:10px 14px;font-size:13px;font-weight:700;color:#1a1a1a">{s["workshop_name"]}</td>
          <td style="padding:10px 14px;font-size:12px;color:#666">{date_nl} &mdash; {s["session_time"]}</td>
          <td style="padding:10px 14px;font-size:12px;color:#666">{s["location"]} ({s["country"]})</td>
          <td style="padding:10px 14px;font-size:12px;color:{color};font-weight:600;text-align:center">over {days}d</td>
        </tr>"""
    if not empty_rows:
        empty_rows = '<tr><td colspan="4" style="padding:14px;color:#999;text-align:center;font-size:13px">Geen volledig lege sessies</td></tr>'

    section_style = "font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#888;padding:20px 0 8px;border-bottom:2px solid #e8722a;margin-bottom:0"
    th_style = "padding:9px 14px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#888;background:#f9f9f9;border-bottom:1px solid #e0e0e0;text-align:left"

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BBQ Experience Center – Bezettingsgraad {data['today']}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:20px 0">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%">

  <!-- Header -->
  <tr><td style="background:#18130f;border-bottom:3px solid #e8722a;padding:18px 24px;border-radius:6px 6px 0 0">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="width:44px;height:44px;background:#e8722a;text-align:center;vertical-align:middle;border-radius:3px">
          <span style="font-size:14px;font-weight:800;color:#fff;letter-spacing:1px">BXC</span>
        </td>
        <td style="padding-left:14px">
          <div style="font-size:18px;font-weight:800;color:#fff;text-transform:uppercase;letter-spacing:1px;line-height:1">BBQ Experience Center</div>
          <div style="font-size:11px;color:#9a8a7a;text-transform:uppercase;letter-spacing:2px;margin-top:3px">Bezettingsgraad &mdash; {today_nl}</div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- Stats -->
  <tr><td style="background:#fff;border-bottom:1px solid #eee">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>{stats_row}</tr>
    </table>
  </td></tr>

  <!-- Per vestiging -->
  <tr><td style="background:#fff;padding:0 16px 16px">
    <div style="{section_style}">Per Vestiging</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px">
      <tr>{loc_cards}</tr>
    </table>
  </td></tr>

  <!-- Aanbevolen workshop -->
  <tr><td style="background:#fff;padding:0 16px 16px;border-top:1px solid #eee">
    <div style="{section_style}">Aanbevolen Workshop per Vestiging</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:1px">
      <thead>
        <tr>
          <th style="{th_style}">Vestiging</th>
          <th style="{th_style}">Workshop</th>
          <th style="{th_style};text-align:center">Score</th>
          <th style="{th_style};text-align:center">Gem. bezetting</th>
          <th style="{th_style}">Eerstvolgende</th>
        </tr>
      </thead>
      <tbody>{sug_rows}</tbody>
    </table>
  </td></tr>

  <!-- Urgentie -->
  <tr><td style="background:#fff;padding:0 16px 16px;border-top:1px solid #eee">
    <div style="{section_style}">Actie Nodig &mdash; &lt; 14 Dagen &amp; &gt;10 Vrije Plekken</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:1px">
      <thead>
        <tr>
          <th style="{th_style}">Workshop</th>
          <th style="{th_style}">Datum &amp; Tijd</th>
          <th style="{th_style}">Locatie</th>
          <th style="{th_style};text-align:center">Vrij</th>
          <th style="{th_style};text-align:center">Dagen</th>
        </tr>
      </thead>
      <tbody>{urg_rows}</tbody>
    </table>
  </td></tr>

  <!-- Leeg -->
  <tr><td style="background:#fff;padding:0 16px 16px;border-top:1px solid #eee;border-radius:0 0 6px 6px">
    <div style="{section_style}">Helemaal Leeg &mdash; Komende 6 Weken Zonder Boekingen</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:1px">
      <thead>
        <tr>
          <th style="{th_style}">Workshop</th>
          <th style="{th_style}">Datum &amp; Tijd</th>
          <th style="{th_style}">Locatie</th>
          <th style="{th_style};text-align:center">Over</th>
        </tr>
      </thead>
      <tbody>{empty_rows}</tbody>
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 0;text-align:center;font-size:11px;color:#aaa">
    Automatisch gegenereerd op {data['today']} &mdash; BBQ Experience Center Bezettingsgraad Dashboard
  </td></tr>

</table>
</td></tr>
</table>

</body>
</html>"""


def run():
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} niet gevonden. Eerst update_database.py uitvoeren.")
        raise SystemExit(1)

    conn = get_conn()
    data = build_data(conn)
    conn.close()

    html = render_html(data)
    OUT_PATH.write_text(html, encoding="utf-8")

    total = len(data["sessions"])
    print(f"Email digest gegenereerd: {OUT_PATH}")
    print(f"  Sessies: {total}, Urgentie: {len(data['urgency'])}, Leeg (6w): {len(data['empty_sessions'])}")
    print(f"  Open ter preview: {OUT_PATH}")


if __name__ == "__main__":
    run()
