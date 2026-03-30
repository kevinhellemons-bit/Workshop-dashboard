# Workflow: Dagelijkse bezettingsgraad refresh

## Doel
Elke ochtend actuele bezettingsdata ophalen van alle BBQ Experience Center workshoppagina's, de database bijwerken, het dashboard regenereren en een Slack digest versturen.

## Inputs
- Geen handmatige inputs vereist
- Vereiste omgevingsvariabelen (in `.env`):
  - `SLACK_WEBHOOK_URL` — Slack Incoming Webhook

## Stappen (in volgorde)

### 1. Scrape alle workshoppagina's
```
python tools/scrape_workshops.py
```
- Haalt alle 52 URLs op
- Slaat ruwe sessiedata op in `.tmp/sessions.json`
- Verwacht uitvoer: "X sessies opgeslagen"
- **Bij fout:** Lees de foutmelding. Controleer of de website bereikbaar is en of de HTML-structuur niet is gewijzigd (zie: Onderhoud).

### 2. Sla data op in database
```
python tools/update_database.py
```
- Leest `.tmp/sessions.json`
- Schrijft naar `.tmp/workshops.db`
- Slaat dagelijkse snapshot op voor trendberekening
- **Bij fout:** Controleer of `.tmp/sessions.json` bestaat en geldig JSON is.

### 3. Genereer dashboard
```
python tools/generate_dashboard.py
```
- Leest `.tmp/workshops.db`
- Genereert `dashboard.html` in de projectroot
- Open het bestand in een browser om het te bekijken
- **Bij fout:** Controleer of de database niet leeg is.

### 4. Verstuur Slack digest
```
python tools/send_slack_digest.py
```
- Verstuurt dagelijkse samenvatting naar Slack
- **Bij fout:** Controleer of `SLACK_WEBHOOK_URL` in `.env` is ingesteld en geldig is.

## Verwachte outputs
- `.tmp/sessions.json` — Ruwe scrapedata
- `.tmp/workshops.db` — SQLite database met historische data
- `dashboard.html` — Visueel dashboard (openen in browser)
- Slack bericht in het geconfigureerde kanaal

## Edge cases

### Website structuur gewijzigd
Als de scraper 0 sessies teruggeeft voor meerdere URLs:
1. Open een workshop URL handmatig in de browser
2. Bekijk de paginabron (Ctrl+U)
3. Zoek naar het patroon `#YYYY-MM-DD##HH:MM###N`
4. Als het patroon is gewijzigd, update `SESSION_PATTERN` in `tools/scrape_workshops.py`
5. Update ook `_try_json_fallback()` als de fallback nodig is
6. Noteer de wijziging in dit workflow-bestand

### Rate limiting
Als de site requests blokkeert:
- Verhoog de `time.sleep(1)` in `scrape_workshops.py` naar 2-3 seconden
- Controleer of de User-Agent header nog geldig is

### Nieuwe workshops toegevoegd
1. Voeg de URL toe aan `WORKSHOP_URLS` in `tools/scrape_workshops.py`
2. Voeg naam, locatie, land en prijs toe
3. Test handmatig: `python tools/scrape_workshops.py`

### Capaciteit gewijzigd (niet meer 20 per sessie)
- Update `TOTAL_CAPACITY` in `tools/scrape_workshops.py`

## Onderhoud
- Controleer maandelijks of alle URLs nog geldig zijn
- Controleer bij grote bezettingsverschillen of de extractielogica nog klopt
- Database groeit ~1 KB per dag — geen actie nodig tenzij > 1 jaar oud
