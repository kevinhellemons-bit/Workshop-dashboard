"""
Reads .tmp/sessions.json and writes to .tmp/workshops.db (SQLite).
- Upserts current session availability
- Records daily snapshots for trend analysis
"""

import json
import sqlite3
from datetime import datetime, date
from pathlib import Path

DB_PATH   = Path(__file__).parent.parent / ".tmp" / "workshops.db"
JSON_PATH = Path(__file__).parent.parent / ".tmp" / "sessions.json"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            workshop_name   TEXT    NOT NULL,
            location        TEXT    NOT NULL,
            country         TEXT    NOT NULL,
            session_date    TEXT    NOT NULL,
            session_time    TEXT    NOT NULL,
            available_spots INTEGER NOT NULL,
            booked_spots    INTEGER NOT NULL,
            total_capacity  INTEGER NOT NULL DEFAULT 20,
            occupancy_pct   REAL    NOT NULL,
            price           REAL,
            last_seen       TEXT    NOT NULL,
            UNIQUE(url, session_date, session_time)
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            workshop_name   TEXT    NOT NULL,
            location        TEXT    NOT NULL,
            country         TEXT    NOT NULL,
            session_date    TEXT    NOT NULL,
            session_time    TEXT    NOT NULL,
            available_spots INTEGER NOT NULL,
            booked_spots    INTEGER NOT NULL,
            occupancy_pct   REAL    NOT NULL,
            snapshot_date   TEXT    NOT NULL,
            scraped_at      TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_date
            ON snapshots(snapshot_date);
        CREATE INDEX IF NOT EXISTS idx_sessions_location
            ON sessions(location);
        CREATE INDEX IF NOT EXISTS idx_sessions_date
            ON sessions(session_date);
    """)
    conn.commit()


def remove_stale_sessions(conn: sqlite3.Connection, sessions: list[dict]) -> int:
    """Verwijder toekomstige sessies die niet meer op de website staan.

    Per URL die sessies heeft opgeleverd, weten we de volledige actuele
    sessielijst. Toekomstige sessies voor die URL die er niet in zitten
    zijn verwijderd van de website en mogen uit de database.
    """
    today = date.today().isoformat()

    # Bouw per URL een set van (datum, tijd) op die wél gevonden zijn
    url_slots: dict[str, set] = {}
    for s in sessions:
        url = s["url"]
        if url not in url_slots:
            url_slots[url] = set()
        url_slots[url].add((s["date"], s["time"]))

    removed = 0
    for url, found_slots in url_slots.items():
        # Haal alle toekomstige sessies voor deze URL op
        rows = conn.execute(
            "SELECT session_date, session_time FROM sessions WHERE url=? AND session_date >= ?",
            (url, today)
        ).fetchall()
        for session_date, session_time in rows:
            if (session_date, session_time) not in found_slots:
                conn.execute(
                    "DELETE FROM sessions WHERE url=? AND session_date=? AND session_time=?",
                    (url, session_date, session_time)
                )
                removed += 1
    return removed


def upsert_sessions(conn: sqlite3.Connection, sessions: list[dict]) -> dict:
    today = date.today().isoformat()
    new_count = 0
    updated_count = 0
    snapshot_count = 0

    for s in sessions:
        # Check if session already exists
        existing = conn.execute(
            "SELECT id, available_spots FROM sessions WHERE url=? AND session_date=? AND session_time=?",
            (s["url"], s["date"], s["time"])
        ).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO sessions
                    (url, workshop_name, location, country, session_date, session_time,
                     available_spots, booked_spots, total_capacity, occupancy_pct, price, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s["url"], s["workshop_name"], s["location"], s["country"],
                s["date"], s["time"],
                s["available_spots"], s["booked_spots"], s["total_capacity"],
                s["occupancy_pct"], s["price"], s["scraped_at"]
            ))
            new_count += 1
        else:
            conn.execute("""
                UPDATE sessions SET
                    available_spots = ?,
                    booked_spots    = ?,
                    occupancy_pct   = ?,
                    last_seen       = ?
                WHERE url=? AND session_date=? AND session_time=?
            """, (
                s["available_spots"], s["booked_spots"], s["occupancy_pct"],
                s["scraped_at"],
                s["url"], s["date"], s["time"]
            ))
            updated_count += 1

        # Record one snapshot per session per day (skip duplicates)
        already_snapped = conn.execute(
            """SELECT 1 FROM snapshots
               WHERE url=? AND session_date=? AND session_time=? AND snapshot_date=?""",
            (s["url"], s["date"], s["time"], today)
        ).fetchone()

        if not already_snapped:
            conn.execute("""
                INSERT INTO snapshots
                    (url, workshop_name, location, country, session_date, session_time,
                     available_spots, booked_spots, occupancy_pct, snapshot_date, scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                s["url"], s["workshop_name"], s["location"], s["country"],
                s["date"], s["time"],
                s["available_spots"], s["booked_spots"], s["occupancy_pct"],
                today, s["scraped_at"]
            ))
            snapshot_count += 1

    conn.commit()
    return {"new": new_count, "updated": updated_count, "snapshots": snapshot_count}


def run():
    if not JSON_PATH.exists():
        print(f"ERROR: {JSON_PATH} niet gevonden. Eerst scrape_workshops.py uitvoeren.")
        raise SystemExit(1)

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    sessions = data.get("sessions", [])
    if not sessions:
        print("Geen sessies in JSON om te verwerken.")
        return

    conn = get_conn()
    init_schema(conn)

    stats = upsert_sessions(conn, sessions)
    removed = remove_stale_sessions(conn, sessions)
    conn.commit()
    conn.close()

    print(
        f"Database bijgewerkt: "
        f"{stats['new']} nieuw, {stats['updated']} bijgewerkt, "
        f"{stats['snapshots']} snapshots toegevoegd, {removed} verouderd verwijderd."
    )
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    run()
