"""
Reads .tmp/workshops.db and sends a daily Slack digest.
Requires SLACK_WEBHOOK_URL in .env
"""

import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / ".tmp" / "workshops.db"

load_dotenv(ROOT / ".env")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_message() -> dict:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    week_ago  = (date.today() - timedelta(days=7)).isoformat()
    in_21     = (date.today() + timedelta(days=21)).isoformat()

    conn = get_conn()

    location_summary = query(conn, """
        SELECT location, country,
               SUM(booked_spots) AS total_booked,
               SUM(total_capacity) AS total_cap,
               ROUND(SUM(booked_spots) * 100.0 / SUM(total_capacity), 1) AS occ_pct,
               COUNT(*) AS sessions
        FROM sessions WHERE session_date >= ?
        GROUP BY location ORDER BY occ_pct DESC
    """, (today,))

    # Daily delta
    today_snap = {r["location"]: r["total_booked"] for r in query(conn,
        "SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location",
        (today,))}
    yest_snap = {r["location"]: r["total_booked"] for r in query(conn,
        "SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location",
        (yesterday,))}
    week_snap = {r["location"]: r["total_booked"] for r in query(conn,
        "SELECT location, SUM(booked_spots) AS total_booked FROM snapshots WHERE snapshot_date=? GROUP BY location",
        (week_ago,))}

    # Urgency
    urgency = query(conn, """
        SELECT workshop_name, location, session_date, session_time, available_spots
        FROM sessions
        WHERE session_date >= ? AND session_date <= ? AND available_spots > 7
        ORDER BY session_date, session_time
        LIMIT 5
    """, (today, in_21))

    # Full sessions
    full_count = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE session_date >= ? AND available_spots = 0", (today,)
    ).fetchone()[0]

    # Overall
    totals = conn.execute(
        "SELECT SUM(booked_spots) AS b, SUM(total_capacity) AS c FROM sessions WHERE session_date >= ?", (today,)
    ).fetchone()
    overall_pct = round(totals[0] / totals[1] * 100, 1) if totals[1] else 0

    conn.close()

    # ── Build blocks ───────────────────────────────────────────────────────
    date_nl = date.today().strftime("%-d %B %Y") if os.name != 'nt' else date.today().strftime("%d %B %Y").lstrip("0")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔥 BBQ Workshop Bezettingsgraad – {date_nl}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Totale bezettingsgraad:* `{overall_pct}%` | *Volzet:* {full_count} sessies"}
        },
        {"type": "divider"},
    ]

    # Location blocks
    loc_lines = []
    for loc in location_summary:
        name = loc["location"]
        pct  = loc["occ_pct"]
        booked = loc["total_booked"]
        cap    = loc["total_cap"]

        emoji = "🟢" if pct >= 70 else ("🟡" if pct >= 40 else "🔵")

        # Deltas
        t_book = today_snap.get(name, booked)
        y_book = yest_snap.get(name)
        w_book = week_snap.get(name)
        day_delta  = f"+{t_book - y_book}" if y_book is not None and t_book > y_book else (f"{t_book - y_book}" if y_book is not None else "–")
        week_delta = f"+{t_book - w_book}" if w_book is not None and t_book > w_book else (f"{t_book - w_book}" if w_book is not None else "–")

        loc_lines.append(
            f"{emoji} *{name}* ({loc['country']})  `{pct}%`  –  {booked}/{cap} tafels geboekt\n"
            f"   ↑ Dag: {day_delta}  |  Week: {week_delta}"
        )

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n\n".join(loc_lines)}
    })

    # Urgency
    if urgency:
        blocks.append({"type": "divider"})
        urg_lines = ["*⚠️ Actie nodig – sessies < 21 dagen met >7 tafels vrij:*"]
        for u in urgency:
            urg_lines.append(f"• *{u['workshop_name']}* – {u['session_date']} {u['session_time']} ({u['location']}) – `{u['available_spots']} tafels vrij`")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(urg_lines)}
        })
    else:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ Geen urgente sessies vandaag."}
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "BBQ Experience Center · Bezettingsgraad Dashboard · Automatisch gegenereerd"}]
    })

    return {"blocks": blocks}


def run():
    if not SLACK_WEBHOOK_URL:
        print("ERROR: SLACK_WEBHOOK_URL niet ingesteld in .env — Slack digest overgeslagen.")
        return

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} niet gevonden.")
        raise SystemExit(1)

    payload = build_message()

    resp = requests.post(
        SLACK_WEBHOOK_URL,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )

    if resp.status_code == 200 and resp.text == "ok":
        print("Slack digest verstuurd.")
    else:
        print(f"ERROR: Slack gaf status {resp.status_code}: {resp.text}")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
