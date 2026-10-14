"""Grading: legs graded from final ESPN scores only. Flat 1u. No guessing:
props ESPN box scores can't settle -> 'ungraded' with note 'ungraded — no data'."""
import oddsmath


def grade_pick(pick, event, closing=None):
    """pick/event: sqlite rows or dicts. Returns (result, profit, clv, notes).
    result in win|loss|push|void|ungraded."""
    if event is None:
        return "ungraded", None, None, "ungraded — no data (event not matched)"
    if not event["is_final"]:
        return "ungraded", None, None, "event not final yet"
    hs, as_ = event["home_score"], event["away_score"]
    if hs is None or as_ is None:
        return "ungraded", None, None, "ungraded — no data (missing final score)"
    market = (pick["market"] or "").lower()
    team = (pick["team"] or "").lower()
    home = (event["home"] or "").lower()
    away = (event["away"] or "").lower()
    is_home = team and (team in home or home.split()[-1] in team)
    is_away = team and (team in away or away.split()[-1] in team)

    result = None
    if market == "moneyline":
        if not (is_home or is_away):
            return "ungraded", None, None, "ungraded — no data (team not matched to event)"
        won = (hs > as_) if is_home else (as_ > hs)
        if hs == as_:
            result = "push"
        else:
            result = "win" if won else "loss"
    elif market == "spread":
        if pick["line"] is None or not (is_home or is_away):
            return "ungraded", None, None, "ungraded — no data (spread line/team missing)"
        adj = (hs + pick["line"]) - as_ if is_home else (as_ + pick["line"]) - hs
        result = "win" if adj > 0 else ("push" if adj == 0 else "loss")
    elif market in ("total", "totals", "over/under"):
        if pick["line"] is None:
            return "ungraded", None, None, "ungraded — no data (total line missing)"
        tot = hs + as_
        over = "over" in (pick["selection"] or "").lower()
        if tot == pick["line"]:
            result = "push"
        else:
            result = "win" if (tot > pick["line"]) == over else "loss"
    elif market == "prop":
        reason = None
        try:
            reason = pick["_prop_reason"]
        except Exception:  # noqa: BLE001 - plain dict picks may carry it
            reason = pick.get("_prop_reason") if isinstance(pick, dict) else None
        return "ungraded", None, None, f"ungraded — {reason or 'no data (no prop resolver for this pick)'}"
    else:
        return "ungraded", None, None, f"ungraded — no data (market '{pick['market']}' not gradeable in v0)"

    odds = pick["odds_claimed"] or pick["odds_available"]
    profit = oddsmath.profit_units(result, odds) if odds else (0.0 if result in ("push", "void") else None)
    clv, _closing_odds, line_note = _clv_for(pick, closing, is_home, is_away)
    notes = "graded vs final ESPN score"
    if line_note:
        notes += f"; {line_note}"
    return result, profit, clv, notes


def _g(row, key):
    try:
        return row[key]
    except Exception:  # noqa: BLE001 - dict or sqlite Row, key may be absent
        return None


def _clv_for(pick, closing, is_home=None, is_away=None):
    """Fix 2: probability-based approx-CLV (percentage points, devigged) for
    moneyline, spread AND total picks. `closing` is the closing_lines row
    merged with its odds_snapshots prices (may be a plain closing_lines row —
    snapshot price keys then read as None and spread/total CLV falls back to
    the closing side price only). Returns (clv_pp, closing_odds_for_pick,
    line_move_note). Line movement for spread/total is recorded in the note:
    'line moved +1.0 pts vs your line' (positive = market moved your way)."""
    if closing is None:
        return None, None, ""
    market = (pick["market"] or "").lower()
    odds = pick["odds_claimed"] or pick["odds_available"]
    if not odds:
        return None, None, ""
    if is_home is None:
        team = (pick["team"] or "").lower()
        return None, None, ""  # caller without side info cannot price CLV
    if market == "moneyline":
        picked = _g(closing, "home_ml") if is_home else _g(closing, "away_ml")
        other = _g(closing, "away_ml") if is_home else _g(closing, "home_ml")
        return oddsmath.clv_pct(odds, picked, other), picked, ""
    if market == "spread":
        picked = _g(closing, "spread_home_price") if is_home else _g(closing, "spread_away_price")
        other = _g(closing, "spread_away_price") if is_home else _g(closing, "spread_home_price")
        note = ""
        close_line_home = _g(closing, "spread")
        if close_line_home is not None and pick["line"] is not None:
            close_line = close_line_home if is_home else -close_line_home
            moved = round(pick["line"] - close_line, 2)
            note = f"line: yours {pick['line']:+g} -> close {close_line:+g} (moved {moved:+g} pts)"
        return oddsmath.clv_pct(odds, picked, other), picked, note
    if market in ("total", "totals", "over/under"):
        over = "over" in (pick["selection"] or "").lower()
        picked = _g(closing, "over_price") if over else _g(closing, "under_price")
        other = _g(closing, "under_price") if over else _g(closing, "over_price")
        note = ""
        close_total = _g(closing, "total")
        if close_total is not None and pick["line"] is not None:
            moved = round((close_total - pick["line"]) * (1 if over else -1), 2)
            note = f"line: yours {pick['line']:g} -> close {close_total:g} (moved {moved:+g} pts)"
        return oddsmath.clv_pct(odds, picked, other), picked, note
    return None, None, ""


def grade_all(conn, dbmod):
    """Grade every TRACKED/PIPELINE_TEST pick whose event is final and ungraded."""
    picks = conn.execute(
        "SELECT p.*, e.home, e.away, e.home_score, e.away_score, e.is_final "
        "FROM picks p LEFT JOIN events e ON e.id=p.event_id "
        "WHERE p.status IN ('TRACKED','PIPELINE_TEST') "
        "AND NOT EXISTS (SELECT 1 FROM grades g WHERE g.pick_id=p.id)").fetchall()
    graded = 0
    for p in picks:
        closing = _closing_merged(conn, p["event_id"]) if p["event_id"] else None
        ev = {"home": p["home"], "away": p["away"], "home_score": p["home_score"],
              "away_score": p["away_score"], "is_final": p["is_final"],
              "commence_time": p["commence_time"]}
        # Fix 3: NHL props settle from the NHL official API game logs.
        if (p["market"] or "").lower() == "prop" and (p["sport"] or "") == "NHL" \
                and p["event_id"] and p["is_final"]:
            import nhlprops
            res, detail, terminal = nhlprops.try_grade(p, ev)
            if res is not None:
                odds_u = p["odds_claimed"] or p["odds_available"]
                profit = oddsmath.profit_units(res, odds_u) if odds_u else None
                conn.execute(
                    "INSERT OR IGNORE INTO grades(pick_id,graded_at,result,profit_units,"
                    "closing_odds,clv_pct,is_pipeline_test,notes) VALUES(?,?,?,?,?,?,?,?)",
                    (p["id"], dbmod.now_iso(), res, profit, None, None,
                     1 if p["status"] == "PIPELINE_TEST" else 0,
                     f"graded vs NHL API game log: {detail}"))
                graded += 1
                continue
            if not terminal:
                continue  # stats not posted yet — retry a later cycle
            p = dict(p)
            p["_prop_reason"] = detail
        result, profit, clv, notes = grade_pick(p, ev if p["event_id"] else None, closing)
        if result == "ungraded" and ("not final" in notes or "not matched" in notes
                                      or "missing final score" in notes):
            continue  # not terminal yet — leave ungraded for a later cycle
        team = (p["team"] or "").lower()
        home_l = (p["home"] or "").lower()
        away_l = (p["away"] or "").lower()
        is_home = bool(team) and (team in home_l or home_l.split()[-1] in team)
        is_away = bool(team) and (team in away_l or away_l.split()[-1] in team)
        _c, closing_odds, _n = _clv_for(p, closing, is_home, is_away)
        conn.execute(
            "INSERT OR IGNORE INTO grades(pick_id,graded_at,result,profit_units,closing_odds,clv_pct,is_pipeline_test,notes) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (p["id"], dbmod.now_iso(), result, profit, closing_odds, clv,
             1 if p["status"] == "PIPELINE_TEST" else 0, notes))
        graded += 1
    conn.commit()
    return graded


def _closing_merged(conn, event_id):
    """closing_lines row merged with its linked odds_snapshots prices
    (spread/total two-way prices live on the snapshot)."""
    row = conn.execute(
        "SELECT c.*, s.spread_home_price, s.spread_away_price, s.over_price, "
        "s.under_price FROM closing_lines c LEFT JOIN odds_snapshots s "
        "ON s.id=c.snapshot_id WHERE c.event_id=?", (event_id,)).fetchone()
    return dict(row) if row else None


def regrade_clv(conn):
    """Fix 2(d): recompute clv_pct (+ closing_odds) for every existing grade
    under the devigged probability formula. Returns before/after rows —
    grades has one row per pick (UNIQUE pick_id), so the returned table IS
    the preserved history note; callers must print/store it."""
    out = []
    rows = conn.execute(
        "SELECT g.pick_id, g.clv_pct AS old_clv, g.closing_odds AS old_close, "
        "p.*, e.home, e.away FROM grades g JOIN picks p ON p.id=g.pick_id "
        "LEFT JOIN events e ON e.id=p.event_id").fetchall()
    for r in rows:
        closing = _closing_merged(conn, r["event_id"]) if r["event_id"] else None
        team = (r["team"] or "").lower()
        home_l = (r["home"] or "").lower()
        away_l = (r["away"] or "").lower()
        is_home = bool(team) and (team in home_l or home_l.split()[-1] in team)
        is_away = bool(team) and (team in away_l or away_l.split()[-1] in team)
        new_clv, new_close, note = _clv_for(r, closing, is_home, is_away)
        conn.execute("UPDATE grades SET clv_pct=?, closing_odds=? WHERE pick_id=?",
                     (new_clv, new_close, r["pick_id"]))
        out.append({"pick_id": r["pick_id"], "market": r["market"],
                    "selection": r["selection"],
                    "odds": r["odds_claimed"] or r["odds_available"],
                    "old_clv": r["old_clv"], "new_clv": new_clv,
                    "old_close": r["old_close"], "new_close": new_close,
                    "line_note": note})
    conn.commit()
    return out
