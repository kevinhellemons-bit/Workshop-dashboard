"""
Stuurt elke maandag een Slack-bericht met de "Actie nodig" sessies:
aankomende sessies binnen 21 dagen met meer dan 7 vrije tafels.
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


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_message() -> dict:
    today   = date.today().isoformat()
    in_21   = (date.today() + timedelta(days=21)).isoformat()
    week_nl = fmt_date(today, full=True)

    conn = get_conn()

    urgency = query(conn, """
        SELECT workshop_name, location, country, session_date, session_time,
               available_spots, total_capacity, occupancy_pct
        FROM sessions
        WHERE session_date >= ? AND session_date <= ?
          AND available_spots > 7
        ORDER BY session_date, session_time, location
    """, (today, in_21))

    # Overall stats for context
    totals = conn.execute(
        "SELECT SUM(booked_spots) AS b, SUM(total_capacity) AS c FROM sessions WHERE session_date >= ?",
        (today,)
    ).fetchone()
    overall_pct = round(totals[0] / totals[1] * 100, 1) if totals[1] else 0

    conn.close()

    # ── Header ────────────────────────────────────────────────────────────
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 BBQ Experience Center – Weekoverzicht Actie Nodig"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Week van *{week_nl}* — sessies in de komende 21 dagen met meer dan 7 vrije tafels.\n"
                    f"Totale bezettingsgraad alle vestigingen: `{overall_pct}%`"
                )
            }
        },
        {"type": "divider"},
    ]

    if not urgency:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ Geen sessies met meer dan 7 vrije tafels in de komende 21 dagen. Goed bezig!"}
        })
    else:
        # Group by location
        by_loc = {}
        for u in urgency:
            by_loc.setdefault(u["location"], []).append(u)

        for loc, sessions in by_loc.items():
            lines = [f"*{loc}*"]
            for s in sessions:
                days_until = (date.fromisoformat(s["session_date"]) - date.today()).days
                pct = s["occupancy_pct"]
                bar = "🔴" if days_until <= 7 else ("🟡" if days_until <= 14 else "🟠")
                lines.append(
                    f"{bar} *{s['workshop_name']}*  –  {fmt_date(s['session_date'])} {s['session_time']}"
                    f"  |  `{s['available_spots']} tafels vrij`  `{pct}% bezet`  _(over {days_until} dagen)_"
                )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)}
            })
            blocks.append({"type": "divider"})

        # Summary line
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Totaal: {len(urgency)} sessie{'s' if len(urgency) != 1 else ''} met actie nodig.*"
            }
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "BBQ Experience Center · Wekelijkse actiemelding · Elke maandag automatisch gegenereerd"}]
    })

    return {"blocks": blocks}


def run():
    if not SLACK_WEBHOOK_URL:
        print("ERROR: SLACK_WEBHOOK_URL niet ingesteld in .env — weekly digest overgeslagen.")
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
        print("Slack weekoverzicht verstuurd.")
    else:
        print(f"ERROR: Slack gaf status {resp.status_code}: {resp.text}")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
