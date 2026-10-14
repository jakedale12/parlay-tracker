"""Parlay Researcher & Tracker v0 — configuration (stdlib only, $0 stack)."""
import os

DATA_DIR = os.environ.get("PARLAY_DATA", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.environ.get("PARLAY_DB", os.path.join(DATA_DIR, "parlay.db"))

NTFY_SERVER = os.environ.get("PARLAY_NTFY_SERVER", "http://localhost:2586")
NTFY_TOPIC = os.environ.get("PARLAY_NTFY_TOPIC", "parlay-alerts")

DASHBOARD_PORT = int(os.environ.get("PARLAY_PORT", "8796"))
POLL_SECONDS = int(os.environ.get("PARLAY_POLL_SECONDS", "900"))  # ~15 min

TZ_DISPLAY = "America/Toronto"

LEAGUES = {
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
}

LINE_LABEL = "approx-CLV: ESPN/DK close"  # never label this Pinnacle/sharp
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36 parlay-tracker/0.1"
