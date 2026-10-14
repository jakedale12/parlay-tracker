"""Fix 3: NHL prop grading via the NHL's free official API (api-web.nhle.com,
no key). NOTE: /v1/game-center/{id}/boxscore returns 404 for every game from
both the build VM and the tracker container (verified 2026-10-13) — the
working official route is roster + player game-log:
  GET /v1/roster/{ABBREV}/current            -> player ids by name
  GET /v1/player/{id}/game-log/{season}/{2}  -> per-game goals/assists/points/shots
A prop settles only when the captured text parses to player + stat + line +
over/under. Anything else stays ungraded with a specific reason — never guessed.
Responses are cached in-process (the serve loop is one long-lived process)."""
import json
import re
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import netutil

BASE = "https://api-web.nhle.com/v1"
ET = ZoneInfo("America/Toronto")

# ESPN full team name -> NHL API abbreviation
TEAM_ABBREV = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI", "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA", "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH", "New Jersey Devils": "NJD",
    "New York Islanders": "NYI", "New York Rangers": "NYR",
    "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA", "St Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR",
    "Utah Mammoth": "UTA", "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}

STAT_KEYS = {"goals": "goals", "assists": "assists", "points": "points",
             "shots": "shots", "shots on goal": "shots", "sog": "shots"}

_cache = {}


def _get(path):
    if path in _cache:
        return _cache[path]
    req = urllib.request.Request(BASE + path,
                                 headers={"User-Agent": "parlay-tracker/1.0"})
    with netutil.open_url(req, timeout=25) as r:
        data = json.load(r)
    _cache[path] = data
    return data


def parse_prop(selection):
    """(player, stat_key, line, direction) or (None, reason)."""
    s = re.sub(r"\s+", " ", (selection or "")).strip()
    m = re.match(r"^(.+?)\s+Anytime Goalscorer$", s, re.I) or \
        re.match(r"^(.+?)\s+Anytime Goal Scorer$", s, re.I)
    if m:
        return m.group(1).strip(), "goals", 0.5, "over"
    m = re.match(r"^(.+?)\s+(Over|Under)\s+(\d+(?:\.\d+)?)\s+"
                 r"(Shots on Goal|Shots|Points|Goals|Assists|SOG)\b", s, re.I)
    if m:
        return (m.group(1).strip(), STAT_KEYS[m.group(4).lower()],
                float(m.group(3)), m.group(2).lower())
    if " and " in s and re.search(r"\d\+\s*Point", s):
        return None, "multi-player combo prop — not settleable in v1"
    return None, f"prop text not parseable to player+stat+line: '{s[:60]}'"


def _season_id(d):
    return f"{d.year}{d.year + 1}" if d.month >= 9 else f"{d.year - 1}{d.year}"


def _find_player_id(abbrev, player_name):
    roster = _get(f"/roster/{abbrev}/current")
    last = player_name.split()[-1].lower()
    first = player_name.split()[0].lower()
    for grp in ("forwards", "defensemen", "goalies"):
        for p in roster.get(grp, []):
            ln = p["lastName"]["default"].lower()
            fn = p["firstName"]["default"].lower()
            if ln == last and (fn == first or fn.startswith(first[0])):
                return p["id"]
    return None


def try_grade(pick, event):
    """Returns (result, stat_line_str) or (None, reason, terminal).
    result in win|loss|push. Never guesses: any gap -> (None, reason, ...)."""
    res = parse_prop(pick["selection"])
    if res[0] is None:
        return None, res[1], True  # unparseable text: terminal, specific reason
    player, stat, line, direction = res
    home_ab = TEAM_ABBREV.get(event["home"])
    away_ab = TEAM_ABBREV.get(event["away"])
    if not home_ab or not away_ab:
        return None, "team not in NHL abbrev map", True
    try:
        commence = datetime.fromisoformat(
            (event["commence_time"] or "").replace("Z", "+00:00"))
    except ValueError:
        return None, "event commence_time unparseable", True
    game_date = commence.astimezone(ET).date()
    pid = None
    for ab, opp in ((home_ab, away_ab), (away_ab, home_ab)):
        try:
            pid = _find_player_id(ab, player)
        except Exception as e:  # noqa: BLE001
            return None, f"NHL API roster fetch failed: {e}", False
        if pid:
            break
    if not pid:
        return None, f"player '{player}' not found on either roster", True
    try:
        log = _get(f"/player/{pid}/game-log/{_season_id(game_date)}/2")
    except Exception as e:  # noqa: BLE001
        return None, f"NHL API game-log fetch failed: {e}", False
    entry = next((g for g in log.get("gameLog", [])
                  if g.get("gameDate") == game_date.isoformat()), None)
    if entry is None:
        return None, "game log entry not posted yet for this game", False
    value = entry.get(stat)
    if value is None:
        return None, f"stat '{stat}' missing in game log", True
    if value == line:
        result = "push"
    elif direction == "over":
        result = "win" if value > line else "loss"
    else:
        result = "win" if value < line else "loss"
    return result, f"{player} {stat}={value} vs line {line:g} ({direction})", True
