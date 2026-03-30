"""
Pusht de gegenereerde dashboard.html naar een Google Apps Script web app.
De bestaande deployment (en dus de URL) blijft hetzelfde — alleen de inhoud wordt bijgewerkt.

Vereisten:
  - credentials.json in de projectroot (OAuth Desktop-app, Apps Script API ingeschakeld)
  - APPS_SCRIPT_ID in .env (Script-ID uit Apps Script → Projectinstellingen)

Eerste keer: vraagt om browserauthenticatie → slaat token op als token_apps_script.json
"""

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

ROOT          = Path(__file__).parent.parent
DASHBOARD_HTML = ROOT / "dashboard.html"
CREDENTIALS    = ROOT / "credentials.json"
TOKEN_PATH     = ROOT / "token_apps_script.json"
SCRIPT_ID      = os.getenv("APPS_SCRIPT_ID", "")

SCOPES = [
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.deployments",
]


def get_service():
    # In CI (GitHub Actions): schrijf credentials/token vanuit env vars naar bestanden
    token_env = os.getenv("GOOGLE_TOKEN_JSON")
    creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if token_env:
        TOKEN_PATH.write_text(token_env)
    if creds_env:
        CREDENTIALS.write_text(creds_env)

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
            TOKEN_PATH.write_text(creds.to_json())
    return build("script", "v1", credentials=creds)


def get_current_files(service):
    """Haal huidige scriptbestanden op zodat we ze kunnen behouden."""
    result = service.projects().getContent(scriptId=SCRIPT_ID).execute()
    return result.get("files", [])


DO_GET = (
    "function doGet() {\n"
    "  return HtmlService.createHtmlOutputFromFile('index')\n"
    "    .setTitle('BBQ Experience Center \u2013 Dashboard')\n"
    "    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);\n"
    "}\n"
)

MANIFEST = json.dumps({
    "timeZone": "Europe/Amsterdam",
    "dependencies": {},
    "exceptionLogging": "STACKDRIVER",
    "runtimeVersion": "V8",
    "webapp": {
        "executeAs": "USER_DEPLOYING",
        "access": "ANYONE_ANONYMOUS",
    },
}, indent=2)


def push_dashboard(service, html_content):
    """Vervang de index.html in het Apps Script project met nieuwe dashboard HTML."""
    current_files = get_current_files(service)

    # Bewaar bestaand manifest als dat er al is, anders gebruik default
    manifest_src = next(
        (f["source"] for f in current_files if f.get("name") == "appsscript"),
        MANIFEST,
    )

    new_files = [
        {"name": "appsscript", "type": "JSON",       "source": manifest_src},
        {"name": "Code",       "type": "SERVER_JS",  "source": DO_GET},
        {"name": "index",      "type": "HTML",        "source": html_content},
    ]

    service.projects().updateContent(
        scriptId=SCRIPT_ID,
        body={"files": new_files},
    ).execute()


def update_deployment(service):
    """Maak een nieuwe versie aan en update de bestaande deployment."""
    # Maak nieuwe versie
    version = service.projects().versions().create(
        scriptId=SCRIPT_ID,
        body={"description": f"Dashboard update"},
    ).execute()
    version_number = version["versionNumber"]

    # Zoek bestaande deployment
    deployments = service.projects().deployments().list(
        scriptId=SCRIPT_ID
    ).execute().get("deployments", [])

    # Gebruik de eerste niet-@HEAD deployment
    target = next(
        (d for d in deployments if d.get("deploymentId") and
         d.get("deploymentConfig", {}).get("versionNumber") is not None),
        None
    )

    if target:
        dep_id = target["deploymentId"]
        service.projects().deployments().update(
            scriptId=SCRIPT_ID,
            deploymentId=dep_id,
            body={
                "deploymentConfig": {
                    "versionNumber": version_number,
                    "manifestFileName": "appsscript",
                    "description": "Dashboard update",
                }
            },
        ).execute()
        print(f"  Deployment bijgewerkt (versie {version_number})")
    else:
        # Geen bestaande deployment — maak nieuwe aan
        dep = service.projects().deployments().create(
            scriptId=SCRIPT_ID,
            body={
                "versionNumber": version_number,
                "manifestFileName": "appsscript",
                "description": "Dashboard",
            },
        ).execute()
        print(f"  Nieuwe deployment aangemaakt: {dep.get('deploymentId')}")


def run():
    # In CI: schrijf credentials/token vanuit env vars naar bestanden (vóór checks)
    creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
    token_env = os.getenv("GOOGLE_TOKEN_JSON")
    if creds_env:
        CREDENTIALS.write_text(creds_env)
    if token_env:
        TOKEN_PATH.write_text(token_env)

    if not SCRIPT_ID:
        print("ERROR: APPS_SCRIPT_ID niet ingesteld in .env")
        print("  Voeg toe: APPS_SCRIPT_ID=jouw-script-id")
        raise SystemExit(1)

    if not CREDENTIALS.exists():
        print(f"ERROR: {CREDENTIALS} niet gevonden.")
        print("  Download OAuth credentials via Google Cloud Console → Inloggegevens → OAuth 2.0-client-ID")
        raise SystemExit(1)

    if not DASHBOARD_HTML.exists():
        print(f"ERROR: {DASHBOARD_HTML} niet gevonden. Eerst generate_dashboard.py uitvoeren.")
        raise SystemExit(1)

    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    print(f"  Dashboard geladen: {len(html):,} bytes")

    print("  Verbinden met Google Apps Script API...")
    service = get_service()

    print("  HTML uploaden naar Apps Script project...")
    push_dashboard(service, html)

    print("  Deployment bijwerken...")
    update_deployment(service)

    print(f"Dashboard gepubliceerd!")
    print(f"  URL: https://script.google.com/macros/s/{SCRIPT_ID}/exec")


if __name__ == "__main__":
    run()
