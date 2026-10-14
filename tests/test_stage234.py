import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_data2"

import consensus  # noqa: E402
import db as dbmod  # noqa: E402
import manual  # noqa: E402
import oddsmath  # noqa: E402
import records  # noqa: E402
import sources  # noqa: E402

DB = "/tmp/parlay_test_data2/test234.db"


def fresh_conn():
    if os.path.exists(DB):
        os.remove(DB)
    return dbmod.connect(DB)


ARTICLE_HTML = """
<html><head><script type="application/ld+json">{"@context":"https://schema.org",
"@type":"NewsArticle","headline":"Avalanche vs Stars Prediction, Picks for Tonight",
"author":[{"@type":"ProfilePage","mainEntity":{"@type":"Person","name":"Neil Parker"}}],
"datePublished":"2026-10-13T17:37:00-05:00","dateModified":"2026-10-13T18:00:00-05:00"}</script></head>
<body><h2 id="Prediction"><span>TOP PICK</span><br>
Avalanche vs Stars prediction: Avalanche moneyline</h2>
<p>widget</p></body></html>"""

PROP_HTML = ARTICLE_HTML.replace("Avalanche moneyline",
                                 "Nathan MacKinnon over 1.5 points")
TOTAL_HTML = ARTICLE_HTML.replace("Avalanche moneyline", "Over 6.5")


def test_covers_expert_parse():
    p = sources.parse_covers_expert_article(ARTICLE_HTML, "http://x")
    assert p["author"] == "Neil Parker"
    assert p["posted_at"] == "2026-10-13T22:37:00Z"  # -05:00 -> UTC
    assert p["market"] == "moneyline" and p["team_nick"] == "Avalanche"
    assert (p["away_nick"], p["home_nick"]) == ("Avalanche", "Stars")
    pp = sources.parse_covers_expert_article(PROP_HTML, "http://x")
    assert pp["market"] == "prop"  # player prop, NOT misread as a total
    pt = sources.parse_covers_expert_article(TOTAL_HTML, "http://x")
    assert pt["market"] == "total" and pt["line"] == 6.5
    assert sources.parse_covers_expert_article("<html>no picks</html>") is None


def test_cappertek_service_finding():
    f = sources.parse_cappertek_service(
        "Purchase Picks (1) Documented Record Loading picks... "
        "You must be logged in a customer account to access contact info.")
    assert f["purchase_picks_offered"] and f["documented_record_shown"]
    assert f["picks_rendered_by_js"] and f["public_pregame_picks"] is False


def test_drawdown_and_streaks():
    assert records.max_drawdown([1.0, -1.0, -1.0, 1.0, -1.0]) == 2.0
    assert records.max_drawdown([0.5, 0.5]) == 0.0
    cur, lw, ll = records._streak_info(["win", "win", "loss", "win", "win", "win"])
    assert cur == "W3" and lw == 3 and ll == 1


def _seed_family(conn, source_name, capper):
    sid = dbmod.upsert_source(conn, source_name, "test", None, "active")
    cid = dbmod.upsert_capper(conn, sid, capper)
    return sid, cid


def _insert_tracked(conn, sid, cid, event_id, team="Toronto Maple Leafs",
                    market="moneyline", selection=None, line=None,
                    captured="2026-10-13T20:00:00Z", commence="2026-10-14T23:00:00Z",
                    odds=-110):
    pick = {"source_id": sid, "capper_id": cid, "event_id": event_id, "sport": "NHL",
            "market": market, "selection": selection or f"{team} ML", "team": team,
            "line": line, "odds_claimed": odds, "captured_at": captured,
            "commence_time": commence, "raw_text": "seed"}
    pid, st = dbmod.insert_pick(conn, pick)
    assert st == "TRACKED"
    return pid


GAME_DAY_ET = "2026-10-14"  # commence 2026-10-14T23:00Z = 19:00 ET that day


def _seed_event(conn, espn_id="900", commence=None, home="Toronto Maple Leafs",
                away="Boston Bruins"):
    if commence is None:
        commence = "2026-10-14T23:00:00Z"
    conn.execute(
        "INSERT INTO events(espn_id,sport,home,away,commence_time,status,is_final,updated_at) "
        "VALUES(?,?,?,?,?,?,0,?)", (espn_id, "NHL", home, away, commence, "pre", commence))
    conn.commit()
    return conn.execute("SELECT id FROM events WHERE espn_id=?", (espn_id,)).fetchone()[0]


def test_records_windows_and_splits():
    conn = fresh_conn()
    sid, cid = _seed_family(conn, "Manual forward-in", "Window Wendy")
    ev = _seed_event(conn)
    for i in range(3):
        pid = _insert_tracked(conn, sid, cid, ev)
        conn.execute("INSERT INTO grades(pick_id,graded_at,result,profit_units,clv_pct) "
                     "VALUES(?,?,?,?,?)",
                     (pid, "2026-10-15T00:00:00Z", "win", 0.9091, 2.5))
    pid = _insert_tracked(conn, sid, cid, ev)
    conn.execute("INSERT INTO grades(pick_id,graded_at,result,profit_units,clv_pct) "
                 "VALUES(?,?,?,?,?)", (pid, "2026-10-15T00:00:00Z", "loss", -1.0, -1.0))
    conn.commit()
    rec = records.capper_records(conn)[0]
    assert (rec["w"], rec["l"], rec["n"]) == (3, 1, 4)
    assert rec["beat_close_pct"] == 75.0 and rec["avg_clv_pct"] == 1.62
    assert rec["last100"]["roi_pct"] == rec["roi_pct"]
    assert rec["by_sport_market"]["NHL/moneyline"]["w"] == 3
    assert rec["current_streak"] == "L1" and rec["longest_win_streak"] == 3
    srec = records.source_records(conn)[0]
    assert srec["source"] == "Manual forward-in" and srec["n"] == 4


def test_consensus_same_family_not_independent():
    conn = fresh_conn()
    ev = _seed_event(conn)
    # Two Covers cappers (computer + expert) = ONE family -> NOT qualifying
    sid1, cid1 = _seed_family(conn, "Covers", "Covers Computer Picks")
    sid2, cid2 = _seed_family(conn, "Covers", "Neil Parker")
    for i in range(100):
        _insert_tracked(conn, sid1, cid1, ev)
    _insert_tracked(conn, sid2, cid2, ev)
    assert consensus.qualifying_legs(conn, GAME_DAY_ET) == []
    # A second, independent family with >=100 tracked tips it over
    sid3, cid3 = _seed_family(conn, "Manual forward-in", "HNICaps")
    for i in range(99):
        _insert_tracked(conn, sid3, cid3, ev, team="Boston Bruins",
                        selection="Boston Bruins ML")
    _insert_tracked(conn, sid3, cid3, ev)
    legs = consensus.qualifying_legs(conn, GAME_DAY_ET)
    assert len(legs) == 1 and set(legs[0]["families"]) == {"Covers", "Manual forward-in"}
    assert legs[0]["full_weight"] is False  # both families < 500


def test_consensus_family_below_100_blocked():
    conn = fresh_conn()
    ev = _seed_event(conn)
    sid1, cid1 = _seed_family(conn, "Covers", "Covers Computer Picks")
    sid2, cid2 = _seed_family(conn, "Manual forward-in", "Small Sam")
    for i in range(100):
        _insert_tracked(conn, sid1, cid1, ev)
    _insert_tracked(conn, sid2, cid2, ev)  # family total only 1 tracked
    assert consensus.qualifying_legs(conn, GAME_DAY_ET) == []


def test_fair_product_and_paper_grading():
    legs = [{"price_proxy": -110}, {"price_proxy": 150}]
    dec, american, implied = consensus.fair_product(legs)
    assert dec == round((1 + 100 / 110) * 2.5, 4) and implied == round(100 / dec, 2)
    assert oddsmath.american_to_decimal(american) is not None

    conn = fresh_conn()
    ev = _seed_event(conn)
    sid, cid = _seed_family(conn, "Covers", "Covers Computer Picks")
    p1 = _insert_tracked(conn, sid, cid, ev)
    p2 = _insert_tracked(conn, sid, cid, ev)
    import json
    legs_json = json.dumps([
        {"pick_ids": [p1], "price_proxy": -110, "selection": "Leafs ML"},
        {"pick_ids": [p2], "price_proxy": 150, "selection": "Bruins ML"}])
    conn.execute("INSERT INTO suggestions(created_at,legs_json,status,stake_units) "
                 "VALUES('2026-10-14T00:00:00Z',?,'paper',0.25)", (legs_json,))
    conn.commit()
    assert consensus.grade_suggestions(conn) == []  # ungraded legs -> pending
    for pid, res in ((p1, "win"), (p2, "win")):
        conn.execute("INSERT INTO grades(pick_id,graded_at,result,profit_units) "
                     "VALUES(?,?,?,0)", (pid, "2026-10-15T00:00:00Z", res))
    conn.commit()
    graded = consensus.grade_suggestions(conn)
    assert graded[0]["result"] == "win"
    assert graded[0]["profit_units"] == round(0.25 * ((1 + 100 / 110) * 2.5 - 1), 4)


def test_quiet_hours():
    assert consensus.in_quiet_hours(datetime(2026, 10, 14, 23, 30,
                                               tzinfo=consensus.ET)) is True
    assert consensus.in_quiet_hours(datetime(2026, 10, 14, 6, 59,
                                             tzinfo=consensus.ET)) is True
    assert consensus.in_quiet_hours(datetime(2026, 10, 14, 7, 0,
                                             tzinfo=consensus.ET)) is False
    assert consensus.in_quiet_hours(datetime(2026, 10, 14, 12, 0,
                                             tzinfo=consensus.ET)) is False
    # silent-when-empty: no created, no graded -> no send attempted
    assert consensus.notify_new_suggestions([], []) is None


def test_watchlist_and_manual_arbitrary_capper():
    conn = fresh_conn()
    wid = dbmod.add_watchlist(conn, "Brand New Capper", source_label="X",
                              kind="social", handle="@brandnew")
    assert wid and dbmod.list_watchlist(conn)[0]["name"] == "Brand New Capper"
    # manual forward-in auto-creates ANY capper name (no pre-registration needed)
    pid, status, eid = manual.add_manual_pick(
        conn, "PICK | capper=Totally New Name | sport=NHL | market=moneyline | "
              "team=Toronto Maple Leafs | odds=-150")
    assert status == "UNMATCHED"  # no events seeded -> stored, never counted
    row = conn.execute("SELECT c.name FROM picks p JOIN cappers c ON c.id=p.capper_id "
                       "WHERE p.id=?", (pid,)).fetchone()
    assert row[0] == "Totally New Name"


def test_suggestions_schema_migrated():
    conn = fresh_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suggestions)")}
    for c in ("stake_units", "fair_decimal", "implied_pct", "haircut_pct", "is_sgp",
              "slip_text"):
        assert c in cols


# ---------------- Stage 5: slips, settings, brief, dashboard ----------------

def test_slip_format():
    legs = [
        {"selection": "Colorado Avalanche ML", "price_proxy": -102, "sport": "NHL",
         "matchup": "Colorado Avalanche @ Dallas Stars",
         "commence_time": "2026-10-14T23:30:00Z", "families": ["Covers"]},
        {"selection": "Toronto Maple Leafs ML", "price_proxy": 136, "sport": "NHL",
         "matchup": "Boston Bruins @ Toronto Maple Leafs",
         "commence_time": "2026-10-14T23:00:00Z", "families": ["Manual forward-in"]},
    ]
    stats = {"Covers": {"n": 150, "avg_clv_pct": 2.5},
             "Manual forward-in": {"n": 120, "avg_clv_pct": None}}
    slip = consensus.format_slip(legs, 342, 22.62, 0.25, stats,
                                 target_book="FanDuel", paper=True)
    assert slip.startswith("PARLAY SLIP — PAPER")
    assert "Book: FanDuel — check price on FanDuel" in slip  # exact proxy label
    assert "Leg 1: Colorado Avalanche ML (-102)" in slip
    assert "Leg 2: Toronto Maple Leafs ML (+136)" in slip
    assert "Parlay odds: +342" in slip and "check price on FanDuel" in slip
    assert "Stake: 0.25u paper" in slip
    assert "Why: consensus of Covers (n=150 tracked, approx-CLV 2.5%)" in slip
    assert "$" not in slip  # units only — never a dollar amount


def test_settings_defaults_and_roundtrip():
    import config
    import settings as sm
    old = config.DATA_DIR
    config.DATA_DIR = "/tmp/parlay_test_settings"
    try:
        s = sm.load()
        assert s["target_book"] == "FanDuel" and s["unit_value"] == ""
        assert sm.sports_enabled(s) == ["NHL", "NFL"]
        assert sm.unit_value(s) is None  # unset until Jake sets it
        sm.save({"target_book": "BetMGM"})
        assert sm.load()["target_book"] == "BetMGM"
        sm.save({"target_book": "FanDuel", "sports_nfl": "off"})
        assert sm.sports_enabled() == ["NHL"]
    finally:
        config.DATA_DIR = old
        p = "/tmp/parlay_test_settings/settings.yaml"
        if os.path.exists(p):
            os.remove(p)


def _seed_today_event(conn, espn_id="777", home="Toronto Maple Leafs",
                      away="Boston Bruins"):
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/Toronto")
    now_et = _dt.now(et)
    start_et = now_et.replace(hour=19, minute=30, second=0, microsecond=0)
    if start_et <= now_et:
        start_et = now_et + _td(hours=2)
    commence = start_et.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
    captured = (_dt.now(_tz.utc) - _td(minutes=5)).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO events(espn_id,sport,home,away,commence_time,status,is_final,updated_at) "
        "VALUES(?,?,?,?,?,?,0,?)",
        (espn_id, "NHL", home, away, commence, "pre", commence))
    conn.commit()
    eid = conn.execute("SELECT id FROM events WHERE espn_id=?", (espn_id,)).fetchone()[0]
    return eid, captured, commence, start_et.strftime("%Y-%m-%d")


def test_brief_silent_then_slip_and_copy_button():
    import brief as brief_mod
    import dashboard
    conn = fresh_conn()
    assert brief_mod.generate_brief(conn) == ""  # silent: no consensus
    ev, captured, commence, day_et = _seed_today_event(conn)
    sid1, cid1 = _seed_family(conn, "Covers", "Covers Computer Picks")
    sid2, cid2 = _seed_family(conn, "Manual forward-in", "HNICaps")
    for i in range(100):
        _insert_tracked(conn, sid1, cid1, ev, captured=captured, commence=commence)
        _insert_tracked(conn, sid2, cid2, ev, captured=captured, commence=commence)
    ev2, cap2, com2, _ = _seed_today_event(conn, "778", home="Montreal Canadiens",
                                          away="New York Rangers")
    for i in range(2):  # second game both families agree on -> second leg
        _insert_tracked(conn, sid1, cid1, ev2, team="Montreal Canadiens",
                        captured=cap2, commence=com2)
        _insert_tracked(conn, sid2, cid2, ev2, team="Montreal Canadiens",
                        captured=cap2, commence=com2)
    created = consensus.build_suggestions(conn, day_et)
    assert len(created) == 1 and created[0]["slip_text"].startswith("PARLAY SLIP — PAPER")
    assert consensus.build_suggestions(conn, day_et) == []  # max one per day
    text = brief_mod.generate_brief(conn)
    assert "PARLAY BRIEF" in text and "PARLAY SLIP — PAPER" in text
    assert "check price on FanDuel" in text and "$" not in text
    page = dashboard.render(conn)
    assert "copy-slip-btn" in page and "function copySlip" in page  # 2-tap copy
    assert "Today's slips" in page and "settings.yaml" in page
    # graded results no longer push ntfy (only NEW slips do)
    assert consensus.notify_new_suggestions(
        [], [{"id": 1, "result": "win", "profit_units": 0.5}]) is None


def test_ntfy_quiet_exception_leg_within_hour():
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    now = _dt.now(_tz.utc)
    soon = (now + _td(minutes=30)).isoformat().replace("+00:00", "Z")
    later = (now + _td(hours=3)).isoformat().replace("+00:00", "Z")
    assert consensus._leg_starts_within_hour([{"legs": [{"commence_time": soon}]}], now)
    assert not consensus._leg_starts_within_hour([{"legs": [{"commence_time": later}]}], now)
