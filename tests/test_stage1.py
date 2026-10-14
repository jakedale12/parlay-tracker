import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_data"

import db as dbmod  # noqa: E402
import grading  # noqa: E402
import manual  # noqa: E402
import oddsmath  # noqa: E402
import records  # noqa: E402
import sources  # noqa: E402


def fresh_conn():
    path = "/tmp/parlay_test_data/test.db"
    if os.path.exists(path):
        os.remove(path)
    return dbmod.connect(path)


def test_odds_conversion_and_roi():
    assert oddsmath.american_to_decimal(-110) == 1 + 100 / 110
    assert oddsmath.american_to_decimal(150) == 2.5
    assert oddsmath.profit_units("win", 150) == 1.5
    assert oddsmath.profit_units("win", -200) == 0.5
    assert oddsmath.profit_units("loss", 150) == -1.0
    assert oddsmath.profit_units("push", 150) == 0.0
    assert oddsmath.roi_pct([1.5, -1.0]) == 25.0
    assert oddsmath.clv_pct(120, 100) > 0  # got +120, closed +100 -> beat close


def test_late_pick_excluded_and_stored():
    conn = fresh_conn()
    sid = dbmod.upsert_source(conn, "T", "manual", None, "active")
    cid = dbmod.upsert_capper(conn, sid, "Late Larry")
    pick = {"source_id": sid, "capper_id": cid, "sport": "NHL", "market": "moneyline",
            "selection": "Leafs ML", "team": "Toronto Maple Leafs", "odds_claimed": -150,
            "captured_at": "2026-10-14T00:30:00Z", "commence_time": "2026-10-13T23:00:00Z",
            "raw_text": "late pick"}
    pid, status = dbmod.insert_pick(conn, pick)
    assert status == "EXCLUDED_LATE"
    row = conn.execute("SELECT * FROM picks WHERE id=?", (pid,)).fetchone()
    assert row is not None and row["status"] == "EXCLUDED_LATE"  # stored, not dropped
    # early pick stays TRACKED
    pick["captured_at"] = "2026-10-13T20:00:00Z"
    _, status2 = dbmod.insert_pick(conn, pick)
    assert status2 == "TRACKED"


def _grade(market, selection, team, line, hs, as_):
    pick = {"market": market, "selection": selection, "team": team, "line": line,
            "odds_claimed": -110, "odds_available": None}
    ev = {"home": "Toronto Maple Leafs", "away": "Boston Bruins",
          "home_score": hs, "away_score": as_, "is_final": 1}
    return grading.grade_pick(pick, ev)


def test_grading_win_loss_push():
    assert _grade("moneyline", "x", "Toronto Maple Leafs", None, 4, 2)[0] == "win"
    assert _grade("moneyline", "x", "Toronto Maple Leafs", None, 2, 4)[0] == "loss"
    assert _grade("spread", "x", "Toronto Maple Leafs", -1.5, 4, 2)[0] == "win"
    assert _grade("spread", "x", "Toronto Maple Leafs", -2.0, 4, 2)[0] == "push"
    assert _grade("total", "Over 6.5", None, 6.5, 4, 2)[0] == "loss"
    assert _grade("total", "Over 6.5", None, 6.5, 5, 2)[0] == "win"
    r = _grade("prop", "Matthews over 0.5 goals", "Toronto Maple Leafs", None, 4, 2)
    assert r[0] == "ungraded" and "no data" in r[3]


def test_consensus_thresholds():
    assert records.sample_label(50).startswith("insufficient")
    assert records.sample_label(150).startswith("warming up")
    assert records.sample_label(500).startswith("followable")
    assert records.consensus_eligible(150, 2) is True       # warming + 2 independent
    assert records.consensus_eligible(150, 1) is False       # needs 2+ independent
    assert records.consensus_eligible(50, 3) is False        # sources not warming up
    assert records.consensus_eligible(600, 2) is True


def test_manual_parse():
    p = manual.parse_manual(
        "PICK | capper=HNICaps | sport=NHL | market=spread | team=Boston Bruins | line=-1.5 | odds=+130")
    assert p["capper"] == "HNICaps" and p["line"] == -1.5 and p["odds_claimed"] == 130


def test_match_event_prefers_closest():
    import espn
    from datetime import datetime, timedelta, timezone
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    soon = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    for espn_id, ct, sc in (("1", old, 1), ("2", soon, 0)):
        conn.execute(
            "INSERT INTO events(espn_id,sport,home,away,commence_time,status,is_final,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (espn_id, "NHL", "Toronto Maple Leafs", "Boston Bruins", ct, "x", sc, old))
    conn.commit()
    ev = espn.match_event(conn, "NHL", "Toronto Maple Leafs")
    assert ev["espn_id"] == "2"  # tonight's game, not yesterday's final


def test_covers_parse_sample():
    html = ('<div id="computer-picks-1" data-pick data-pick-teams="Boston Bruins,Toronto Maple Leafs">'
            " Team Predicted Score ML Total Puck Line BOS BOS 3.10 -120 Caesars Odds o6.5 "
            " TOR TOR 2.80 +100 bet365 u6.5 </div>"
            '<div id="computer-picks-2" data-pick data-pick-teams="A,B"></div>')
    picks = sources.parse_covers_nhl(html)
    assert len(picks) == 1
    assert picks[0]["pick_team"] == "Boston Bruins" and picks[0]["odds"] == -120
