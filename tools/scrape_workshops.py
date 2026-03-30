"""
Scrapes all BBQ Experience Center workshop pages and extracts session data.
Output: .tmp/sessions.json
"""

import json
import re
import time
import os
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

TOTAL_CAPACITY = 20

WORKSHOP_URLS = [
    # ── Roosendaal (NL) ───────────────────────────────────────────────────
    ("https://www.bbqexperiencecenter.nl/proef-beleef-het-varken/",         "Proef & Beleef het Varken",    "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/bbq-experience-workshop/",          "BBQ Experience Workshop",      "Roosendaal", "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/bier-bbq-workshop/",                "Bier & BBQ Workshop",          "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/the-bastard-experience/",           "The Bastard Experience",       "Roosendaal", "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/kamado-experience/",                "Kamado Experience",            "Roosendaal", "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/big-green-eggperience/",            "Big Green Eggperience",        "Roosendaal", "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/pizza-masterclass/",                "Masterclass Pizza",            "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/uit-de-zee-masterclass/",           "Uit de Zee Masterclass",       "Roosendaal", "NL", 125.00),
    ("https://www.bbqexperiencecenter.nl/wild-winter/",                      "Wild & Winter 3.0",            "Roosendaal", "NL", 119.50),
    ("https://www.bbqexperiencecenter.nl/chefs-choice-menu/",                "Chef's Choice Menu",           "Roosendaal", "NL", 119.50),
    ("https://www.bbqexperiencecenter.nl/italie-2-0/",                       "Italië 2.0",                   "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/vlees-4-0/",                        "Vlees 4.0",                    "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/american-classics/",                "American Classics",            "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/streetfood/",                       "Streetfood 3.0",               "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/whisky-bbq-workshop/",              "Whisky & BBQ Workshop",        "Roosendaal", "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/kerstmenu-met-wijn-pairing/",       "Kerstmenu met Wijn Pairing",   "Roosendaal", "NL", 150.00),

    # ── Nunspeet (NL) ────────────────────────────────────────────────────
    ("https://www.bbqexperiencecenter.nl/proef-bleef-het-varken-nunspeet/",  "Proef & Beleef het Varken",    "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/bbq-experience-workshop-nunspeet/", "BBQ Experience Workshop",      "Nunspeet",   "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/bier-bbq-workshop-nunspeet/",       "Bier & BBQ Workshop",          "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/the-bastard-experience-nunspeet/",  "The Bastard Experience",       "Nunspeet",   "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/ofyr-experience-workshop-nunspeet/","OFYR Experience Workshop",     "Nunspeet",   "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/kamado-experience-nunspeet/",       "Kamado Experience",            "Nunspeet",   "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/big-green-eggperience-nunspeet/",   "Big Green Eggperience",        "Nunspeet",   "NL",  99.50),
    ("https://www.bbqexperiencecenter.nl/ofyr-masterclass-nunspeet/",        "OFYR Masterclass",             "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/masterclass-pizza-nunspeet/",       "Masterclass Pizza",            "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/uit-de-zee-masterclass-nunspeet/",  "Uit de Zee Masterclass",       "Nunspeet",   "NL", 125.00),
    ("https://www.bbqexperiencecenter.nl/wild-winter-3-0-nunspeet/",         "Wild & Winter 3.0",            "Nunspeet",   "NL", 119.50),
    ("https://www.bbqexperiencecenter.nl/chefs-choice-menu-nunspeet/",       "Chef's Choice Menu",           "Nunspeet",   "NL", 119.50),
    ("https://www.bbqexperiencecenter.nl/italie-2-0-nunspeet/",              "Italië 2.0",                   "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/vlees-4-0-nunspeet/",               "Vlees 4.0",                    "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/american-classics-nunspeet/",       "American Classics",            "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/streetfood-3-0-nunspeet/",          "Streetfood 3.0",               "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/whisky-bbq-workshop-nunspeet/",     "Whisky & BBQ Workshop",        "Nunspeet",   "NL", 114.50),
    ("https://www.bbqexperiencecenter.nl/kerstmenu-met-wijn-pairing-nunspeet/","Kerstmenu met Wijn Pairing", "Nunspeet",   "NL", 150.00),
    ("https://www.bbqexperiencecenter.nl/proef-beleef-de-veluwe/",           "Proef & Beleef de Veluwe",     "Nunspeet",   "NL", 114.50),

    # ── Herent (BE) ──────────────────────────────────────────────────────
    ("https://www.bbqexperiencecenter.be/nl/proef-beleef-het-varken/",       "Proef & Beleef het Varken",    "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/the-bastard-experience/",        "The Bastard Experience",       "Herent",     "BE",  99.50),
    ("https://www.bbqexperiencecenter.be/nl/bier-bbq-workshop/",             "Bier & BBQ Workshop",          "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/whisky-bbq-workshop/",           "Whisky & BBQ Workshop",        "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/bbq-experience-workshop/",       "BBQ Experience Workshop",      "Herent",     "BE",  99.50),
    ("https://www.bbqexperiencecenter.be/nl/ofyr-experience-workshop/",      "OFYR Experience Workshop",     "Herent",     "BE",  99.50),
    ("https://www.bbqexperiencecenter.be/nl/kamado-experience/",             "Kamado Experience",            "Herent",     "BE",  99.50),
    ("https://www.bbqexperiencecenter.be/nl/big-green-eggperience/",         "Big Green Eggperience",        "Herent",     "BE",  99.50),
    ("https://www.bbqexperiencecenter.be/nl/pizza-masterclass/",             "Masterclass Pizza",            "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/uit-de-zee-masterclass/",        "Uit de Zee Masterclass",       "Herent",     "BE", 125.00),
    ("https://www.bbqexperiencecenter.be/nl/italie-2-0/",                    "Italië 2.0",                   "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/ofyr-experience-masterclass/",   "OFYR Masterclass",             "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/american-classics/",             "American Classics",            "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/streetfood/",                    "Streetfood 3.0",               "Herent",     "BE", 114.50),
    ("https://www.bbqexperiencecenter.be/nl/chefs-choice-menu/",             "Chef's Choice Menu",           "Herent",     "BE", 119.50),
    ("https://www.bbqexperiencecenter.be/nl/wild-winter/",                   "Wild & Winter 3.0",            "Herent",     "BE", 119.50),
    ("https://www.bbqexperiencecenter.be/nl/vlees-4-0/",                     "Vlees 4.0",                    "Herent",     "BE", 114.50),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

# Matches: #2026-03-27##18:00###0  or  #2026-03-27##18:00###6
SESSION_PATTERN = re.compile(
    r"#(\d{4}-\d{2}-\d{2})##(\d{2}:\d{2})###(\d+)"
)


def fetch_sessions(url: str) -> list[dict]:
    """Fetch a workshop page and extract session slots."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR fetching {url}: {e}")
        return []

    html = resp.text
    matches = SESSION_PATTERN.findall(html)

    if not matches:
        # Fallback: try alternative JSON-like format from WooCommerce script tags
        matches = _try_json_fallback(html)

    sessions = []
    for date_str, time_str, spots_str in matches:
        available = int(spots_str)
        booked = TOTAL_CAPACITY - available
        sessions.append({
            "date": date_str,
            "time": time_str,
            "available_spots": available,
            "booked_spots": booked,
            "total_capacity": TOTAL_CAPACITY,
            "occupancy_pct": round(booked / TOTAL_CAPACITY * 100, 1),
        })

    return sessions


def _try_json_fallback(html: str) -> list[tuple]:
    """
    Secondary extraction: look for WooCommerce Bookings JS objects.
    Returns list of (date, time, available_spots) tuples.
    """
    results = []

    # Pattern: "2026-03-27":{"18:00":{"available":6}}
    pattern = re.compile(
        r'"(\d{4}-\d{2}-\d{2})"[^}]*?"(\d{2}:\d{2})"[^}]*?"available"\s*:\s*(\d+)'
    )
    for m in pattern.finditer(html):
        results.append((m.group(1), m.group(2), m.group(3)))

    return results


def run():
    output_dir = Path(__file__).parent.parent / ".tmp"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "sessions.json"

    scraped_at = datetime.now().isoformat(timespec="seconds")
    all_records = []
    errors = []

    print(f"Scraping {len(WORKSHOP_URLS)} workshop pages...")

    for i, (url, name, location, country, price) in enumerate(WORKSHOP_URLS, 1):
        print(f"  [{i:02d}/{len(WORKSHOP_URLS)}] {name} – {location}... ", end="", flush=True)
        sessions = fetch_sessions(url)
        print(f"{len(sessions)} sessies gevonden")

        if not sessions:
            errors.append(url)

        for s in sessions:
            all_records.append({
                "url": url,
                "workshop_name": name,
                "location": location,
                "country": country,
                "price": price,
                **s,
                "scraped_at": scraped_at,
            })

        # Be polite — 1 second between requests
        if i < len(WORKSHOP_URLS):
            time.sleep(1)

    result = {
        "scraped_at": scraped_at,
        "total_urls": len(WORKSHOP_URLS),
        "urls_with_sessions": len(WORKSHOP_URLS) - len(errors),
        "total_sessions": len(all_records),
        "errors": errors,
        "sessions": all_records,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nKlaar. {len(all_records)} sessies opgeslagen in {output_file}")
    if errors:
        print(f"Geen sessies gevonden voor {len(errors)} URL(s):")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    run()
