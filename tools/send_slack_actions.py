"""
Verstuurt Slack actiepunten op basis van SLACK_DATA env var.
Aangeroepen door GitHub Actions workflow send_slack_actions.yml.
"""
import json
import os
import sys

import requests

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_DATA_JSON = os.environ.get("SLACK_DATA", '{"actions":[]}')

CHANNELS = {
    "marketing":      "#marketing",
    "klantenservice": "#klantenservice",
    "retail":         "#support-team-retail",
}
LABELS = {
    "marketing":      "Marketing Push",
    "klantenservice": "Annuleren",
    "retail":         "Andere Workshop in Plaats",
}
EMOJIS = {
    "marketing":      "\U0001f4e3",
    "klantenservice": "❌",
    "retail":         "\U0001f504",
}


def main():
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN niet ingesteld")
        sys.exit(1)

    data = json.loads(SLACK_DATA_JSON)
    actions = data.get("actions", [])
    if not actions:
        print("Geen actiepunten om te versturen.")
        return

    by_channel: dict[str, list] = {}
    for a in actions:
        by_channel.setdefault(a["action_id"], []).append(a)

    sent = 0
    for ch, items in by_channel.items():
        channel = CHANNELS.get(ch, f"#{ch}")
        label   = LABELS.get(ch, ch)
        emoji   = EMOJIS.get(ch, "")
        n = len(items)
        lines = [
            f"• *{a['workshop']}* — {a['location']} — {a['date']} {a['time']}"
            + (f" — `{a['available_spots']} vrij`" if a.get("available_spots") else "")
            for a in items
        ]
        blocks = [
            {"type": "header",  "text": {"type": "plain_text", "text": f"{emoji} Actiepunt: {label}"}},
            {"type": "section", "text": {"type": "mrkdwn",     "text": f"*{n} sessie{'s' if n > 1 else ''}:*\n" + "\n".join(lines)}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "BBQ Experience Center · Dashboard actiepunt"}]},
        ]
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": channel, "blocks": blocks},
            timeout=10,
        )
        result = resp.json()
        if result.get("ok"):
            sent += len(items)
            print(f"  {channel}: {len(items)} actiepunt(en) verstuurd")
        else:
            print(f"  Slack fout voor {channel}: {result.get('error')}")

    print(f"Totaal verstuurd: {sent} actiepunt(en)")


if __name__ == "__main__":
    main()
