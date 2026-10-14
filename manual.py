"""Manual forward-in: Jake drops a pick he saw (Pikkit / Action / X / DubClub).
Same timestamp rule as every source: counts only if forwarded BEFORE game start;
late forwards are stored as EXCLUDED_LATE and logged, never counted.

Format (pipe-delimited, one line):
  PICK | capper=<name> | sport=NHL | market=moneyline | team=<team name> | odds=-150
Optional fields: line=<number> (spread/total), selection=<text>, starts=<YYYY-MM-DD>,
source=<label, default 'Manual forward-in'>, units=<n>

Examples:
  PICK | capper=HNICaps | sport=NHL | market=moneyline | team=Toronto Maple Leafs | odds=-150
  PICK | capper=HNICaps | sport=NHL | market=spread | team=Boston Bruins | line=-1.5 | odds=+130
  PICK | capper=HNICaps | sport=NFL | market=total | selection=Over 47.5 | line=47.5 | odds=-110 | team=

JAKE'S OWN PICKS are their own SOURCE FAMILY: capper name 'Jake'
(case-insensitive) OR an explicit source=Jake field maps the pick to the
dedicated source 'Jake' (kind='own'), NOT the generic 'Manual forward-in'
family. Source family is the consensus-independence unit, so Jake's picks
are independent of third-party picks filed under Manual — and they follow
every rule everyone else does: flat-1u grading, captured_at < commence_time
(a late Jake pick is EXCLUDED_LATE, no exceptions for the owner), and the
Jake family only votes in consensus once it reaches 100+ tracked picks.
"""
import re
import sys

import db as dbmod


def parse_manual(text):
    if not text.strip().lower().startswith("pick"):
        raise ValueError("line must start with 'PICK |'")
    fields = {}
    for part in text.split("|")[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip().lower()] = v.strip()
    for req in ("capper", "sport", "market"):
        if req not in fields:
            raise ValueError(f"missing required field: {req}")
    odds = fields.get("odds")
    return {
        "capper": fields["capper"],
        "source_label": fields.get("source", "Manual forward-in"),
        "sport": fields["sport"].upper(),
        "market": fields["market"].lower(),
        "selection": fields.get("selection") or fields.get("team") or "",
        "team": fields.get("team"),
        "line": float(fields["line"]) if fields.get("line") else None,
        "odds_claimed": int(float(odds)) if odds else None,
        "units_claimed": float(fields["units"]) if fields.get("units") else None,
        "starts_date": fields.get("starts"),
        "raw_text": text.strip(),
    }


def resolve_source(p):
    """Map parsed fields to (source_name, source_kind). Jake's own picks
    (capper=Jake or source=Jake, case-insensitive) get the dedicated 'Jake'
    source family; everything else keeps its manual-forward-in label."""
    label = (p.get("source_label") or "").strip()
    if label.lower() == "jake" or (p.get("capper") or "").strip().lower() == "jake":
        return "Jake", "own"
    return label or "Manual forward-in", "manual"


def add_manual_pick(conn, text, espn_match=None):
    """Parse, match to an ESPN event (if matcher supplied), insert with rule applied."""
    p = parse_manual(text)
    src_name, src_kind = resolve_source(p)
    notes = ("Jake's own picks — dedicated source family (kind=own). Same rules as "
             "everyone: flat-1u, pre-game timestamp rule, 100+ tracked to vote."
             if src_kind == "own" else
             "Jake forward-in (Pikkit/Action/X/DubClub etc.)")
    sid = dbmod.upsert_source(conn, src_name, src_kind, None, "active", notes)
    cid = dbmod.upsert_capper(conn, sid, p["capper"])
    event_id, commence = None, None
    if espn_match is not None:
        ev = espn_match(conn, p["sport"], p["team"] or p["selection"], p.get("starts_date"))
        if ev is not None:
            event_id, commence = ev["id"], ev["commence_time"]
    pick = {"source_id": sid, "capper_id": cid, "event_id": event_id, "sport": p["sport"],
            "market": p["market"], "selection": p["selection"], "team": p["team"],
            "line": p["line"], "odds_claimed": p["odds_claimed"],
            "units_claimed": p["units_claimed"], "captured_at": dbmod.now_iso(),
            "status": None if event_id else "UNMATCHED",
            "exclusion_reason": None if event_id else "event unmatched — commence_time unverified",
            "commence_time": commence, "raw_text": p["raw_text"]}
    pid, status = dbmod.insert_pick(conn, pick)
    return pid, status, event_id


if __name__ == "__main__":
    import espn
    conn = dbmod.connect()
    line = " ".join(sys.argv[1:])
    pid, status, eid = add_manual_pick(conn, line, espn_match=espn.match_event)
    print(f"pick_id={pid} status={status} event_id={eid}")
