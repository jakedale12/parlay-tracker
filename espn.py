"""ESPN unofficial scoreboard ingest (free, no key): events, scores, DK-sourced odds."""
import json
import urllib.request
from datetime import datetime, timezone

import config
import db as dbmod


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_scoreboard(payload, sport):
    """Yield normalized events from an ESPN scoreboard payload."""
    for ev in payload.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {}
        for c in comp.get("competitors", []):
            side = c.get("homeAway")
            teams[side] = {
                "name": (c.get("team") or {}).get("displayName") or c.get("name"),
                "abbr": (c.get("team") or {}).get("abbreviation"),
                "score": _to_int(c.get("score")),
            }
        status_obj = (ev.get("status") or {}).get("type") or {}
        odds = {}
        if comp.get("odds"):
            o = comp["odds"][0]
            ml = o.get("moneyline") or {}
            ps = o.get("pointSpread") or {}
            tt = o.get("total") or {}

            def close_odds(node):
                return _to_int(((node or {}).get("close") or {}).get("odds"))

            def close_line(node):
                return ((node or {}).get("close") or {}).get("line")

            odds = {
                "provider": (o.get("provider") or {}).get("name") or "ESPN/DK",
                "home_ml": close_odds(ml.get("home")),
                "away_ml": close_odds(ml.get("away")),
                "spread": _f(close_line(ps.get("home"))) if ps else _f(o.get("spread")),
                "spread_home_price": close_odds(ps.get("home")),
                "spread_away_price": close_odds(ps.get("away")),
                "total": _f(o.get("overUnder")),
                "over_price": close_odds(tt.get("over")),
                "under_price": close_odds(tt.get("under")),
                "details": o.get("details"),
            }
            odds["raw"] = {k: v for k, v in odds.items() if k != "raw"}
        yield {
            "espn_id": str(ev.get("id")), "sport": sport,
            "home": teams.get("home", {}), "away": teams.get("away", {}),
            "commence_time": ev.get("date"),
            "status": status_obj.get("name") or status_obj.get("description"),
            "completed": bool(status_obj.get("completed")),
            "odds": odds,
            "name": ev.get("name"),
        }


def ingest_scoreboard(conn, sport, dates=None):
    """Fetch + store events and an odds snapshot for each event that has odds.
    dates: optional YYYYMMDD for past/final boards (grading-pipeline tests)."""
    url = config.LEAGUES[sport]
    if dates:
        url = f"{url}?dates={dates}"
    status, payload = fetch_json(url)
    count = snaps = 0
    for ev in parse_scoreboard(payload, sport):
        conn.execute(
            "INSERT INTO events(espn_id,sport,home,away,commence_time,status,home_score,away_score,is_final,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sport,espn_id) DO UPDATE SET status=excluded.status,"
            "home_score=excluded.home_score, away_score=excluded.away_score, is_final=excluded.is_final,"
            "updated_at=excluded.updated_at, commence_time=COALESCE(events.commence_time, excluded.commence_time)",
            (ev["espn_id"], sport, ev["home"].get("name"), ev["away"].get("name"), ev["commence_time"],
             ev["status"], ev["home"].get("score"), ev["away"].get("score"),
             1 if ev["completed"] else 0, dbmod.now_iso()))
        eid = conn.execute("SELECT id FROM events WHERE sport=? AND espn_id=?",
                           (sport, ev["espn_id"])).fetchone()[0]
        o = ev.get("odds") or {}
        if o.get("home_ml") is not None or o.get("spread") is not None or o.get("total") is not None:
            conn.execute(
                "INSERT INTO odds_snapshots(event_id,captured_at,provider,home_ml,away_ml,spread,"
                "spread_home_price,spread_away_price,total,over_price,under_price,raw_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, dbmod.now_iso(), "ESPN/DK", o.get("home_ml"), o.get("away_ml"),
                 _f(o.get("spread")), o.get("spread_home_price"), o.get("spread_away_price"),
                 _f(o.get("total")), o.get("over_price"), o.get("under_price"),
                 json.dumps(o.get("raw") or {})))
            snaps += 1
        count += 1
    conn.commit()
    return {"http": status, "events": count, "odds_snapshots": snaps}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def update_closing_lines(conn):
    """Closing line = latest odds snapshot taken at/before T-5 min to start
    (or the last snapshot before start if the T-5 window was missed).
    Labelled approx-CLV: ESPN/DK close, NOT Pinnacle."""
    rows = conn.execute(
        "SELECT e.id AS eid, e.commence_time FROM events e WHERE e.commence_time IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM closing_lines c WHERE c.event_id=e.id)").fetchall()
    made = 0
    now = dbmod.now_iso()
    for r in rows:
        # only close events that have started
        if now < r["commence_time"]:
            continue
        snap = conn.execute(
            "SELECT * FROM odds_snapshots WHERE event_id=? AND captured_at<=? "
            "ORDER BY captured_at DESC LIMIT 1", (r["eid"], r["commence_time"])).fetchone()
        if not snap:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO closing_lines(event_id,snapshot_id,captured_at,label,home_ml,away_ml,spread,total) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (r["eid"], snap["id"], snap["captured_at"], config.LINE_LABEL,
             snap["home_ml"], snap["away_ml"], snap["spread"], snap["total"]))
        made += 1
    conn.commit()
    return made


def _team_matches(name, team):
    n, t = (name or "").lower(), (team or "").lower()
    return bool(t) and (t in n or (t.split() and t.split()[-1] in n))


def _closest(cands):
    """Event closest to now (a just-started game must still match, so the
    captured_at < commence_time rule can EXCLUDE it as late — an unmatched
    pick would leak through as tracked)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    def dist(r):
        try:
            dt = datetime.fromisoformat((r["commence_time"] or "").replace("Z", "+00:00"))
        except ValueError:
            return 10**9
        return abs((dt - now).total_seconds())

    return min(cands, key=dist) if cands else None


def match_event(conn, sport, team_name, on_date=None):
    """Match a team to the event closest to now (or on on_date if given)."""
    if not team_name:
        return None
    cands = [r for r in conn.execute(
        "SELECT * FROM events WHERE sport=?", (sport,)).fetchall()
        if _team_matches(r["home"], team_name) or _team_matches(r["away"], team_name)]
    if on_date:
        cands = [r for r in cands if (r["commence_time"] or "").startswith(on_date)]
    return _closest(cands)


def match_matchup(conn, sport, away, home, on_date=None):
    """Match an event by BOTH teams (preferred when the source gives a matchup)."""
    cands = [r for r in conn.execute(
        "SELECT * FROM events WHERE sport=?", (sport,)).fetchall()
        if (_team_matches(r["away"], away) and _team_matches(r["home"], home))
        or (_team_matches(r["away"], home) and _team_matches(r["home"], away))]
    if on_date:
        cands = [r for r in cands if (r["commence_time"] or "").startswith(on_date)]
    return _closest(cands)
