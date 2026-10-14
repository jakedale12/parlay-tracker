"""Per-capper / per-source records (Stage 3 depth). TRACKED picks only —
EXCLUDED_LATE and PIPELINE_TEST picks never count. Sample-size discipline:
  n < 100: 'insufficient sample' | 100-499: 'warming up' | >=500: 'followable sample'
NOBODY is followable under 500 tracked picks, regardless of ROI.

Metrics: W-L-P, flat-1u ROI, win rate, current + longest streaks, max drawdown
(units, over the ordered graded equity curve), avg approx-CLV and beat-close %
(share of CLV-known picks with CLV > 0), split by sport x market, and windows
all-time + last-100 picks."""
import oddsmath

FOLLOWABLE_N = 500
WARMING_N = 100


def sample_label(n):
    if n >= FOLLOWABLE_N:
        return "followable sample (>=500 tracked picks)"
    if n >= WARMING_N:
        return "warming up (100-499 picks — NOT followable yet)"
    return "insufficient sample (<100 picks — NOT followable)"


def consensus_eligible(n_tracked, independent_sources):
    """A leg qualifies for consensus only with 2+ INDEPENDENT sources and
    each contributing source at warming-up (100+) or better."""
    return independent_sources >= 2 and n_tracked >= WARMING_N


def max_drawdown(profits):
    """Largest peak-to-trough drop (units, >= 0) of the cumulative profit curve."""
    peak = run = dd = 0.0
    for p in profits:
        if p is None:
            continue
        run += p
        peak = max(peak, run)
        dd = max(dd, peak - run)
    return round(dd, 4)


def _streak_info(results):
    """results: ordered W/L/P result strings (graded only).
    Returns (current_streak_str, longest_win, longest_loss)."""
    lw = ll = run_w = run_l = 0
    for r in results:
        if r == "win":
            run_w += 1; run_l = 0
        elif r == "loss":
            run_l += 1; run_w = 0
        else:  # push/void breaks both runs
            run_w = run_l = 0
        lw = max(lw, run_w); ll = max(ll, run_l)
    cur = "—"
    if results:
        last = results[-1]
        if last in ("win", "loss"):
            n = 0
            for r in reversed(results):
                if r == last:
                    n += 1
                else:
                    break
            cur = f"{last[0].upper()}{n}"
    return cur, lw, ll


def _summarize(rows):
    """rows: graded-or-not pick dicts ordered by pick id for ONE entity.
    Returns the full Stage 3 metric set."""
    graded = [r for r in rows if r["result"] in ("win", "loss", "push", "void")]
    profits = [r["profit_units"] for r in graded if r["profit_units"] is not None]
    clvs = [r["clv_pct"] for r in rows if r["clv_pct"] is not None]
    w = sum(1 for r in graded if r["result"] == "win")
    l = sum(1 for r in graded if r["result"] == "loss")
    p = sum(1 for r in graded if r["result"] in ("push", "void"))
    cur, lw, ll = _streak_info([r["result"] for r in graded])
    decided = w + l
    return {
        "n": len(rows), "n_graded": len(graded), "w": w, "l": l, "push": p,
        "win_rate": round(w / decided * 100, 1) if decided else None,
        "roi_pct": oddsmath.roi_pct(profits),
        "units": round(sum(profits), 4) if profits else None,
        "avg_clv_pct": round(sum(clvs) / len(clvs), 2) if clvs else None,
        "beat_close_pct": (round(sum(1 for c in clvs if c > 0) / len(clvs) * 100, 1)
                           if clvs else None),
        "clv_n": len(clvs),
        "max_drawdown_units": max_drawdown([r["profit_units"] for r in graded]),
        "current_streak": cur, "longest_win_streak": lw, "longest_loss_streak": ll,
    }


def _fetch_rows(conn, where=""):
    return conn.execute(
        "SELECT c.name AS capper, s.name AS source, p.id AS pick_id, p.sport, p.market, "
        "g.result, g.profit_units, g.clv_pct FROM picks p "
        "JOIN cappers c ON c.id=p.capper_id JOIN sources s ON s.id=p.source_id "
        f"LEFT JOIN grades g ON g.pick_id=p.id WHERE p.status='TRACKED' {where} "
        "ORDER BY p.id").fetchall()


def capper_records(conn):
    rows = _fetch_rows(conn)
    agg = {}
    for r in rows:
        a = agg.setdefault((r["source"], r["capper"]),
                           {"source": r["source"], "capper": r["capper"],
                            "rows": [], "by_sport_market": {}})
        a["rows"].append(r)
        sm = a["by_sport_market"].setdefault(f"{r['sport']}/{r['market']}", [])
        sm.append(r)
    out = []
    for a in agg.values():
        rec = {"source": a["source"], "capper": a["capper"]}
        rec.update(_summarize(a["rows"]))
        last100 = _summarize(a["rows"][-100:]) if a["rows"] else _summarize([])
        rec["last100"] = {k: last100[k] for k in
                          ("n", "w", "l", "push", "win_rate", "roi_pct", "units")}
        rec["by_sport_market"] = {
            k: {kk: vv for kk, vv in _summarize(v).items()
                if kk in ("n", "w", "l", "push", "win_rate", "roi_pct", "units")}
            for k, v in sorted(a["by_sport_market"].items())}
        rec["streak"] = rec["current_streak"]
        rec["profits"] = [r["profit_units"] for r in a["rows"]
                        if r["profit_units"] is not None]
        rec["clvs"] = [r["clv_pct"] for r in a["rows"] if r["clv_pct"] is not None]
        rec["sample_label"] = sample_label(rec["n"])
        rec["followable"] = rec["n"] >= FOLLOWABLE_N
        out.append(rec)
    return sorted(out, key=lambda x: -x["n"])


def source_records(conn):
    """Same metric set aggregated across all cappers of one source."""
    rows = _fetch_rows(conn)
    agg = {}
    for r in rows:
        agg.setdefault(r["source"], []).append(r)
    out = []
    for source, srows in agg.items():
        rec = {"source": source}
        rec.update(_summarize(srows))
        rec["sample_label"] = sample_label(rec["n"])
        rec["followable"] = rec["n"] >= FOLLOWABLE_N
        out.append(rec)
    return sorted(out, key=lambda x: -x["n"])


def tracked_counts_by_source(conn):
    """TRACKED pick count per source name (consensus eligibility input)."""
    return {r["source"]: r["n"] for r in conn.execute(
        "SELECT s.name AS source, COUNT(*) AS n FROM picks p "
        "JOIN sources s ON s.id=p.source_id WHERE p.status='TRACKED' "
        "GROUP BY s.name").fetchall()}


def streaks(conn):
    """Current W/L streak per capper from graded TRACKED picks, newest first."""
    rows = conn.execute(
        "SELECT c.name, g.result FROM picks p JOIN cappers c ON c.id=p.capper_id "
        "JOIN grades g ON g.pick_id=p.id WHERE p.status='TRACKED' AND g.result IN ('win','loss') "
        "ORDER BY p.id DESC").fetchall()
    out = {}
    for r in rows:
        if r["name"] in out and out[r["name"]]["type"] != r["result"]:
            continue
        cur = out.setdefault(r["name"], {"type": r["result"], "count": 0})
        if cur["type"] == r["result"]:
            cur["count"] += 1
    return {k: f"{v['type'][0].upper()}{v['count']}" for k, v in out.items()}
