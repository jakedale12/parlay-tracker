"""Parlay Tracker v0 — Stage 1 app: poll cycle + CLI.

Cycle (~15 min): ESPN NHL/NFL scoreboards (today + last 2 days so finals land)
-> closing-line snapshots -> Covers picks -> grade finals -> heartbeat.
CapperTek leaderboard seeding runs at most once/day (claimed records only).
The system NEVER places a bet and NEVER logs into a sportsbook.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import config
import dashboard
import db as dbmod
import espn
import grading
import manual
import sources


def run_cycle(conn, include_cappertek=False):
    import settings as settings_mod
    sports_on = settings_mod.sports_enabled()
    out = {"sports_enabled": sports_on}
    today = datetime.now(timezone.utc)
    for sport in sports_on:
        for back in (0, 1, 2):
            d = (today - timedelta(days=back)).strftime("%Y%m%d")
            try:
                r = espn.ingest_scoreboard(conn, sport, dates=d)
                out[f"espn_{sport}_{d}"] = r
            except Exception as e:  # noqa: BLE001
                out[f"espn_{sport}_{d}"] = {"error": str(e)}
    out["closing_lines_new"] = espn.update_closing_lines(conn)
    if "NHL" in sports_on:
        try:
            out["covers"] = sources.ingest_covers(conn, espn.match_event)
        except Exception as e:  # noqa: BLE001
            out["covers"] = {"ok": False, "error": str(e)}
    for sport in sports_on:
        try:
            r = sources.ingest_covers_experts(conn, espn.match_event, sport)
            out[f"covers_experts_{sport}"] = {k: v for k, v in r.items() if k != "example"}
            out[f"covers_experts_{sport}_example"] = r.get("example")
        except Exception as e:  # noqa: BLE001
            out[f"covers_experts_{sport}"] = {"ok": False, "error": str(e)}
    # Fix 1 families (each verified live separately before wiring):
    if "NHL" in sports_on:
        try:
            r = sources.ingest_ultimatecapper_nhl(conn, espn.match_event)
            out["ultimatecapper_nhl"] = {k: v for k, v in r.items() if k != "example"}
            out["ultimatecapper_nhl_example"] = r.get("example")
        except Exception as e:  # noqa: BLE001
            out["ultimatecapper_nhl"] = {"ok": False, "error": str(e)}
    if "NFL" in sports_on:
        try:
            out["ultimatecapper_nfl"] = sources.ingest_ultimatecapper_nfl(
                conn, espn.match_event)
        except Exception as e:  # noqa: BLE001
            out["ultimatecapper_nfl"] = {"ok": False, "error": str(e)}
    try:
        r = sources.ingest_sportshub(conn, espn.match_event)
        out["sportshub"] = {k: v for k, v in r.items() if k != "example"}
        out["sportshub_example"] = r.get("example")
    except Exception as e:  # noqa: BLE001
        out["sportshub"] = {"ok": False, "error": str(e)}
    try:
        import reddit as _reddit
        out["reddit_potd"] = _reddit.ingest_reddit_potd(conn, dbmod, espn.match_event)
    except Exception as e:  # noqa: BLE001
        out["reddit_potd"] = {"ok": False, "error": str(e)}
    try:
        out["supercontest"] = sources.ingest_supercontest(conn, espn.match_event)
    except Exception as e:  # noqa: BLE001
        out["supercontest"] = {"ok": False, "error": str(e)}
    try:
        r = sources.ingest_covers_consensus(conn, espn.match_event)
        out["covers_consensus"] = r.get("families", r)
    except Exception as e:  # noqa: BLE001
        out["covers_consensus"] = {"ok": False, "error": str(e)}
    if "NHL" in sports_on:
        try:
            r = sources.ingest_action_nhl(conn, espn.match_event)
            out["action_nhl"] = {k: v for k, v in r.items() if k != "example"}
            out["action_nhl_example"] = r.get("example")
        except Exception as e:  # noqa: BLE001
            out["action_nhl"] = {"ok": False, "error": str(e)}
    if "NFL" in sports_on:
        try:
            r = sources.ingest_action_nfl(conn, espn.match_event)
            out["action_nfl"] = {k: v for k, v in r.items() if k != "example"}
            out["action_nfl_example"] = r.get("example")
        except Exception as e:  # noqa: BLE001
            out["action_nfl"] = {"ok": False, "error": str(e)}
    if include_cappertek or not conn.execute(
            "SELECT 1 FROM ingest_log WHERE source='CapperTek' AND ok=1 "
            "AND substr(ran_at,1,10)=substr(?,1,10)", (dbmod.now_iso(),)).fetchone():
        try:
            out["cappertek"] = sources.ingest_cappertek(conn)
        except Exception as e:  # noqa: BLE001
            out["cappertek"] = {"ok": False, "error": str(e)}
        try:  # daily service-page detail check (expected finding: purchase-gated)
            out["cappertek_detail"] = sources.ingest_cappertek_detail(conn)
        except Exception as e:  # noqa: BLE001
            out["cappertek_detail"] = {"ok": False, "error": str(e)}
    out["graded_new"] = grading.grade_all(conn, dbmod)
    # Stage 4: consensus -> paper suggestions -> paper grading -> quiet ntfy
    import consensus
    created = []
    try:
        created = consensus.build_suggestions(conn)
        out["suggestions_new"] = len(created)
    except Exception as e:  # noqa: BLE001
        out["suggestions_new"] = {"error": str(e)}
    try:
        graded_sugg = consensus.grade_suggestions(conn)
        out["suggestions_graded"] = graded_sugg
    except Exception as e:  # noqa: BLE001
        graded_sugg = []
        out["suggestions_graded"] = {"error": str(e)}
    try:
        out["ntfy"] = consensus.notify_new_suggestions(created, graded_sugg)
    except Exception as e:  # noqa: BLE001
        out["ntfy"] = {"error": str(e)}
    hb = os.path.join(config.DATA_DIR, "heartbeat")
    with open(hb, "w") as f:
        f.write(str(time.time()))
    return out


def demo_pipeline_test(conn):
    """Grading-pipeline proof on a COMPLETED game: a clearly-labelled synthetic
    pick (PIPELINE_TEST, never counted in any record) on a real final ESPN event,
    graded end-to-end. Uses the most recent final NHL event with a moneyline."""
    row = conn.execute(
        "SELECT * FROM events WHERE is_final=1 AND home_score IS NOT NULL "
        "ORDER BY commence_time DESC LIMIT 1").fetchone()
    if row is None:
        return {"ok": False, "error": "no final event in DB yet — run a cycle first"}
    sid = dbmod.upsert_source(conn, "Pipeline Test", "test", None, "test",
                              "Synthetic grading-pipeline checks only. Never counted.")
    cid = dbmod.upsert_capper(conn, sid, "PIPELINE-TEST (not a capper)")
    winner = row["home"] if row["home_score"] > row["away_score"] else row["away"]
    text = (f"PIPELINE TEST — synthetic pick for grading verification only, not a capper pick. "
            f"{winner} ML vs {row['away']} @ {row['home']}, final {row['away_score']}-{row['home_score']}")
    pick = {"source_id": sid, "capper_id": cid, "event_id": row["id"], "sport": row["sport"],
            "market": "moneyline", "selection": f"{winner} ML", "team": winner,
            "odds_claimed": -110, "captured_at": dbmod.now_iso(),
            "commence_time": row["commence_time"], "status": "PIPELINE_TEST",
            "exclusion_reason": "pipeline test on a completed game — excluded from all records",
            "raw_text": text}
    pid, status = dbmod.insert_pick(conn, pick)
    graded = grading.grade_all(conn, dbmod)
    g = conn.execute("SELECT * FROM grades WHERE pick_id=?", (pid,)).fetchone()
    return {"ok": True, "pick_id": pid, "status": status,
            "event": f"{row['away']} @ {row['home']}", "graded_new": graded,
            "grade": dict(g) if g else None}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    conn = dbmod.connect()
    sources.register_static_sources(conn)
    if cmd == "cycle":
        print(json.dumps(run_cycle(conn), indent=2, default=str))
    elif cmd == "demo-pipeline-test":
        run_cycle(conn)
        print(json.dumps(demo_pipeline_test(conn), indent=2, default=str))
    elif cmd == "brief":
        # Daily brief for the Muse cron: prints the brief, or NOTHING when
        # there is no qualifying consensus today (silent by design).
        import brief as brief_mod
        text = brief_mod.generate_brief(conn)
        if text:
            print(text)
    elif cmd == "settings":
        import settings as settings_mod
        print(json.dumps(settings_mod.load(), indent=2))
    elif cmd == "settings-set":
        import settings as settings_mod
        print(json.dumps(settings_mod.save({sys.argv[2]: sys.argv[3]}), indent=2))
    elif cmd == "watch-add":
        # app.py watch-add "Capper Name" [source_label] [kind] [handle]
        name = sys.argv[2]
        wid = dbmod.add_watchlist(
            conn, name,
            source_label=sys.argv[3] if len(sys.argv) > 3 else "Manual forward-in",
            kind=sys.argv[4] if len(sys.argv) > 4 else "manual",
            handle=sys.argv[5] if len(sys.argv) > 5 else None)
        print(f"watchlist_id={wid} name={name} — forward picks with: "
              f"PICK | capper={name} | sport=NHL | market=moneyline | team=... | odds=-110")
    elif cmd == "watch-list":
        for r in dbmod.list_watchlist(conn):
            print(dict(r))
    elif cmd == "suggest":
        import consensus
        print(json.dumps({"qualifying_legs": consensus.qualifying_legs(conn),
                          "created": consensus.build_suggestions(conn),
                          "graded": consensus.grade_suggestions(conn)},
                         indent=2, default=str))
    elif cmd == "records":
        import records as _records
        print(json.dumps({"cappers": _records.capper_records(conn),
                          "sources": _records.source_records(conn)},
                         indent=2, default=str))
    elif cmd == "backfill-history":
        # History/baseline layer (context only): ESPN finals into hist_* tables
        # + baselines recompute. Never touches the picks ledger.
        import history as history_mod
        print(json.dumps(history_mod.backfill_all(conn), indent=2, default=str))
    elif cmd == "baselines":
        import history as history_mod
        print(json.dumps(history_mod.compute_baselines(conn), indent=2, default=str))
    elif cmd == "history-check":
        import history as history_mod
        print(json.dumps(history_mod.sanity_check(conn), indent=2, default=str))
    elif cmd == "reddit-potd":
        import reddit as _reddit
        print(json.dumps(_reddit.ingest_reddit_potd(
            conn, dbmod, espn.match_event, force=True), indent=2, default=str))
    elif cmd == "supercontest":
        import sources as _sources
        print(json.dumps(_sources.ingest_supercontest(
            conn, espn.match_event, force=True), indent=2, default=str))
    elif cmd == "demo-prop-test":
        # Fix 3 proof: copy each EXCLUDED_LATE NHL prop pick onto a clearly
        # labelled PIPELINE_TEST row on the same (final) event and grade —
        # pipeline rows are excluded from all records.
        import grading as _grading
        created = []
        for r in conn.execute(
                "SELECT * FROM picks WHERE market='prop' AND sport='NHL' "
                "AND status='EXCLUDED_LATE'").fetchall():
            if conn.execute("SELECT 1 FROM picks WHERE status='PIPELINE_TEST' "
                            "AND event_id=? AND selection=?",
                            (r["event_id"], r["selection"])).fetchone():
                continue
            pick = {"source_id": r["source_id"], "capper_id": r["capper_id"],
                    "event_id": r["event_id"], "sport": "NHL", "market": "prop",
                    "selection": r["selection"], "team": r["team"],
                    "odds_claimed": r["odds_claimed"],
                    "captured_at": dbmod.now_iso(),
                    "commence_time": r["commence_time"], "status": "PIPELINE_TEST",
                    "exclusion_reason": "pipeline test of NHL prop grading — "
                                        "excluded from all records",
                    "raw_text": f"PIPELINE_TEST copy of pick {r['id']}"}
            pid, _st = dbmod.insert_pick(conn, pick)
            created.append(pid)
        n = _grading.grade_all(conn, dbmod)
        rows = conn.execute(
            "SELECT g.pick_id, p.selection, g.result, g.profit_units, g.notes "
            "FROM grades g JOIN picks p ON p.id=g.pick_id "
            "WHERE p.status='PIPELINE_TEST' AND p.market='prop'").fetchall()
        print(json.dumps({"created": created, "graded_new": n,
                          "prop_grades": [dict(r) for r in rows]},
                         indent=2, default=str))
    elif cmd == "regrade-clv":
        # Fix 2(d): recompute CLV under the devigged probability formula and
        # print the BEFORE/AFTER table (the preserved history note).
        import grading as _grading
        print(json.dumps(_grading.regrade_clv(conn), indent=2, default=str))
    elif cmd == "add-pick":
        pid, status, eid = manual.add_manual_pick(conn, " ".join(sys.argv[2:]),
                                                  espn_match=espn.match_event)
        print(f"pick_id={pid} status={status} event_id={eid}")
    elif cmd == "serve":
        stop = threading.Event()

        def loop():
            lconn = dbmod.connect()  # sqlite connections are thread-bound
            sources.register_static_sources(lconn)
            while not stop.is_set():
                try:
                    r = run_cycle(lconn)
                    print(f"[cycle] {json.dumps(r, default=str)[:500]}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[cycle] ERROR {e}", flush=True)
                stop.wait(config.POLL_SECONDS)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        print(f"dashboard on :{config.DASHBOARD_PORT}", flush=True)
        dashboard.serve(dbmod.connect, config.DASHBOARD_PORT)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
