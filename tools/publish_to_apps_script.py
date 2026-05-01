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

ROOT           = Path(__file__).parent.parent
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
    result = service.projects().getContent(scriptId=SCRIPT_ID).execute()
    return result.get("files", [])


DO_GET = (
    "function doGet() {\n"
    "  return HtmlService.createHtmlOutputFromFile('index')\n"
    "    .setTitle('BBQ Experience Center – Dashboard')\n"
    "    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);\n"
    "}\n"
    "\n"
    "function triggerWorkflow() {\n"
    "  try {\n"
    "    var props = PropertiesService.getScriptProperties();\n"
    "    var token = props.getProperty('GITHUB_PAT');\n"
    "    if (!token) return { ok: false, error: 'GITHUB_PAT ontbreekt in Script-eigenschappen' };\n"
    "    var resp = UrlFetchApp.fetch(\n"
    "      'https://api.github.com/repos/kevinhellemons-bit/Workshop-dashboard/actions/workflows/daily.yml/dispatches',\n"
    "      { method: 'post', contentType: 'application/json',\n"
    "        headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },\n"
    "        payload: JSON.stringify({ ref: 'main' }),\n"
    "        muteHttpExceptions: true }\n"
    "    );\n"
    "    var code = resp.getResponseCode();\n"
    "    if (code === 204) return { ok: true };\n"
    "    return { ok: false, error: 'GitHub status ' + code + ': ' + resp.getContentText() };\n"
    "  } catch(err) {\n"
    "    return { ok: false, error: err.message };\n"
    "  }\n"
    "}\n"
    "\n"
    "function sendSlackActions(jsonStr) {\n"
    "  try {\n"
    "    var data = JSON.parse(jsonStr);\n"
    "    var actions = data.actions || [];\n"
    "    var props = PropertiesService.getScriptProperties();\n"
    "    var token = props.getProperty('SLACK_BOT_TOKEN');\n"
    "    var channels = { marketing: '#marketing', klantenservice: '#klantenservice', retail: '#support-team-retail' };\n"
    "    var labels   = { marketing: 'Marketing Push', klantenservice: 'Annuleren', retail: 'Andere Workshop in Plaats' };\n"
    "    var emojis   = { marketing: '\U0001F4E3', klantenservice: '❌', retail: '\U0001F504' };\n"
    "    var byChannel = {};\n"
    "    actions.forEach(function(a) {\n"
    "      if (!byChannel[a.action_id]) byChannel[a.action_id] = [];\n"
    "      byChannel[a.action_id].push(a);\n"
    "    });\n"
    "    var sent = 0; var missing = [];\n"
    "    Object.keys(byChannel).forEach(function(ch) {\n"
    "      if (!token) { missing.push(ch); return; }\n"
    "      var items = byChannel[ch];\n"
    "      var lines = items.map(function(a) {\n"
    "        var spots = a.available_spots ? ' — `' + a.available_spots + ' vrij`' : '';\n"
    "        return '• *' + a.workshop + '* — ' + a.location + ' — ' + a.date + ' ' + a.time + spots;\n"
    "      });\n"
    "      var blocks = [\n"
    "        { type: 'header', text: { type: 'plain_text', text: emojis[ch] + ' Actiepunt: ' + labels[ch] } },\n"
    "        { type: 'section', text: { type: 'mrkdwn', text: '*' + items.length + ' sessie' + (items.length > 1 ? 's' : '') + ':*\\n' + lines.join('\\n') } },\n"
    "        { type: 'context', elements: [{ type: 'mrkdwn', text: 'BBQ Experience Center · Dashboard actiepunt' }] }\n"
    "      ];\n"
    "      UrlFetchApp.fetch('https://slack.com/api/chat.postMessage', {\n"
    "        method: 'post', contentType: 'application/json',\n"
    "        headers: { Authorization: 'Bearer ' + token },\n"
    "        payload: JSON.stringify({ channel: channels[ch], blocks: blocks })\n"
    "      });\n"
    "      sent += items.length;\n"
    "    });\n"
    "    return { ok: true, sent: sent, missing: missing };\n"
    "  } catch (err) {\n"
    "    return { ok: false, error: err.message };\n"
    "  }\n"
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
    current_files = get_current_files(service)

    manifest_src = next(
        (f["source"] for f in current_files if f.get("name") == "appsscript"),
        MANIFEST,
    )

    new_files = [
        {"name": "appsscript", "type": "JSON",      "source": manifest_src},
        {"name": "Code",       "type": "SERVER_JS", "source": DO_GET},
        {"name": "index",      "type": "HTML",       "source": html_content},
    ]

    service.projects().updateContent(
        scriptId=SCRIPT_ID,
        body={"files": new_files},
    ).execute()


def update_deployment(service):
    version = service.projects().versions().create(
        scriptId=SCRIPT_ID,
        body={"description": "Dashboard update"},
    ).execute()
    version_number = version["versionNumber"]

    deployments = service.projects().deployments().list(
        scriptId=SCRIPT_ID
    ).execute().get("deployments", [])

    target = next(
        (d for d in deployments if d.get("deploymentId") and
         d.get("deploymentConfig", {}).get("versionNumber") is not None),
        None
    )

    if target:
        dep_id = target["deploymentId"]
        result = service.projects().deployments().update(
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
        for ep in result.get("entryPoints", []):
            if ep.get("entryPointType") == "WEB_APP":
                print(f"  Deployment URL: {ep['webApp']['url']}")
    else:
        dep = service.projects().deployments().create(
            scriptId=SCRIPT_ID,
            body={
                "versionNumber": version_number,
                "manifestFileName": "appsscript",
                "description": "Dashboard",
            },
        ).execute()
        dep_id = dep.get("deploymentId")
        print(f"  Nieuwe deployment aangemaakt: {dep_id}")
        for ep in dep.get("entryPoints", []):
            if ep.get("entryPointType") == "WEB_APP":
                print(f"  Deployment URL: {ep['webApp']['url']}")


def run():
    creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
    token_env = os.getenv("GOOGLE_TOKEN_JSON")
    if creds_env:
        CREDENTIALS.write_text(creds_env)
    if token_env:
        TOKEN_PATH.write_text(token_env)

    if not SCRIPT_ID:
        print("ERROR: APPS_SCRIPT_ID niet ingesteld in .env")
        raise SystemExit(1)

    if not CREDENTIALS.exists():
        print(f"ERROR: {CREDENTIALS} niet gevonden.")
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
