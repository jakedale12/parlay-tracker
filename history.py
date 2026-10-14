"""History / baseline layer — CONTEXT ONLY.

Backfills completed games from ESPN's free scoreboard into SEPARATE tables
(hist_events / hist_results / baselines). These rows are league history used
for baselines on the dashboard. They are NEVER picks: nothing here is read by
records.py, consensus.py or grading of the picks ledger, and no historical
game can ever count toward a capper scorecard, ROI, consensus or the
followable bar.

What ESPN free actually serves (VERIFIED LIVE 2026-10-13):
- NHL month chunks: ?dates=YYYYMM returns the whole month in ONE call
  (Oct 2025 = 208 events). Range syntax ?dates=YYYYMMDD-YYYYMMDD = HTTP 400.
  ?dates=YYYY caps at 1000 events and limit>1000 breaks — months are the way.
- NFL: ?dates=2025&week=N&seasontype=2 (regular) / =3 (playoffs), one week
  per call (week 1 = 16 games).
- Past-date payloads carry NO odds block (with_odds = 0 across every past
  payload probed). So historical moneylines/spreads/totals and historical
  closing lines are NOT AVAILABLE FREE from ESPN. Baselines that need odds
  (favourite win %) are reported as not available — never faked. The only
  close-movement stats come from our OWN closing_lines archive (approx,
  small n, labelled).
"""
import json
import statistics

import config
import db as dbmod
import espn

NHL_SEASON_LABEL = "2025-26"
NHL_MONTHS = ["202510", "202511", "202512", "202601", "202602",
              "202603", "202604", "202605", "202606"]
NHL_WINDOW = ("2025-10-07T00:00:00Z", "2026-07-01T00:00:00Z")  # reg season + playoffs
NFL_SEASON_LABEL = "2025"
NFL_WINDOW = ("2025-09-01T00:00:00Z", "2026-03-01T00:00:00Z")

COMMON_TOTALS = {"NHL": [5.5, 6.5], "NFL": [41.5, 44.5, 47.5]}


def _store_payload(conn, payload, sport, season_label, window):
    """Store completed finals from one scoreboard payload. Returns count stored."""
    stored = 0
    for ev in espn.parse_scoreboard(payload, sport):
        if not ev["completed"]:
            continue
        ct = ev["commence_time"] or ""
        if not (window[0] <= ct < window[1]):
            continue
        if "Pro Bowl" in (ev.get("name") or ""):
            continue  # exhibition, not a league game — would pollute NFL baselines
        if sport == "NFL" and {ev["home"].get("name"), ev["away"].get("name")} & {"NFC", "AFC"}:
            continue  # Pro Bowl Games list teams as 'NFC'/'AFC' (name has no 'Pro Bowl')
        hs, as_ = ev["home"].get("score"), ev["away"].get("score")
        if hs is None or as_ is None:
            continue
        o = ev.get("odds") or {}
        ts = dbmod.now_iso()
        conn.execute(
            "INSERT INTO hist_events(sport,espn_id,season_label,home,away,commence_time,"
            "status,home_score,away_score,is_final,home_ml,away_ml,spread,total,fetched_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?) "
            "ON CONFLICT(sport,espn_id) DO UPDATE SET home_score=excluded.home_score,"
            "away_score=excluded.away_score,status=excluded.status,fetched_at=excluded.fetched_at",
            (sport, ev["espn_id"], season_label, ev["home"].get("name"),
             ev["away"].get("name"), ct, ev["status"], hs, as_,
             o.get("home_ml"), o.get("away_ml"), o.get("spread"), o.get("total"), ts))
        hid = conn.execute("SELECT id FROM hist_events WHERE sport=? AND espn_id=?",
                           (sport, ev["espn_id"])).fetchone()[0]
        winner = "home" if hs > as_ else ("away" if as_ > hs else "tie")
        conn.execute(
            "INSERT INTO hist_results(hist_event_id,sport,home_score,away_score,"
            "winner_side,total_score,fetched_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(hist_event_id) DO UPDATE SET home_score=excluded.home_score,"
            "away_score=excluded.away_score,winner_side=excluded.winner_side,"
            "total_score=excluded.total_score",
            (hid, sport, hs, as_, winner, hs + as_, ts))
        stored += 1
    conn.commit()
    return stored


def backfill_nhl(conn):
    """Full 2025-26 NHL season finals: 9 month calls, window-filtered, deduped."""
    calls = stored = 0
    per_month = {}
    for ym in NHL_MONTHS:
        url = f"{config.LEAGUES['NHL']}?dates={ym}&limit=1000"
        calls += 1
        try:
            _, payload = espn.fetch_json(url)
        except Exception as e:  # noqa: BLE001 — log and continue, never retry-loop
            per_month[ym] = {"error": str(e)}
            continue
        n = _store_payload(conn, payload, "NHL", NHL_SEASON_LABEL, NHL_WINDOW)
        per_month[ym] = n
        stored += n
    total = conn.execute(
        "SELECT COUNT(*) FROM hist_events WHERE sport='NHL' AND season_label=?",
        (NHL_SEASON_LABEL,)).fetchone()[0]
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "History NHL",
                  f"backfill {NHL_SEASON_LABEL}: {calls} ESPN month calls, "
                  f"{total} finals stored (window {NHL_WINDOW[0]}..{NHL_WINDOW[1]})"))
    conn.commit()
    return {"league": "NHL", "season": NHL_SEASON_LABEL, "calls": calls,
            "stored_this_run": stored, "games_stored": total, "per_month": per_month}


def backfill_nfl(conn):
    """Full 2025 NFL season finals (regular + playoffs): one call per week."""
    calls = stored = 0
    per_week = {}
    plan = [(2, w) for w in range(1, 19)] + [(3, w) for w in range(1, 6)]
    for seasontype, week in plan:
        url = (f"{config.LEAGUES['NFL']}?dates={NFL_SEASON_LABEL}"
               f"&week={week}&seasontype={seasontype}&limit=1000")
        calls += 1
        try:
            _, payload = espn.fetch_json(url)
        except Exception as e:  # noqa: BLE001
            per_week[f"st{seasontype}w{week}"] = {"error": str(e)}
            continue
        n = _store_payload(conn, payload, "NFL", NFL_SEASON_LABEL, NFL_WINDOW)
        per_week[f"st{seasontype}w{week}"] = n
        stored += n
    total = conn.execute(
        "SELECT COUNT(*) FROM hist_events WHERE sport='NFL' AND season_label=?",
        (NFL_SEASON_LABEL,)).fetchone()[0]
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "History NFL",
                  f"backfill {NFL_SEASON_LABEL}: {calls} ESPN week calls, "
                  f"{total} finals stored (Pro Bowl excluded)"))
    conn.commit()
    return {"league": "NFL", "season": NFL_SEASON_LABEL, "calls": calls,
            "stored_this_run": stored, "games_stored": total, "per_week": per_week}


def backfill_all(conn):
    out = {"nhl": backfill_nhl(conn), "nfl": backfill_nfl(conn)}
    out["baselines"] = compute_baselines(conn)
    return out


# ---------------- Baselines ----------------

def _pct(num, den):
    return round(num / den * 100, 1) if den else None


def _close_movement(conn, sport):
    """Close-movement stats from OUR OWN closing_lines archive only
    (approx — ESPN/DK, small n by construction early on). Movement =
    closing line minus the EARLIEST odds snapshot for the same event."""
    rows = conn.execute(
        "SELECT cl.total AS close_total, cl.home_ml AS close_ml, "
        "(SELECT os.total FROM odds_snapshots os WHERE os.event_id=cl.event_id "
        " ORDER BY os.captured_at, os.id LIMIT 1) AS first_total, "
        "(SELECT os.home_ml FROM odds_snapshots os WHERE os.event_id=cl.event_id "
        " ORDER BY os.captured_at, os.id LIMIT 1) AS first_ml "
        "FROM closing_lines cl JOIN events e ON e.id=cl.event_id WHERE e.sport=?",
        (sport,)).fetchall()
    tot_moves = [abs(r["close_total"] - r["first_total"]) for r in rows
                 if r["close_total"] is not None and r["first_total"] is not None]
    ml_moves = [abs(r["close_ml"] - r["first_ml"]) for r in rows
                if r["close_ml"] is not None and r["first_ml"] is not None]
    return {
        "source": "our own closing_lines archive (approx — ESPN/DK close, not Pinnacle)",
        "n_events_with_close": len(rows),
        "avg_abs_total_move": round(sum(tot_moves) / len(tot_moves), 3) if tot_moves else None,
        "pct_totals_moved_half_or_more": (
            _pct(sum(1 for m in tot_moves if m >= 0.5), len(tot_moves)) if tot_moves else None),
        "avg_abs_home_ml_move": round(sum(ml_moves) / len(ml_moves), 1) if ml_moves else None,
    }


def compute_baselines(conn):
    """Per-league baselines from hist_results/hist_events finals. Odds-based
    baselines (favourite win %) are only computed when historical odds were
    actually present in the payloads (they are not, free — see module doc)."""
    out = {}
    for sport in ("NHL", "NFL"):
        rows = conn.execute(
            "SELECT h.*, r.winner_side, r.total_score FROM hist_events h "
            "JOIN hist_results r ON r.hist_event_id=h.id WHERE h.sport=? AND h.is_final=1",
            (sport,)).fetchall()
        if not rows:
            continue
        n = len(rows)
        ties = sum(1 for r in rows if r["winner_side"] == "tie")
        home_wins = sum(1 for r in rows if r["winner_side"] == "home")
        totals = sorted(r["total_score"] for r in rows)
        with_ml = [r for r in rows if r["home_ml"] is not None and r["away_ml"] is not None]
        fav = None
        if with_ml:
            fav_games = [r for r in with_ml if r["home_ml"] != r["away_ml"]]
            fav_wins = sum(1 for r in fav_games
                           if (r["home_ml"] < r["away_ml"] and r["winner_side"] == "home")
                           or (r["away_ml"] < r["home_ml"] and r["winner_side"] == "away"))
            fav = _pct(fav_wins, len(fav_games))
        scope = (f"{rows[0]['season_label']} season finals "
                 f"({min(r['commence_time'] for r in rows)[:10]}.."
                 f"{max(r['commence_time'] for r in rows)[:10]})")
        metrics = {
            "games_n": n, "ties": ties,
            "home_win_pct": _pct(home_wins, n - ties),
            "avg_total": round(sum(totals) / n, 2),
            "median_total": statistics.median(totals),
            "total_p25": totals[int(0.25 * (n - 1))], "total_p75": totals[int(0.75 * (n - 1))],
            "over_pct": {str(line): _pct(sum(1 for t in totals if t > line), n)
                         for line in COMMON_TOTALS[sport]},
            "favourite_win_pct": fav,
            "favourite_note": (None if fav is not None else
                               "not available free — ESPN past-date payloads carry no odds"),
            "odds_rows_n": len(with_ml),
            "close_movement": _close_movement(conn, sport),
        }
        conn.execute(
            "INSERT INTO baselines(league,scope,games_n,computed_at,metrics_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(league,scope) DO UPDATE SET "
            "games_n=excluded.games_n, computed_at=excluded.computed_at, "
            "metrics_json=excluded.metrics_json",
            (sport, scope, n, dbmod.now_iso(), json.dumps(metrics)))
        conn.commit()
        out[sport] = {"scope": scope, **metrics}
    return out


def get_baselines(conn):
    out = []
    for r in conn.execute("SELECT * FROM baselines ORDER BY league").fetchall():
        d = dict(r)
        d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
        out.append(d)
    return out


# ---------------- Sanity checks ----------------

def sanity_check(conn, sport="NHL", month="202510"):
    """(1) Recompute one month's home win % by a SECOND path — re-fetch the
    ESPN month payload and aggregate in memory with parse_scoreboard —
    and compare against the stored hist rows. (2) Re-run grading.grade_pick
    on real stored finals (ML win/loss, total over, spread push)."""
    import grading
    _, payload = espn.fetch_json(f"{config.LEAGUES[sport]}?dates={month}&limit=1000")
    ym = f"{month[:4]}-{month[4:]}"
    mem_hw = mem_decided = 0
    for ev in espn.parse_scoreboard(payload, sport):
        if not ev["completed"] or not (ev["commence_time"] or "").startswith(ym):
            continue
        if not (NHL_WINDOW[0] <= ev["commence_time"] < NHL_WINDOW[1]):
            continue
        hs, as_ = ev["home"].get("score"), ev["away"].get("score")
        if hs is None or as_ is None or hs == as_:
            continue
        mem_decided += 1
        mem_hw += 1 if hs > as_ else 0
    mem_pct = _pct(mem_hw, mem_decided)
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(winner_side='home') AS hw FROM hist_results "
        "WHERE sport=? AND hist_event_id IN (SELECT id FROM hist_events "
        "WHERE sport=? AND substr(commence_time,1,7)=?) AND winner_side != 'tie'",
        (sport, sport, ym)).fetchone()
    sql_pct = _pct(row["hw"] or 0, row["n"] or 0)
    month_check = {"month": ym, "memory_home_win_pct": mem_pct, "memory_n": mem_decided,
                   "sql_home_win_pct": sql_pct, "sql_n": row["n"],
                   "match": mem_pct == sql_pct and mem_decided == row["n"]}

    grade_checks = []
    finals = conn.execute(
        "SELECT * FROM hist_events WHERE sport=? AND is_final=1 AND home_score IS NOT NULL "
        "ORDER BY commence_time LIMIT 3", (sport,)).fetchall()
    for ev in finals:
        hs, as_, home, away = ev["home_score"], ev["away_score"], ev["home"], ev["away"]
        winner, loser = (home, away) if hs > as_ else (away, home)
        cases = [
            ("moneyline winner", {"market": "moneyline", "selection": f"{winner} ML",
                                  "team": winner, "line": None, "odds_claimed": -110,
                                  "odds_available": None}, "win"),
            ("moneyline loser", {"market": "moneyline", "selection": f"{loser} ML",
                                 "team": loser, "line": None, "odds_claimed": -110,
                                 "odds_available": None}, "loss"),
            ("total over (line=total-0.5)", {"market": "total", "selection": "Over",
                                             "team": None, "line": hs + as_ - 0.5,
                                             "odds_claimed": -110,
                                             "odds_available": None}, "win"),
            ("spread push (home line=away-home)", {"market": "spread", "selection": home,
                                                   "team": home, "line": float(as_ - hs),
                                                   "odds_claimed": -110,
                                                   "odds_available": None}, "push"),
        ]
        evd = {"home": home, "away": away, "home_score": hs, "away_score": as_,
               "is_final": 1}
        for name, pick, expected in cases:
            got = grading.grade_pick(pick, evd)[0]
            grade_checks.append({"game": f"{away} @ {home} {as_}-{hs}", "case": name,
                                 "expected": expected, "got": got,
                                 "ok": got == expected})
    return {"month_check": month_check, "grade_checks": grade_checks,
            "all_grade_checks_ok": all(c["ok"] for c in grade_checks)}
