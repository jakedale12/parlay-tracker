"""Stage 6 additions: (1) history/baseline layer + TRACKED vs CLAIMED
separation, (2) Jake as a tracked source (own family) + dashboard log-my-pick."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_data3"

import dashboard  # noqa: E402
import db as dbmod  # noqa: E402
import grading  # noqa: E402
import history  # noqa: E402
import manual  # noqa: E402
import records  # noqa: E402
import consensus  # noqa: E402

DB = "/tmp/parlay_test_data3/test6.db"


def fresh_conn():
    if os.path.exists(DB):
        os.remove(DB)
    return dbmod.connect(DB)


def _payload_event(eid, date, home, away, hs, as_):
    return {"id": eid, "date": date, "name": f"{away} at {home}",
            "status": {"type": {"name": "Final", "completed": True}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": home, "abbreviation": "H"},
                 "score": str(hs)},
                {"homeAway": "away", "team": {"displayName": away, "abbreviation": "A"},
                 "score": str(as_)}]}]}


def test_hist_backfill_store_and_baselines():
    conn = fresh_conn()
    games = [  # (home, away, hs, as_) totals 6,7,5,8; home wins 3/4
        ("Toronto Maple Leafs", "Boston Bruins", 4, 2),
        ("Boston Bruins", "Toronto Maple Leafs", 3, 4),
        ("New York Rangers", "Boston Bruins", 3, 2),
        ("Boston Bruins", "New York Rangers", 5, 3),
    ]
    payload = {"events": [_payload_event(str(i + 1), f"2025-10-{10 + i}T23:00Z",
                                         h, a, hs, as_)
                          for i, (h, a, hs, as_) in enumerate(games)]}
    n = history._store_payload(conn, payload, "NHL", "2025-26", history.NHL_WINDOW)
    assert n == 4
    # history lives in SEPARATE tables — the picks ledger stays empty
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM hist_results").fetchone()[0] == 4
    out = history.compute_baselines(conn)
    m = out["NHL"]
    assert m["games_n"] == 4 and m["home_win_pct"] == 75.0
    assert m["avg_total"] == 6.5 and m["median_total"] == 6.5
    assert m["over_pct"]["5.5"] == 75.0 and m["over_pct"]["6.5"] == 50.0
    # past ESPN payloads carry no odds -> favourite baseline honestly unavailable
    assert m["favourite_win_pct"] is None and "not available free" in m["favourite_note"]
    assert m["odds_rows_n"] == 0
    # re-running the store is idempotent (dedupe by espn_id)
    assert history._store_payload(conn, payload, "NHL", "2025-26",
                                  history.NHL_WINDOW) == 4
    assert conn.execute("SELECT COUNT(*) FROM hist_events").fetchone()[0] == 4


def test_grading_regression_on_hist_final():
    # known final: home wins 4-2 (total 6)
    ev = {"home": "Toronto Maple Leafs", "away": "Boston Bruins",
          "home_score": 4, "away_score": 2, "is_final": 1}
    base = {"odds_claimed": -110, "odds_available": None, "line": None}
    assert grading.grade_pick({**base, "market": "moneyline",
                               "selection": "Toronto Maple Leafs ML",
                               "team": "Toronto Maple Leafs"}, ev)[0] == "win"
    assert grading.grade_pick({**base, "market": "moneyline",
                               "selection": "Boston Bruins ML",
                               "team": "Boston Bruins"}, ev)[0] == "loss"
    assert grading.grade_pick({**base, "market": "total", "selection": "Over 5.5",
                               "team": None, "line": 5.5}, ev)[0] == "win"
    assert grading.grade_pick({**base, "market": "spread",
                               "selection": "Toronto Maple Leafs -2.0",
                               "team": "Toronto Maple Leafs", "line": -2.0}, ev)[0] == "push"


def test_claimed_only_capper_has_no_tracked_scorecard():
    conn = fresh_conn()
    sid = dbmod.upsert_source(conn, "CapperTek", "leaderboard", None, "seed_only")
    dbmod.upsert_capper(conn, sid, "Claimed Carl",
                        claimed_record="99-1-0 (99%) | +999.00 | +999%")
    # CLAIMED data must NEVER produce a tracked scorecard / ROI / source record
    assert records.capper_records(conn) == []
    assert records.source_records(conn) == []
    assert records.tracked_counts_by_source(conn) == {}
    # ...but it renders, in its own labelled CLAIMED section, alongside TRACKED labels
    page = dashboard.render(conn)
    assert dashboard.CLAIMED_LABEL in page and dashboard.TRACKED_LABEL in page
    assert "Claimed Carl" in page.split("Claimed records")[1]  # in CLAIMED section


def _seed_event(conn, espn_id, hours_from_now, home="Toronto Maple Leafs",
                away="Boston Bruins"):
    now = datetime.now(timezone.utc)
    commence = (now + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO events(espn_id,sport,home,away,commence_time,status,is_final,updated_at) "
        "VALUES(?,?,?,?,?,?,0,?)", (espn_id, "NHL", home, away, commence, "pre", commence))
    conn.commit()
    row = conn.execute("SELECT * FROM events WHERE espn_id=?", (espn_id,)).fetchone()
    return row


def _pick_source_name(conn, pid):
    return conn.execute(
        "SELECT s.name, s.kind FROM picks p JOIN sources s ON s.id=p.source_id "
        "WHERE p.id=?", (pid,)).fetchone()


def test_jake_source_own_family_and_rules():
    conn = fresh_conn()
    ev = _seed_event(conn, "j1", hours_from_now=3)
    matcher = lambda c, sport, team, on_date=None: ev  # noqa: E731
    # capper=JAKE (any case) -> dedicated source 'Jake', kind 'own'
    pid, status, _ = manual.add_manual_pick(
        conn, "PICK | capper=JAKE | sport=NHL | market=moneyline | "
              "team=Toronto Maple Leafs | odds=-120", espn_match=matcher)
    assert status == "TRACKED"
    src = _pick_source_name(conn, pid)
    assert src["name"] == "Jake" and src["kind"] == "own"
    # explicit source=Jake with a different capper spelling also lands in Jake family
    pid2, _, _ = manual.add_manual_pick(
        conn, "PICK | capper=Jake D | source=jake | sport=NHL | market=moneyline | "
              "team=Boston Bruins | odds=+100", espn_match=matcher)
    assert _pick_source_name(conn, pid2)["name"] == "Jake"
    # a normal forwarded pick is NOT in the Jake family
    pid3, _, _ = manual.add_manual_pick(
        conn, "PICK | capper=HNICaps | sport=NHL | market=moneyline | "
              "team=Boston Bruins | odds=+100", espn_match=matcher)
    assert _pick_source_name(conn, pid3)["name"] == "Manual forward-in"
    # Jake appears in scorecards as TRACKED, family counts treat Jake as its own family
    rec = [r for r in records.capper_records(conn) if r["source"] == "Jake"]
    assert rec and rec[0]["capper"] == "JAKE"
    assert consensus.source_family("Jake") == "Jake"
    assert consensus.family_tracked_counts(conn)["Jake"] == 2
    # late Jake pick: NO exceptions for the owner
    past = _seed_event(conn, "j2", hours_from_now=-2)
    matcher_past = lambda c, sport, team, on_date=None: past  # noqa: E731
    _, status_late, _ = manual.add_manual_pick(
        conn, "PICK | capper=Jake | sport=NHL | market=moneyline | "
              "team=Toronto Maple Leafs | odds=-120", espn_match=matcher_past)
    assert status_late == "EXCLUDED_LATE"


def test_dashboard_log_my_pick_handler():
    conn = fresh_conn()
    ev = _seed_event(conn, "j9", hours_from_now=5)
    import espn
    assert espn.match_event(conn, "NHL", "Toronto Maple Leafs")["espn_id"] == "j9"
    pid, status, _eid = dashboard.handle_log_pick(conn, {
        "sport": "NHL", "market": "moneyline", "team": "Toronto Maple Leafs",
        "odds": "-115", "line": "", "selection": ""})
    assert status == "TRACKED"
    src = _pick_source_name(conn, pid)
    assert src["name"] == "Jake" and src["kind"] == "own"
    row = conn.execute("SELECT capper_id FROM picks WHERE id=?", (pid,)).fetchone()
    name = conn.execute("SELECT name FROM cappers WHERE id=?",
                        (row["capper_id"],)).fetchone()[0]
    assert name == "Jake"
