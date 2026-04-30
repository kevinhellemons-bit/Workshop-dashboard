"""
Scrapes BBQ Experience Center workshop sessions.
- NL (Roosendaal + Nunspeet): via Twize Booking API
- BE (Herent): via HTML scraping (API key covers NL channel only)
Output: .tmp/sessions.json
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TOTAL_CAPACITY = 10
TWIZE_API_KEY    = os.getenv("TWIZE_API_KEY", "")
TWIZE_API_KEY_BE = os.getenv("TWIZE_API_KEY_BE", "")
TWIZE_API_URL    = "https://bxc.booking.twize.io/api/slots/"

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

# Lookups keyed by (workshop_name_lower, location) — used when mapping API results
PRICE_MAP = {(name.lower(), loc): price for _, name, loc, _, price in WORKSHOP_URLS}
URL_MAP   = {(name.lower(), loc): url   for url, name, loc, _, _  in WORKSHOP_URLS}

HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

LOCATION_ID_MAP = {
    "Roosendaal": "1",
    "Nunspeet":   "2",
    "Herent":     "4",
}

SLOT_WITH_LOC_PATTERN = re.compile(
    r'"location_id"\s*:\s*(\d+)[^{}]{0,500}"option_label"\s*:\s*"#(\d{4}-\d{2}-\d{2})##(\d{2}:\d{2})###(\d+)"'
)

SESSION_PATTERN = re.compile(
    r"#(\d{4}-\d{2}-\d{2})##(\d{2}:\d{2})###(\d+)"
)


def fetch_sessions_from_api(scraped_at: str, api_key: str, country_override: str | None = None) -> list[dict]:
    """Fetch all sessions from Twize Booking API for a given key/channel."""
    resp = requests.get(
        TWIZE_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    records = []
    for bookable in data.get("bookables", []):
        workshop_name = bookable["name"]
        for slot in bookable.get("slots", []):
            if slot.get("publication_status") != "published":
                continue
            loc   = slot["location"]["name"]
            cntry = country_override or slot["location"]["country"]
            avail = slot["availability"]
            available = avail["available_tables"]
            booked    = avail["used_tables"]
            total     = avail["max_tables"]

            dt       = datetime.fromisoformat(slot["starts_at"])
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")

            key   = (workshop_name.lower(), loc)
            url   = URL_MAP.get(key, f"twize://{bookable['id']}")
            price = PRICE_MAP.get(key)

            records.append({
                "url":            url,
                "workshop_name":  workshop_name,
                "location":       loc,
                "country":        cntry,
                "price":          price,
                "date":           date_str,
                "time":           time_str,
                "available_spots": available,
                "booked_spots":   booked,
                "total_capacity": total,
                "occupancy_pct":  round(booked / total * 100, 1) if total else 0,
                "scraped_at":     scraped_at,
            })
    return records


def fetch_sessions(url: str, location: str = "") -> list[dict]:
    """Fetch a workshop page (HTML) and extract session slots. Used for Herent (BE)."""
    try:
        resp = requests.get(url, headers=HTML_HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR fetching {url}: {e}")
        return []

    html = resp.text
    target_loc_id = LOCATION_ID_MAP.get(location)

    if target_loc_id:
        slot_matches = SLOT_WITH_LOC_PATTERN.findall(html)
        filtered = [(d, t, s) for loc_id, d, t, s in slot_matches if loc_id == target_loc_id]
        seen = set()
        matches = []
        for d, t, s in filtered:
            if (d, t) not in seen:
                seen.add((d, t))
                matches.append((d, t, s))
    else:
        matches = SESSION_PATTERN.findall(html)
        if not matches:
            matches = _try_json_fallback(html)

    sessions = []
    for date_str, time_str, spots_str in matches:
        available = int(spots_str)
        booked = TOTAL_CAPACITY - available
        sessions.append({
            "date":            date_str,
            "time":            time_str,
            "available_spots": available,
            "booked_spots":    booked,
            "total_capacity":  TOTAL_CAPACITY,
            "occupancy_pct":   round(booked / TOTAL_CAPACITY * 100, 1),
        })
    return sessions


def _try_json_fallback(html: str) -> list[tuple]:
    results = []
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

    # ── NL locations via Twize API (Roosendaal + Nunspeet) ─────────────────
    if TWIZE_API_KEY:
        print("Fetching NL sessions from Twize API...")
        try:
            nl_records = fetch_sessions_from_api(scraped_at, TWIZE_API_KEY)
            by_loc: dict[str, int] = {}
            for r in nl_records:
                by_loc[r["location"]] = by_loc.get(r["location"], 0) + 1
            for loc, count in sorted(by_loc.items()):
                print(f"  {loc}: {count} sessies")
            print(f"  Totaal NL: {len(nl_records)} sessies")
            all_records.extend(nl_records)
        except Exception as e:
            print(f"  ERROR Twize NL API: {e}")
            errors.append(TWIZE_API_URL)
    else:
        print("TWIZE_API_KEY niet gevonden — NL API overgeslagen.")

    # ── BE location via Twize API (Herent) ─────────────────────────────────
    if TWIZE_API_KEY_BE:
        print("Fetching BE sessions from Twize API...")
        try:
            # BE API wrongly reports country="NL" for Herent — override to "BE"
            be_records = fetch_sessions_from_api(scraped_at, TWIZE_API_KEY_BE, country_override="BE")
            by_loc_be: dict[str, int] = {}
            for r in be_records:
                by_loc_be[r["location"]] = by_loc_be.get(r["location"], 0) + 1
            for loc, count in sorted(by_loc_be.items()):
                print(f"  {loc}: {count} sessies")
            print(f"  Totaal BE: {len(be_records)} sessies")
            all_records.extend(be_records)
        except Exception as e:
            print(f"  ERROR Twize BE API: {e}")
            errors.append(TWIZE_API_URL + "?be")
    else:
        # Fallback: HTML scraping for Herent
        be_urls = [
            (url, name, loc, country, price)
            for url, name, loc, country, price in WORKSHOP_URLS
            if country == "BE"
        ]
        print(f"\nScraping {len(be_urls)} Herent (BE) pagina's (geen BE API key)...")
        for i, (url, name, location, country, price) in enumerate(be_urls, 1):
            print(f"  [{i:02d}/{len(be_urls)}] {name} – {location}... ", end="", flush=True)
            sessions = fetch_sessions(url, location)
            print(f"{len(sessions)} sessies gevonden")
            if not sessions:
                errors.append(url)
            for s in sessions:
                all_records.append({
                    "url":           url,
                    "workshop_name": name,
                    "location":      location,
                    "country":       country,
                    "price":         price,
                    **s,
                    "scraped_at":    scraped_at,
                })
            if i < len(be_urls):
                time.sleep(1)

    result = {
        "scraped_at":         scraped_at,
        "total_sessions":     len(all_records),
        "errors":             errors,
        "sessions":           all_records,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nKlaar. {len(all_records)} sessies opgeslagen in {output_file}")
    if errors:
        print(f"Fouten bij {len(errors)} bron(nen):")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    run()
