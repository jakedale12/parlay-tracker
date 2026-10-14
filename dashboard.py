"""Dashboard (stdlib http.server) — phone-first, one screen, no login (tailnet).

Order (Jake's Stage 5 spec):
  1. TODAY'S SLIPS at top — bet-slip text blocks with a Copy button
     (tap slip -> tap Copy = 2 taps to the full slip text in the clipboard)
  2. Capper leaderboard (sample warnings visible) + source scorecards
  3. Open paper slips with live leg status
  4. Consensus board, recent picks, watchlist, sources, settings
  5. CLAIMED section (unverified CapperTek claims — separate, never counts)
  6. League baselines (historical context only — not picks, not records)
One write path exists: the 'Log my pick' form (POST /log-pick) files Jake's
own pick through the same manual parser + timestamp rule as every source.
The system NEVER places a bet."""
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import consensus
import db as dbmod
import records
import settings as settings_mod

ET = ZoneInfo("America/Toronto")

COPY_JS = """
function copySlip(id, btn){
  var el = document.getElementById(id);
  var text = el.innerText || el.textContent;
  function done(){ btn.textContent = 'Copied ✓'; setTimeout(function(){btn.textContent='Copy slip';}, 2000); }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(function(){ fallback(); });
  } else { fallback(); }
  function fallback(){
    var ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta); done();
  }
}
"""


def _et(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%b %d %I:%M %p ET")
    except ValueError:
        return iso


def _et_day(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _v(x, suffix=""):
    return f"{x}{suffix}" if x is not None else "—"


def _slip_block(s, idx, fams_stats):
    """One suggestion as a copyable slip text block + Copy button."""
    legs_list = []
    try:
        legs_list = json.loads(s["legs_json"] or "[]")
    except Exception:  # noqa: BLE001
        pass
    text = s["slip_text"]
    if not text and legs_list:
        text = consensus.format_slip(
            legs_list, s["fair_american"], s["implied_pct"], s["stake_units"] or 0.25,
            fams_stats, target_book=settings_mod.load()["target_book"], paper=True)
    sid = f"slip-{s['id']}"
    return (f"<div class=slipwrap><pre class=slip id=\"{sid}\">{html.escape(text or '')}</pre>"
            f"<button type=\"button\" class=\"copy-slip-btn\" data-slip=\"{sid}\" "
            f"onclick=\"copySlip('{sid}', this)\">Copy slip</button></div>")


TRACKED_LABEL = "TRACKED — our timestamped pre-game record"
CLAIMED_LABEL = "CLAIMED — unverified, does not count"


def _log_pick_form():
    return """<form method="post" action="/log-pick" class=logpick>
<label>Sport <select name="sport"><option>NHL</option><option>NFL</option></select></label>
<label>Market <select name="market"><option>moneyline</option><option>spread</option>
<option>total</option></select></label>
<label>Team <input name="team" placeholder="Toronto Maple Leafs" size="22"></label>
<label>Line <input name="line" placeholder="-1.5 / 47.5 (spread/total)" size="10"></label>
<label>Odds <input name="odds" placeholder="-110" size="7"></label>
<label>Selection <input name="selection" placeholder="Over 47.5 (totals only)" size="16"></label>
<button type="submit">File my pick</button>
<p class=hint>Files under source <b>Jake</b> through the same parser + timestamp rule as
everyone: filed after game start = EXCLUDED_LATE. Totals: put Over/Under in Selection.</p>
</form>"""


def handle_log_pick(conn, form):
    """File Jake's own pick from the dashboard form via the SAME manual
    parser path (capper=Jake -> dedicated source family 'Jake', kind=own)
    and the SAME captured_at < commence_time rule.
    Returns the parser's (pick_id, status, event_id) tuple."""
    import espn
    import manual
    sport = (form.get("sport") or "NHL").strip().upper()
    market = (form.get("market") or "moneyline").strip().lower()
    team = (form.get("team") or "").strip()
    selection = (form.get("selection") or "").strip()
    if market != "total" and not team:
        raise ValueError("team is required for moneyline/spread picks")
    parts = [f"capper=Jake", f"sport={sport}", f"market={market}"]
    if team:
        parts.append(f"team={team}")
    if form.get("line"):
        parts.append(f"line={form['line'].strip()}")
    if form.get("odds"):
        parts.append(f"odds={form['odds'].strip()}")
    if selection:
        parts.append(f"selection={selection}")
    text = "PICK | " + " | ".join(parts)
    return manual.add_manual_pick(conn, text, espn_match=espn.match_event)


def _leg_live_status(conn, legs_list):
    parts = []
    for leg in legs_list:
        status = "scheduled"
        pids = leg.get("pick_ids") or []
        if pids:
            ev = conn.execute(
                "SELECT e.status, e.home_score, e.away_score, e.is_final FROM picks p "
                "JOIN events e ON e.id=p.event_id WHERE p.id=?", (pids[0],)).fetchone()
            g = conn.execute("SELECT result FROM grades WHERE pick_id=?",
                             (pids[0],)).fetchone()
            if ev is not None:
                if ev["is_final"]:
                    status = f"final {ev['away_score']}-{ev['home_score']}"
                elif ev["status"]:
                    status = ev["status"]
            if g is not None:
                status += f" · leg {g['result']}"
        parts.append(f"{html.escape(leg.get('selection', ''))}: {html.escape(status)}")
    return "<br>".join(parts)


def render(conn):
    cfg = settings_mod.load()
    fams_stats = consensus.family_stats(conn)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    all_sugg = conn.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT 30").fetchall()
    todays = [s for s in all_sugg if _et_day(s["created_at"]) == today]
    if todays:
        slips_top = "".join(_slip_block(s, i, fams_stats) for i, s in enumerate(todays))
    else:
        slips_top = ("<p>No slip today — a slip appears here only when 2+ independent "
                     "sources (100+ tracked picks each) agree on the same legs. "
                     "Nothing qualifying = nothing suggested, on purpose.</p>")

    recs = records.capper_records(conn)
    src_recs = records.source_records(conn)
    lb_rows = []
    for r in recs:
        splits = "<br>".join(
            f"{html.escape(k)}: {v['w']}-{v['l']}-{v['push']} · ROI {_v(v['roi_pct'], '%')}"
            for k, v in r["by_sport_market"].items()) or "—"
        lb_rows.append(
            f"<tr><td>{html.escape(r['capper'])}</td><td>{html.escape(r['source'])}</td>"
            f"<td>{r['w']}-{r['l']}-{r['push']}</td><td>{r['n']}</td>"
            f"<td>{_v(r['roi_pct'], '%')}</td><td>{_v(r['win_rate'], '%')}</td>"
            f"<td>{_v(r['units'])}</td><td>{r['current_streak']} (best W{r['longest_win_streak']}/"
            f"L{r['longest_loss_streak']})</td><td>{_v(r['max_drawdown_units'])}</td>"
            f"<td>{_v(r['avg_clv_pct'], '%')}</td><td>{_v(r['beat_close_pct'], '%')}"
            f" (n={r['clv_n']})</td>"
            f"<td>{r['last100']['w']}-{r['last100']['l']}-{r['last100']['push']} · "
            f"ROI {_v(r['last100']['roi_pct'], '%')}</td>"
            f"<td>{splits}</td><td>{html.escape(r['sample_label'])}</td></tr>")
    lb = "".join(lb_rows) or \
        "<tr><td colspan=14>No tracked picks yet. Sample-size rule: nobody is followable under 500 tracked picks.</td></tr>"

    sb = "".join(
        f"<tr><td>{html.escape(r['source'])}</td><td>{r['w']}-{r['l']}-{r['push']}</td>"
        f"<td>{r['n']}</td><td>{_v(r['roi_pct'], '%')}</td><td>{_v(r['win_rate'], '%')}</td>"
        f"<td>{_v(r['max_drawdown_units'])}</td><td>{_v(r['avg_clv_pct'], '%')}</td>"
        f"<td>{_v(r['beat_close_pct'], '%')}</td><td>{html.escape(r['sample_label'])}</td></tr>"
        for r in src_recs) or "<tr><td colspan=9>No tracked picks yet.</td></tr>"

    open_rows = []
    for s in all_sugg:
        try:
            legs_list = json.loads(s["legs_json"] or "[]")
        except Exception:  # noqa: BLE001
            legs_list = []
        live = _leg_live_status(conn, legs_list)
        result = s["result"] or "open"
        if s["profit_units"] is not None:
            result += f" ({s['profit_units']:+}u paper)"
        open_rows.append(
            f"<tr><td>{_et(s['created_at'])}</td><td>{live}</td>"
            f"<td>{_v(s['stake_units'], 'u')}</td><td>{_v(s['fair_american'])}</td>"
            f"<td>{'SGP' if s['is_sgp'] else 'cross-game'}</td>"
            f"<td>{html.escape(s['status'])}</td><td>{html.escape(result)}</td></tr>")
    open_tbl = "".join(open_rows) or \
        "<tr><td colspan=7>No paper slips yet.</td></tr>"

    picks = conn.execute(
        "SELECT p.*, c.name AS capper, s.name AS source, g.result FROM picks p "
        "JOIN cappers c ON c.id=p.capper_id JOIN sources s ON s.id=p.source_id "
        "LEFT JOIN grades g ON g.pick_id=p.id ORDER BY p.id DESC LIMIT 50").fetchall()
    pk = "".join(
        f"<tr><td>{_et(p['captured_at'])}</td><td>{html.escape(p['capper'])}</td>"
        f"<td>{html.escape(p['source'])}</td><td>{html.escape(p['sport'] or '')}</td>"
        f"<td>{html.escape(p['market'])}</td><td>{html.escape(p['selection'])}</td>"
        f"<td>{p['odds_claimed'] if p['odds_claimed'] is not None else (p['odds_available'] or '')}</td>"
        f"<td>{html.escape(p['status'])}</td>"
        f"<td>{p['result'] or ''}</td></tr>" for p in picks) or \
        "<tr><td colspan=9>No picks captured yet.</td></tr>"

    try:
        legs = consensus.qualifying_legs(conn)
    except Exception as e:  # noqa: BLE001
        legs = []
        cb = f"<p>Consensus check error: {html.escape(str(e))}</p>"
    else:
        if legs:
            cb = "<table><tr><th>Matchup</th><th>Leg</th><th>ESPN/DK proxy price</th>" \
                 "<th>Independent families (tracked n)</th></tr>" + "".join(
                f"<tr><td>{html.escape(l['matchup'])} · {_et(l['commence_time'])}</td>"
                f"<td>{html.escape(l['selection'])}</td>"
                f"<td>{l['price_proxy']} ({html.escape(l['price_label'])})</td>"
                f"<td>{html.escape(', '.join(f'{f} ({n})' for f, n in l['family_counts'].items()))}</td></tr>"
                for l in legs) + "</table>"
        else:
            counts = consensus.family_tracked_counts(conn)
            cnt = ", ".join(f"{html.escape(f)}: {n} tracked" for f, n in
                            sorted(counts.items())) or "none yet"
            cb = (f"<p><b>No qualifying consensus today.</b> A leg needs 2+ independent "
                  f"source families on the same game/market/side, each with 100+ tracked "
                  f"picks. Current family counts — {cnt}.</p>")

    wl = conn.execute("SELECT * FROM watchlist ORDER BY name").fetchall()
    wlr = "".join(
        f"<tr><td>{html.escape(w['name'])}</td><td>{html.escape(w['source_label'])}</td>"
        f"<td>{html.escape(w['kind'])}</td><td>{html.escape(w['handle'] or '')}</td>"
        f"<td>{html.escape(w['status'])}</td></tr>" for w in wl) or \
        "<tr><td colspan=5>Empty — Jake's capper list not provided yet. Add one any time: <code>app.py watch-add \"Name\"</code> (see README).</td></tr>"

    srcs = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
    sr = "".join(f"<tr><td>{html.escape(s['name'])}</td><td>{html.escape(s['status'])}</td>"
                 f"<td>{html.escape(s['notes'] or '')}</td></tr>" for s in srcs)

    # CLAIMED bucket: CapperTek claimed_record strings. Rendered ONLY here,
    # in their own section -- they never feed scorecards/ROI/consensus.
    claimed = conn.execute(
        "SELECT c.name AS capper, c.claimed_record, s.name AS source FROM cappers c "
        "JOIN sources s ON s.id=c.source_id WHERE c.claimed_record IS NOT NULL "
        "AND c.claimed_record != '' ORDER BY s.name, c.name").fetchall()
    claimed_tbl = "".join(
        f"<tr><td>{html.escape(c['capper'])}</td><td>{html.escape(c['source'])}</td>"
        f"<td>{html.escape(c['claimed_record'])}</td>"
        f"<td><b>{CLAIMED_LABEL}</b></td></tr>" for c in claimed) or \
        "<tr><td colspan=4>No claimed records seeded.</td></tr>"

    # League baselines: historical context from hist_* tables (history.py).
    # NOT picks, NOT tracked records -- context only.
    import history as history_mod
    base_rows = []
    for b in history_mod.get_baselines(conn):
        m = b["metrics"]
        cm = m.get("close_movement") or {}
        over = " · ".join(f"Over {k}: {v}%" for k, v in (m.get("over_pct") or {}).items())
        fav = (f"{m['favourite_win_pct']}%" if m.get("favourite_win_pct") is not None
               else (m.get("favourite_note") or "not available"))
        base_rows.append(
            f"<tr><td>{html.escape(b['league'])}</td><td>{html.escape(b['scope'])}</td>"
            f"<td>{m.get('games_n')}</td><td>{_v(m.get('home_win_pct'), '%')}</td>"
            f"<td>{_v(m.get('avg_total'))}</td><td>{_v(m.get('median_total'))}</td>"
            f"<td>{over}</td><td>{html.escape(str(fav))}</td>"
            f"<td>close-move (approx, our own archive, n={cm.get('n_events_with_close')}): "
            f"avg |Δtotal| {_v(cm.get('avg_abs_total_move'))}, "
            f"avg |Δhome ML| {_v(cm.get('avg_abs_home_ml_move'))}</td></tr>")
    base_tbl = "".join(base_rows) or \
        "<tr><td colspan=9>No baselines computed yet — run <code>app.py backfill-history</code>.</td></tr>"

    unit_disp = cfg.get("unit_value") or "unset (stakes in units only)"
    settings_tbl = (
        f"<table><tr><th>Setting</th><th>Value</th></tr>"
        f"<tr><td>target_book</td><td>{html.escape(str(cfg['target_book']))}</td></tr>"
        f"<tr><td>sports</td><td>NHL {html.escape(str(cfg['sports_nhl']))} · "
        f"NFL {html.escape(str(cfg['sports_nfl']))}</td></tr>"
        f"<tr><td>unit_value</td><td>{html.escape(str(unit_disp))}</td></tr>"
        f"<tr><td>quiet_hours (ET)</td><td>{cfg['quiet_hours_start']}:00–"
        f"{cfg['quiet_hours_end']}:00</td></tr></table>"
        f"<p>One place: <code>settings.yaml</code> in the data volume "
        f"(<code>app.py settings</code> / <code>app.py settings-set key value</code>).</p>")

    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Parlay Researcher &amp; Tracker</title>
<style>body{{font-family:system-ui;margin:12px;max-width:980px}}
table{{border-collapse:collapse;margin:8px 0 20px;display:block;overflow-x:auto}}
td,th{{border:1px solid #ccc;padding:4px 8px;font-size:13px;vertical-align:top}}
h2{{margin-top:26px}}.warn{{background:#fff3cd;padding:10px;border:1px solid #e0c36a}}
.slip{{background:#f6f6f6;border:1px solid #999;padding:10px;white-space:pre-wrap;
font-size:14px;margin:8px 0 4px}}.copy-slip-btn{{font-size:16px;padding:10px 18px;
margin:4px 0 14px;cursor:pointer}}.logpick label{{display:inline-block;margin:4px 8px 4px 0}}
.logpick .hint{{font-size:12px;color:#555}}</style>
<script>{COPY_JS}</script>
<h1>Parlay Researcher &amp; Tracker</h1>
<p class=warn><b>Sample-size warning:</b> NOBODY is “followable” under <b>500 tracked picks</b>.
100–499 = warming up only. ROI is flat-1u. Prices are ESPN/DK proxy —
<b>check price on {html.escape(str(cfg['target_book']))}</b>. <b>Paper period:
Oct 13 – Nov 12, 2026</b> — slips are paper-only suggestions; Jake places any bet
himself, on his phone. This system never places a bet, never opens a sportsbook,
and never logs into one.</p>
<h2>Today's slips</h2>{slips_top}
<h2>Log my pick (Jake)</h2>{_log_pick_form()}
<h2>Capper leaderboard — {TRACKED_LABEL}</h2><table><tr><th>Capper</th><th>Source</th><th>W-L-P</th><th>n</th>
<th>ROI % (flat 1u)</th><th>Win %</th><th>Units</th><th>Streak</th><th>Max DD (u)</th>
<th>Avg approx-CLV (pp)</th><th>Beat-close %</th><th>Last 100</th><th>Sport×market split</th>
<th>Sample</th></tr>{lb}</table>
<h2>Source scorecards — {TRACKED_LABEL}</h2><table><tr><th>Source</th><th>W-L-P</th><th>n</th><th>ROI %</th>
<th>Win %</th><th>Max DD (u)</th><th>Avg approx-CLV (pp)</th><th>Beat-close %</th>
<th>Sample</th></tr>{sb}</table>
<h2>Open paper slips + live status</h2><table><tr><th>Created</th><th>Legs (live)</th>
<th>Stake</th><th>Fair odds</th><th>Type</th><th>Status</th><th>Result</th></tr>{open_tbl}</table>
<h2>Consensus board (today)</h2>{cb}
<h2>Recent tracked picks</h2><table><tr><th>Captured</th><th>Capper</th><th>Source</th><th>Sport</th>
<th>Market</th><th>Selection</th><th>Odds</th><th>Status</th><th>Grade</th></tr>{pk}</table>
<h2>Watchlist (add-a-capper)</h2><table><tr><th>Capper</th><th>Source label</th><th>Kind</th>
<th>Handle</th><th>Status</th></tr>{wlr}</table>
<h2>Sources</h2><table><tr><th>Source</th><th>Status</th><th>Notes</th></tr>{sr}</table>
<h2>Claimed records — {CLAIMED_LABEL}</h2>
<p>These are records the cappers/platforms CLAIM about themselves (CapperTek leaderboard).
They are unverified, were never timestamped by us pre-game, and <b>never count</b> toward
scorecards, ROI, consensus or the followable bar. Discovery only.</p>
<table><tr><th>Capper</th><th>Source</th><th>Claimed record (as published)</th><th>Bucket</th></tr>{claimed_tbl}</table>
<h2>League baselines — historical context (not picks, not tracked records)</h2>
<p>Computed from ESPN finals stored in the separate history tables. Context for reading
lines — a baseline is not a pick and no capper gets credit or blame for it.</p>
<table><tr><th>League</th><th>Scope</th><th>Games</th><th>Home win %</th><th>Avg total</th>
<th>Median total</th><th>Over %</th><th>Favourite win %</th><th>Close movement</th></tr>{base_tbl}</table>
<h2>Settings</h2>{settings_tbl}
<p>Updated {_et(dbmod.now_iso())} · DB: SQLite (WAL) · Lines: ESPN scoreboard (DraftKings-sourced)</p>"""


def serve(conn_factory, port):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/healthz"):
                self.send_response(404); self.end_headers(); return
            if self.path == "/healthz":
                body = b"ok"
                self.send_response(200); self.send_header("Content-Type", "text/plain")
            else:
                conn = conn_factory()
                body = render(conn).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/log-pick":
                self.send_response(404); self.end_headers(); return
            from urllib.parse import parse_qs
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(
                self.rfile.read(length).decode("utf-8", "replace")).items()}
            try:
                conn = conn_factory()
                pid, status, _eid = handle_log_pick(conn, form)
                msg = (f"Pick filed under source <b>Jake</b>: pick #{pid}, "
                       f"status <b>{html.escape(status)}</b>. "
                       + ("It counts (captured before game start)."
                          if status == "TRACKED" else
                          "It does NOT count (stored only — see status)."))
            except Exception as e:  # noqa: BLE001
                msg = f"Could not file pick: {html.escape(str(e))}"
            body = (f"<!doctype html><meta charset=utf-8>"
                    f"<meta name=viewport content='width=device-width, initial-scale=1'>"
                    f"<p>{msg}</p><p><a href='/'>&larr; back to dashboard</a></p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    HTTPServer(("0.0.0.0", port), H).serve_forever()
