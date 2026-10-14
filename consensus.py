"""Consensus + paper parlay suggestions (Stage 4).

ELIGIBILITY (hard rules, no exceptions):
- A leg qualifies ONLY when 2+ INDEPENDENT source families pick the same
  game/market/side AND every contributing family has >= 100 TRACKED picks
  (records.WARMING_N; 'full weight' noted at >= 500).
- INDEPENDENCE / de-dup rule: the unit of independence is the SOURCE FAMILY.
  Family = source name, except every Covers property (computer picks, human
  experts, any future Covers model) is ONE family 'Covers'. Two cappers in the
  same family voting the same leg = ONE vote (they share an editorial/model
  pipeline, so counting them twice would fake consensus). Within a family the
  earliest captured pick represents the vote. Different families are treated
  as independent pipelines (e.g. Covers vs a manual forward-in from X).
- Builder: max 3 legs, cross-game preferred (distinct events first; a second
  leg on the same event is allowed only as filler and flags the ticket SGP).
- Pricing: each leg's price is the ESPN/DK proxy price (latest odds snapshot,
  labelled proxy — never a book price). Fair price = product of leg decimal
  odds. Haircut % is defined vs a book's actual parlay price; NO book price is
  available to this system, so haircut_pct is NULL and displayed as an
  estimate warning ('real book price will be lower than the naïve product').
  A book/SGP price is never invented.
- PAPER ONLY: suggestions are paper-tracked (30-day paper period
  2026-10-13 -> 2026-11-12). Stake 0.25u, or 0.5u when every contributing
  family is at full weight (>= 500 tracked). The system NEVER places a bet.
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db as dbmod
import oddsmath
import records
import settings as settings_mod

ET = ZoneInfo("America/Toronto")
PAPER_PERIOD_START = "2026-10-13"
PAPER_PERIOD_END = "2026-11-12"  # 30-day paper track ends Nov 12, 2026
MAX_LEGS = 3


def source_family(source_name):
    if (source_name or "").lower().startswith("covers"):
        return "Covers"
    return source_name


def family_tracked_counts(conn):
    counts = {}
    for source, n in records.tracked_counts_by_source(conn).items():
        counts[source_family(source)] = counts.get(source_family(source), 0) + n
    return counts


def _side_key(pick):
    market = (pick["market"] or "").lower()
    if market == "total":
        sel = (pick["selection"] or "").lower()
        side = "over" if "over" in sel else ("under" if "under" in sel else sel)
        return (pick["event_id"], market, side, pick["line"])
    team = (pick["team"] or pick["selection"] or "").strip().lower()
    return (pick["event_id"], market, team, pick["line"] if market == "spread" else None)


def _leg_price(conn, pick):
    """ESPN/DK proxy price for a leg: latest snapshot for the event/market/side,
    falling back to the pick's captured price. Returns (american, label)."""
    snap = conn.execute(
        "SELECT * FROM odds_snapshots WHERE event_id=? ORDER BY captured_at DESC, id DESC "
        "LIMIT 1", (pick["event_id"],)).fetchone()
    if snap is not None:
        ev = conn.execute("SELECT home, away FROM events WHERE id=?",
                          (pick["event_id"],)).fetchone()
        market = (pick["market"] or "").lower()
        team = (pick["team"] or "").lower()
        is_home = ev and team and team in (ev["home"] or "").lower()
        if market == "moneyline":
            px = snap["home_ml"] if is_home else snap["away_ml"]
        elif market == "spread":
            px = snap["spread_home_price"] if is_home else snap["spread_away_price"]
        elif market == "total":
            px = snap["over_price"] if "over" in (pick["selection"] or "").lower() \
                else snap["under_price"]
        else:
            px = None
        if px is not None:
            return px, "ESPN/DK proxy"
    px = pick["odds_available"] or pick["odds_claimed"]
    return px, "captured price (ESPN/DK proxy)" if px else (None, "no price")


def qualifying_legs(conn, on_date_et=None):
    """Legs (one per event/market/side) backed by >= 2 independent families,
    each family >= 100 tracked picks, on events starting on on_date_et
    (default: today, America/Toronto) and not yet final."""
    if on_date_et is None:
        on_date_et = datetime.now(ET).strftime("%Y-%m-%d")
    counts = family_tracked_counts(conn)
    rows = conn.execute(
        "SELECT p.*, s.name AS source, c.name AS capper, e.home, e.away, "
        "e.commence_time, e.is_final FROM picks p "
        "JOIN sources s ON s.id=p.source_id JOIN cappers c ON c.id=p.capper_id "
        "JOIN events e ON e.id=p.event_id WHERE p.status='TRACKED' AND e.is_final=0 "
        "ORDER BY p.captured_at").fetchall()
    groups = {}
    for r in rows:
        # event day in ET
        try:
            day = datetime.fromisoformat(
                r["commence_time"].replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        if day != on_date_et:
            continue
        key = _side_key(r)
        fam = source_family(r["source"])
        g = groups.setdefault(key, {"key": key, "event_id": r["event_id"],
                                    "sport": r["sport"], "market": r["market"],
                                    "home": r["home"], "away": r["away"],
                                    "commence_time": r["commence_time"],
                                    "families": {}, "picks": []})
        if fam not in g["families"]:
            g["families"][fam] = r  # earliest captured pick = the family's vote
        g["picks"].append(r)
    legs = []
    for g in groups.values():
        fams = {f: counts.get(f, 0) for f in g["families"]}
        if len(fams) < 2 or any(n < records.WARMING_N for n in fams.values()):
            continue
        rep = next(iter(g["families"].values()))
        price, price_label = _leg_price(conn, rep)
        legs.append({
            "event_id": g["event_id"], "sport": g["sport"], "market": g["market"],
            "selection": rep["selection"], "team": rep["team"], "line": rep["line"],
            "matchup": f"{g['away']} @ {g['home']}", "commence_time": g["commence_time"],
            "families": sorted(fams), "family_counts": fams,
            "full_weight": all(n >= records.FOLLOWABLE_N for n in fams.values()),
            "pick_ids": [g["families"][f]["id"] for f in sorted(fams)],
            "price_proxy": price, "price_label": price_label,
        })
    legs.sort(key=lambda x: (-len(x["families"]), x["commence_time"] or ""))
    return legs


def family_stats(conn):
    """Per-family rollup for the slip 'why' line: tracked n + avg approx-CLV."""
    per = {}
    for fam, n in family_tracked_counts(conn).items():
        per[fam] = {"n": n, "avg_clv_pct": None}
    clvs = {}
    for r in records._fetch_rows(conn):
        fam = source_family(r["source"])
        if r["clv_pct"] is not None:
            clvs.setdefault(fam, []).append(r["clv_pct"])
    for fam, vals in clvs.items():
        if fam in per:
            per[fam]["avg_clv_pct"] = round(sum(vals) / len(vals), 2)
    return per


def _fmt_american(a):
    return f"{a:+d}" if isinstance(a, int) else "—"


def _leg_line(leg, idx):
    when = ""
    if leg.get("commence_time"):
        try:
            when = datetime.fromisoformat(
                leg["commence_time"].replace("Z", "+00:00")).astimezone(ET).strftime(
                "%a %b %d %I:%M %p ET")
        except Exception:  # noqa: BLE001
            when = ""
    game = leg.get("matchup") or ""
    return (f"Leg {idx}: {leg['selection']} ({_fmt_american(leg.get('price_proxy'))})"
            f" — {leg.get('sport', '')} {game} {when}").rstrip()


def format_slip(legs, fair_american, implied_pct, stake_units, fams_stats,
                target_book="FanDuel", paper=True):
    """The bet-slip-ready text block attached to EVERY suggestion (dashboard,
    ntfy, brief). Prices here are ESPN/DK proxy, never the book's price, so the
    slip carries the exact label 'check price on <target_book>'. Stake is in
    UNITS only — a dollar amount never appears. Suggestion-only: nothing is
    opened or placed."""
    label = f"check price on {target_book}"
    head = "PARLAY SLIP — PAPER (not placed)" if paper else "PARLAY SLIP"
    lines = [head, f"Book: {target_book} — {label}"]
    for i, leg in enumerate(legs, 1):
        lines.append(_leg_line(leg, i))
    lines.append(f"Parlay odds: {_fmt_american(fair_american)} "
                 f"(implied {implied_pct}%) — fair product of ESPN/DK proxy "
                 f"prices, {label}")
    lines.append(f"Stake: {stake_units:g}u{' paper' if paper else ''}")
    why = []
    for fam in sorted({f for l in legs for f in l["families"]}):
        st = fams_stats.get(fam, {})
        clv = st.get("avg_clv_pct")
        why.append(f"{fam} (n={st.get('n', '?')} tracked, approx-CLV "
                   f"{clv if clv is not None else 'n/a'}%)")
    lines.append("Why: consensus of " + " + ".join(why) +
                 " on the same legs.")
    return "\n".join(lines)


def fair_product(legs):
    """Fair (naïve product) price from leg American prices.
    Returns (decimal, american, implied_pct) or (None, None, None)."""
    dec = 1.0
    for leg in legs:
        d = oddsmath.american_to_decimal(leg["price_proxy"])
        if d is None:
            return None, None, None
        dec *= d
    american = round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))
    return round(dec, 4), american, round(100.0 / dec, 2)


def build_suggestions(conn, on_date_et=None):
    """Create at most one paper suggestion per day from qualifying legs.
    Returns list of created suggestion dicts ([] = nothing qualifies = silent)."""
    if on_date_et is None:
        on_date_et = datetime.now(ET).strftime("%Y-%m-%d")
    legs = [l for l in qualifying_legs(conn, on_date_et) if l["price_proxy"] is not None]
    if len(legs) < 2:
        return []
    if conn.execute("SELECT 1 FROM suggestions WHERE substr(created_at,1,10)=?",
                    (datetime.now(timezone.utc).strftime("%Y-%m-%d"),)).fetchone():
        return []  # one paper suggestion per day max — no spam, no dupes
    # cross-game preferred: distinct events first, same-event legs only as filler
    chosen, seen_events = [], set()
    for leg in legs:
        if leg["event_id"] not in seen_events:
            chosen.append(leg); seen_events.add(leg["event_id"])
        if len(chosen) >= MAX_LEGS:
            break
    if len(chosen) < MAX_LEGS:
        for leg in legs:
            if leg not in chosen:
                chosen.append(leg)
            if len(chosen) >= MAX_LEGS:
                break
    chosen = chosen[:MAX_LEGS]
    if len(chosen) < 2:
        return []
    is_sgp = len({l["event_id"] for l in chosen}) < len(chosen)
    dec, american, implied = fair_product(chosen)
    fams = sorted({f for l in chosen for f in l["families"]})
    full = all(l["full_weight"] for l in chosen)
    stake = 0.5 if full else 0.25
    legs_json = json.dumps([{
        "pick_ids": l["pick_ids"], "event_id": l["event_id"], "sport": l["sport"],
        "market": l["market"], "selection": l["selection"], "team": l["team"],
        "line": l["line"], "matchup": l["matchup"], "commence_time": l["commence_time"],
        "price_proxy": l["price_proxy"], "price_label": l["price_label"],
        "families": l["families"],
    } for l in chosen])
    cfg = settings_mod.load()
    slip = format_slip(chosen, american, implied, stake, family_stats(conn),
                       target_book=cfg["target_book"], paper=True)
    cur = conn.execute(
        "INSERT INTO suggestions(created_at,legs_json,status,stake_units,fair_decimal,"
        "fair_american,implied_pct,haircut_pct,is_sgp,families,slip_text,notes) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (dbmod.now_iso(), legs_json, "paper", stake, dec, american, implied, None,
         1 if is_sgp else 0, ",".join(fams), slip,
         f"PAPER ONLY — never a placed bet. Prices are ESPN/DK proxy, not a book price. "
         f"Haircut: n/a (no book parlay price available; a real book price will be lower "
         f"than this naïve product). Paper period {PAPER_PERIOD_START} to {PAPER_PERIOD_END}."))
    conn.commit()
    return [{"id": cur.lastrowid, "legs": chosen, "stake_units": stake,
             "fair_decimal": dec, "fair_american": american, "implied_pct": implied,
             "haircut_pct": None, "is_sgp": is_sgp, "families": fams, "slip_text": slip}]


def grade_suggestions(conn):
    """Grade open paper suggestions from their legs' pick grades.
    Push/void legs drop out (product recomputed); all legs pushed -> push.
    Returns list of newly graded suggestion dicts."""
    out = []
    rows = conn.execute(
        "SELECT * FROM suggestions WHERE status='paper' AND result IS NULL").fetchall()
    for s in rows:
        legs = json.loads(s["legs_json"] or "[]")
        leg_results, eff_dec = [], 1.0
        pending = False
        for leg in legs:
            results = [g["result"] for g in conn.execute(
                "SELECT g.result FROM grades g JOIN picks p ON p.id=g.pick_id "
                "WHERE p.id IN (%s)" % ",".join("?" * len(leg["pick_ids"])),
                leg["pick_ids"]).fetchall()]
            if not results or any(r == "ungraded" for r in results):
                pending = True
                break
            leg_results.append(results[0])
            if results[0] == "win":
                d = oddsmath.american_to_decimal(leg["price_proxy"])
                if d is None:
                    pending = True
                    break
                eff_dec *= d
        if pending:
            continue
        stake = s["stake_units"] or 0.25
        if any(r == "loss" for r in leg_results):
            result, profit = "loss", -stake
        elif all(r in ("push", "void") for r in leg_results):
            result, profit = "push", 0.0
        else:
            result, profit = "win", round(stake * (eff_dec - 1.0), 4)
        conn.execute(
            "UPDATE suggestions SET result=?, profit_units=?, graded_at=? WHERE id=?",
            (result, profit, dbmod.now_iso(), s["id"]))
        conn.commit()
        out.append({"id": s["id"], "result": result, "profit_units": profit})
    return out


def in_quiet_hours(now_et=None, cfg=None):
    """Quiet hours from settings (default 23:00-07:00 America/Toronto)."""
    cfg = cfg or settings_mod.load()
    now_et = now_et or datetime.now(ET)
    start = int(cfg.get("quiet_hours_start", 23))
    end = int(cfg.get("quiet_hours_end", 7))
    if start <= end:
        return start <= now_et.hour < end
    return now_et.hour >= start or now_et.hour < end


def _leg_starts_within_hour(created, now=None):
    now = now or datetime.now(timezone.utc)
    for s in created:
        for leg in s["legs"]:
            ct = leg.get("commence_time")
            if not ct:
                continue
            try:
                start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if now <= start <= now + timedelta(hours=1):
                return True
    return False


def notify_new_suggestions(created, graded=None):
    """ntfy ONLY for a NEW qualifying consensus parlay (the slip text block is
    the message body). At most one suggestion/day is built, so this is a few
    per day max. Quiet hours respected UNLESS a leg starts within the hour.
    Graded results do NOT push (they live on the dashboard + daily brief).
    Silent when nothing new qualifies."""
    if not created:
        return None
    if in_quiet_hours() and not _leg_starts_within_hour(created):
        return "quiet-hours: notification suppressed (no leg starts within the hour)"
    import notify
    cfg = settings_mod.load()
    sent = []
    for s in created:
        body = s.get("slip_text") or format_slip(
            s["legs"], s["fair_american"], s["implied_pct"], s["stake_units"],
            {}, target_book=cfg["target_book"], paper=True)
        sent.append(notify.ntfy("Parlay slip — PAPER (not placed)", body))
    return sent
