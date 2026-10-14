"""Daily brief (Stage 5). One compact text brief:
  - today's tracked picks
  - any consensus slips (bet-slip format, copy-paste blocks)
  - yesterday's graded results
  - capper top-5 (by tracked sample, with sample warnings)

HARD RULE: returns EMPTY STRING when there is no qualifying consensus today —
silent, exactly like Jake's other digests. The Muse cron posts the output
only when non-empty. Command the cron should call:

    docker exec parlay-tracker python app.py brief

(exit 0, prints the brief, or prints nothing at all when silent.)
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import consensus
import records
import settings as settings_mod

ET = ZoneInfo("America/Toronto")


def generate_brief(conn, today_et=None):
    today = (today_et or datetime.now(ET)).strftime("%Y-%m-%d")
    legs = consensus.qualifying_legs(conn, today)
    # 'today' for suggestions = created on today's ET date (created_at is UTC):
    sugg_today = [s for s in conn.execute(
        "SELECT * FROM suggestions WHERE status='paper' ORDER BY id DESC").fetchall()
        if datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
        .astimezone(ET).strftime("%Y-%m-%d") == today]
    if not legs and not sugg_today:
        return ""  # silent: no qualifying consensus today

    cfg = settings_mod.load()
    lines = [f"PARLAY BRIEF — {today} (ET) · book: {cfg['target_book']} · "
             f"paper period ends {consensus.PAPER_PERIOD_END}"]

    # today's tracked picks
    picks = [p for p in conn.execute(
        "SELECT p.*, c.name AS capper, s.name AS source FROM picks p "
        "JOIN cappers c ON c.id=p.capper_id JOIN sources s ON s.id=p.source_id "
        "WHERE p.status='TRACKED' ORDER BY p.id DESC LIMIT 200").fetchall()
        if p["commence_time"] and datetime.fromisoformat(
            p["commence_time"].replace("Z", "+00:00")).astimezone(ET)
        .strftime("%Y-%m-%d") == today]
    lines.append(f"\nToday's tracked picks ({len(picks)}):")
    for p in picks[:12]:
        lines.append(f"- {p['capper']} ({p['source']}): {p['selection']} "
                     f"{p['odds_claimed'] if p['odds_claimed'] is not None else p['odds_available'] or ''}")
    if len(picks) > 12:
        lines.append(f"  … +{len(picks) - 12} more")

    # consensus slips in slip format
    lines.append("\nConsensus slips:")
    if sugg_today:
        for s in sugg_today:
            lines.append("")
            lines.append(s["slip_text"] or "(slip text unavailable)")
    elif legs:
        for l in legs[:consensus.MAX_LEGS]:
            fams = ", ".join(f"{f} (n={n})" for f, n in l["family_counts"].items())
            lines.append(f"- {l['selection']} ({l['price_proxy']}, ESPN/DK proxy — "
                         f"check price on {cfg['target_book']}) · {fams}")

    # yesterday's graded results
    yday = (datetime.fromisoformat(today) - timedelta(days=1)).strftime("%Y-%m-%d")
    grades = [g for g in conn.execute(
        "SELECT c.name AS capper, g.result, g.profit_units, g.graded_at FROM grades g "
        "JOIN picks p ON p.id=g.pick_id JOIN cappers c ON c.id=p.capper_id "
        "WHERE p.status='TRACKED'").fetchall()
        if datetime.fromisoformat(g["graded_at"].replace("Z", "+00:00"))
        .astimezone(ET).strftime("%Y-%m-%d") == yday]
    if grades:
        w = sum(1 for g in grades if g["result"] == "win")
        l_ = sum(1 for g in grades if g["result"] == "loss")
        units = sum(g["profit_units"] or 0 for g in grades)
        lines.append(f"\nYesterday's graded: {w}W-{l_}L ({units:+.2f}u flat-1u)")

    # capper top-5 by tracked sample
    top = records.capper_records(conn)[:5]
    if top:
        lines.append("\nCapper top-5 (by tracked sample):")
        for r in top:
            lines.append(f"- {r['capper']} ({r['source']}): {r['w']}-{r['l']}-{r['push']} "
                         f"n={r['n']} ROI {r['roi_pct']}% · {r['sample_label']}")
    return "\n".join(lines)
