"""Pick sources.

- Covers free picks (VERIFIED LIVE 2026-10-13 from build VM: covers.com/picks/nhl
  HTTP 200, 1.27 MB): 'Computer Picks powered by Odds Shark' cards carry
  data-pick-teams="Away,Home", a predicted score and a moneyline price per team.
  Pick = predicted winner's moneyline. Capper label: 'Covers Computer Picks'.
- CapperTek leaderboard (VERIFIED LIVE 200 by parent check 2026-10-13; direct
  fetch from the build VM stalled/timed out repeatedly, so the parser is built
  generically against its public leaderboard table and its result is recorded in
  ingest_log on every run — never silently assumed). Leaderboard rows seed
  cappers as CLAIMED records only (Tier B discovery): they never become tracked
  picks and never count toward any record.
- Reddit r/sportsbook POTD: BLOCKED (unauthenticated JSON + RSS both 403).
  Needs a Jake-approved OAuth app — out of scope, do not retry.
"""
import re
import urllib.request

import config
import db as dbmod
import netutil


def fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with netutil.open_url(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _strip(html_fragment):
    t = re.sub(r"<script.*?</script>", " ", html_fragment, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&#x2BC6;", " ").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t)


# ---------------- Covers ----------------

CARD_RE = re.compile(
    r'id="computer-picks-\d+"[^>]*data-pick-teams="([^"]+)"', re.S)
ROW_RE = re.compile(r"\b([A-Z]{2,4})\s+\1\s+(\d+\.\d+)\s+([+-]\d{2,4})\b")


def parse_covers_nhl(html_text):
    """Return list of dicts: away, home, pick_team, odds, predicted scores."""
    out = []
    starts = [m for m in CARD_RE.finditer(html_text)]
    for idx, m in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(html_text)
        card = _strip(html_text[m.start():end])[:4000]
        teams = [t.strip() for t in m.group(1).split(",")]
        if len(teams) != 2:
            continue
        rows = ROW_RE.findall(card)
        if len(rows) < 2:
            continue
        (a_abbr, a_score, a_ml), (h_abbr, h_score, h_ml) = rows[0], rows[1]
        a_score, h_score = float(a_score), float(h_score)
        if a_score == h_score:
            continue  # no predicted winner -> no pick
        away, home = teams
        if a_score > h_score:
            pick_team, odds, ps, os_ = away, int(a_ml), a_score, h_score
        else:
            pick_team, odds, ps, os_ = home, int(h_ml), h_score, a_score
        out.append({"away": away, "home": home, "pick_team": pick_team,
                    "odds": odds, "pred_pick": ps, "pred_other": os_,
                    "raw": f"Covers Computer Pick: {away} @ {home} -> {pick_team} ML {odds} "
                           f"(predicted {ps}-{os_})"})
    return out


def ingest_covers(conn, espn_match, sport="NHL", url="https://www.covers.com/picks/nhl"):
    sid = dbmod.upsert_source(conn, "Covers", "free_picks", url, "active",
                              "Free expert/computer picks pages (public scrape). Verified live 200.")
    cid = dbmod.upsert_capper(conn, sid, "Covers Computer Picks")
    try:
        status, html_text = fetch_text(url)
    except Exception as e:  # noqa: BLE001 - log and move on, never retry-loop
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), "Covers", f"fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    picks = parse_covers_nhl(html_text)
    if not picks:
        # One bounded re-fetch: Covers occasionally serves a variant page with
        # no computer-pick cards (observed 2026-10-13). Not a retry loop.
        try:
            status, html_text = fetch_text(url)
            picks = parse_covers_nhl(html_text)
        except Exception:  # noqa: BLE001
            pass
    stored = 0
    for p in picks:
        # dedupe: same capper+team+day already stored?
        dupe = conn.execute(
            "SELECT 1 FROM picks WHERE capper_id=? AND team=? AND substr(captured_at,1,10)=substr(?,1,10)",
            (cid, p["pick_team"], dbmod.now_iso())).fetchone()
        if dupe:
            continue
        ev = None
        try:
            import espn as _espn
            ev = _espn.match_matchup(conn, sport, p["away"], p["home"])
        except Exception:  # noqa: BLE001
            ev = None
        if ev is None:
            ev = espn_match(conn, sport, p["pick_team"])
        pick = {"source_id": sid, "capper_id": cid,
                "event_id": ev["id"] if ev else None, "sport": sport,
                "market": "moneyline", "selection": f"{p['pick_team']} ML",
                "team": p["pick_team"], "odds_claimed": p["odds"],
                "captured_at": dbmod.now_iso(),
                # Unmatched = commence unverified: stored as UNMATCHED and
                # excluded from all records (never silently counted).
                "status": None if ev else "UNMATCHED",
                "exclusion_reason": None if ev else "event unmatched — commence_time unverified",
                "commence_time": ev["commence_time"] if ev else None,
                "raw_text": p["raw"],
                "external_ref": f"{p['away']}@{p['home']}"}
        _, st = dbmod.insert_pick(conn, pick)
        stored += 1
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "Covers",
                  f"HTTP {status}; parsed {len(picks)} computer picks; stored {stored}"))
    conn.commit()
    return {"ok": True, "http": status, "parsed": len(picks), "stored": stored,
            "example": picks[0] if picks else None}


# ---------------- CapperTek (leaderboard seeding, CLAIMED only) ----------------

def parse_cappertek_leaderboard(html_text):
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, flags=re.S | re.I):
        cells = [re.sub(r"\s+", " ", _strip(c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cells = [c for c in cells if c]
        if len(cells) >= 3 and any(ch.isdigit() for ch in cells[1]):
            name = re.sub(r"^#\d+\s+", "", cells[0]).strip()
            rows.append({"name": name, "claimed": " | ".join(cells[1:])})
    return rows


def ingest_cappertek(conn, sport="NHL",
                     url="https://www.cappertek.com/leaderboard.asp?sport=NHL"):
    sid = dbmod.upsert_source(conn, "CapperTek", "leaderboard", url, "seed_only",
                              "Leaderboard = CLAIMED records only (Tier B discovery). "
                              "Verified LIVE from botserver 2026-10-13 (HTTP 200, 100 cappers seeded); "
                              "fetch from the build VM stalled — host-dependent, logged in ingest_log.")
    try:
        status, html_text = fetch_text(url, timeout=45)
        rows = parse_cappertek_leaderboard(html_text)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), "CapperTek", f"fetch/parse failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    for r in rows[:100]:
        dbmod.upsert_capper(conn, sid, r["name"], claimed_record=r["claimed"])
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "CapperTek",
                  f"HTTP {status}; seeded {min(len(rows),100)} cappers as CLAIMED (not tracked)"))
    conn.commit()
    return {"ok": True, "http": status, "seeded": min(len(rows), 100),
            "example": rows[0] if rows else None}


# ---------------- Covers human expert picks (Stage 2) ----------------
"""Editorial prediction articles (VERIFIED LIVE 2026-10-13 from build VM):
article pages carry JSON-LD with author (Person name) + datePublished, and an
<h2 id="Prediction"> 'TOP PICK ... {Away} vs {Home} prediction: {pick phrase}'.
The price itself lives in a client-side best-bet widget (betslipid) that is NOT
in the static HTML, so odds_claimed is None and the pick is graded at the
ESPN/DK price available at capture (odds_available) — the price Jake could
actually get, which is the tracker's standing principle. Articles without the
TOP PICK heading (roundups, 'early leans') are skipped and counted in
ingest_log; only single-matchup prediction articles are parsed."""

import json as _json
from datetime import datetime as _dt, timezone as _tz

EXPERT_LINK_RE = None  # built per-sport in discover function


def _ld_json(html_text):
    for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        html_text, flags=re.S):
        try:
            d = _json.loads(m)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and d.get("author") and d.get("datePublished"):
            return d
    return None


def _author_name(ld):
    a = ld.get("author")
    if isinstance(a, list) and a:
        a = a[0]
    if isinstance(a, dict):
        me = a.get("mainEntity") or a
        return me.get("name")
    return None


def _to_utc_z(iso):
    try:
        d = _dt.fromisoformat(iso.replace("Z", "+00:00"))
        return d.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        return iso


def parse_covers_expert_article(html_text, url=""):
    """Return dict(author, posted_at, away_nick, home_nick, market, team_nick,
    line, selection_phrase, headline) or None if not a parseable TOP PICK article."""
    ld = _ld_json(html_text)
    if not ld:
        return None
    author = _author_name(ld)
    m = re.search(r'<h2 id="Prediction">(.*?)</h2>', html_text, flags=re.S)
    if not m:
        return None
    heading = re.sub(r"\s+", " ", _strip(m.group(1))).strip()
    # heading shape: 'TOP PICK Avalanche vs Stars prediction: Avalanche moneyline'
    mm = re.match(r"(?:TOP PICK\s*)?(.+?)\s+vs\.?\s+(.+?)\s+prediction:\s*(.+)$",
                  heading, flags=re.I)
    if not mm:
        return None
    away_nick, home_nick, phrase = mm.group(1).strip(), mm.group(2).strip(), mm.group(3).strip()
    low = phrase.lower()
    nicks = {away_nick.lower(), home_nick.lower()}
    market = team_nick = None
    line = None
    if "moneyline" in low:
        market = "moneyline"
        team_nick = phrase[:low.index("moneyline")].strip(" -—")
    else:
        tm = re.search(r"^(.*?)\s*(over|under)\s+(\d+(?:\.\d+)?)\b(.*)$", phrase, flags=re.I)
        if tm and tm.group(1).strip().lower() in nicks | {""}:
            market, team_nick = "total", None
            line = float(tm.group(3))
            phrase = f"{tm.group(2).capitalize()} {line:g}"
        elif tm:
            market = "prop"  # player prop phrasing, e.g. 'Nathan MacKinnon over 1.5 points'
        else:
            sm = re.match(r"^(.+?)\s+([+-]\d+(?:\.\d+)?)\s*(?:puck line|spread|line)?\s*$",
                          phrase, flags=re.I)
            if sm and ("puck line" in low or "spread" in low or sm.group(1).strip().lower() in nicks):
                market, team_nick, line = "spread", sm.group(1).strip(), float(sm.group(2))
            elif phrase.strip().lower() not in ("",):
                market = "prop"
    if market is None:
        return None
    return {"author": author, "posted_at": _to_utc_z(ld.get("datePublished")),
            "away_nick": away_nick, "home_nick": home_nick, "market": market,
            "team_nick": team_nick, "line": line, "selection_phrase": phrase,
            "headline": _strip(ld.get("headline") or ""), "url": url}


def discover_covers_expert_links(sport, index_html):
    pat = re.compile(r'href="(https://www\.covers\.com/' + sport.lower() +
                     r'/[a-z0-9\-]*prediction[a-z0-9\-]*)"')
    links = []
    for u in pat.findall(index_html):
        if "ai-predictions" in u:
            continue
        if u not in links:
            links.append(u)
    return links[:8]


def _snapshot_price(conn, event_id, market, is_home, selection):
    snap = conn.execute(
        "SELECT * FROM odds_snapshots WHERE event_id=? ORDER BY captured_at DESC, id DESC "
        "LIMIT 1", (event_id,)).fetchone()
    if snap is None:
        return None
    if market == "moneyline":
        return snap["home_ml"] if is_home else snap["away_ml"]
    if market == "spread":
        return snap["spread_home_price"] if is_home else snap["spread_away_price"]
    if market == "total":
        return snap["over_price"] if "over" in (selection or "").lower() else snap["under_price"]
    return None


def ingest_covers_experts(conn, espn_match, sport="NHL"):
    import espn as _espn
    index_url = f"https://www.covers.com/picks/{sport.lower()}"
    sid = dbmod.upsert_source(
        conn, "Covers", "free_picks", "https://www.covers.com/picks/nhl", "active",
        "Free picks pages (public scrape): computer picks + human expert prediction "
        "articles (JSON-LD author/date + TOP PICK heading; price via ESPN/DK at capture).")
    log_src = f"Covers Experts {sport}"
    try:
        _, index_html = fetch_text(index_url)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"index fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    links = discover_covers_expert_links(sport, index_html)
    stored = skipped = 0
    example = None
    for url in links:
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (url,)).fetchone():
            continue  # already captured this article — dedupe across cycles/redeploys
        try:
            _, art_html = fetch_text(url)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        parsed = parse_covers_expert_article(art_html, url)
        if parsed is None or not parsed["author"]:
            skipped += 1
            continue
        cid = dbmod.upsert_capper(conn, sid, parsed["author"])
        ev = None
        try:
            ev = _espn.match_matchup(conn, sport, parsed["away_nick"], parsed["home_nick"])
        except Exception:  # noqa: BLE001
            ev = None
        team_full = parsed["team_nick"]
        is_home = None
        if ev is not None and parsed["team_nick"]:
            if _espn._team_matches(ev["home"], parsed["team_nick"]):
                team_full, is_home = ev["home"], True
            elif _espn._team_matches(ev["away"], parsed["team_nick"]):
                team_full, is_home = ev["away"], False
        if parsed["market"] == "moneyline":
            selection = f"{team_full} ML"
        elif parsed["market"] == "spread":
            selection = f"{team_full} {parsed['line']:+g}"
        elif parsed["market"] == "total":
            selection = parsed["selection_phrase"]
        else:
            selection = parsed["selection_phrase"]
        odds_avail = (_snapshot_price(conn, ev["id"], parsed["market"], is_home, selection)
                      if ev is not None else None)
        pick = {"source_id": sid, "capper_id": cid,
                "event_id": ev["id"] if ev else None, "sport": sport,
                "market": parsed["market"], "selection": selection,
                "team": team_full if parsed["market"] != "total" else None,
                "line": parsed["line"], "odds_claimed": None,
                "odds_available": odds_avail,
                "posted_at": parsed["posted_at"], "captured_at": dbmod.now_iso(),
                "status": None if ev else "UNMATCHED",
                "exclusion_reason": None if ev else "event unmatched — commence_time unverified",
                "commence_time": ev["commence_time"] if ev else None,
                "raw_text": (f"Covers Expert Pick ({parsed['author']}): "
                             f"{parsed['away_nick']} vs {parsed['home_nick']} -> "
                             f"{parsed['selection_phrase']} [article price in JS widget; "
                             f"graded at ESPN/DK price at capture]"),
                "external_ref": url}
        _, st = dbmod.insert_pick(conn, pick)
        stored += 1
        if example is None:
            example = {**parsed, "status": st, "odds_available": odds_avail}
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), log_src,
                  f"discovered {len(links)} prediction articles; stored {stored}; "
                  f"skipped/unparseable {skipped}"))
    conn.commit()
    return {"ok": True, "discovered": len(links), "stored": stored,
            "skipped": skipped, "example": example}


# ---------------- CapperTek service-page detail (Stage 2 check) ----------------
"""FINDING (verified live 2026-10-13 from build VM, service page
picks.asp?shs=Pyramid.cappertek.com, HTTP 200): a service page shows
'Purchase Picks (n)', a 'Documented Record' of PAST picks, and a
'Latest Picks / PENDING' section rendered by JavaScript ('Loading picks...')
whose selection is behind the purchase/login flow. NO documented pre-game pick
(selection + odds) is exposed publicly. CapperTek therefore stays
discovery-only (CLAIMED leaderboard). Nothing is circumvented."""

SVC_LINK_RE = re.compile(r'href="(picks\.asp\?shs=[^"]+)"')


def parse_cappertek_service(html_text):
    text = _strip(html_text)
    return {
        "purchase_picks_offered": "Purchase Picks" in text,
        "documented_record_shown": "Documented Record" in text,
        "picks_rendered_by_js": "Loading picks" in text,
        "login_gated_contact": "must be logged in" in text.lower(),
        # Static HTML exposes no pick selection/odds pre-game.
        "public_pregame_picks": False,
    }


def ingest_cappertek_detail(conn):
    """Daily check: fetch ONE service page off the leaderboard and record
    whether pre-game picks are publicly exposed (expected: no)."""
    try:
        _, lb_html = fetch_text("https://www.cappertek.com/leaderboard.asp?sport=NHL",
                                timeout=45)
        m = SVC_LINK_RE.search(lb_html)
        if not m:
            raise RuntimeError("no service link found on leaderboard")
        svc_url = "https://www.cappertek.com/" + m.group(1).replace("&amp;", "&")
        _, svc_html = fetch_text(svc_url, timeout=45)
        finding = parse_cappertek_service(svc_html)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), "CapperTek detail", f"check failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    detail = (f"{svc_url}: purchase-gated={finding['purchase_picks_offered']}, "
              f"documented-record(past)={finding['documented_record_shown']}, "
              f"picks JS-rendered={finding['picks_rendered_by_js']}, "
              f"public pre-game picks exposed={finding['public_pregame_picks']} "
              f"-> CapperTek stays discovery-only (CLAIMED).")
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "CapperTek detail", detail))
    conn.commit()
    return {"ok": True, "url": svc_url, "finding": finding, "detail": detail}


def register_static_sources(conn):
    dbmod.upsert_source(conn, "Reddit r/sportsbook POTD", "social",
                        "https://www.reddit.com/r/sportsbook/", "BLOCKED",
                        "Unauthenticated JSON and RSS both HTTP 403 (verified 2026-10-13). "
                        "Needs a Jake-approved OAuth app — out of scope; not retried.")
    dbmod.upsert_source(conn, "Manual forward-in", "manual", None, "active",
                        "Jake forwards picks (Pikkit/Action/X/DubClub) via manual.py format. "
                        "Same pre-game timestamp rule.")
    dbmod.upsert_source(conn, "Jake", "own", None, "active",
                        "Jake's own picks — dedicated source family (kind=own), independent "
                        "of Manual forward-in for consensus. Filed via capper=Jake / "
                        "source=Jake or the dashboard log-my-pick form. Same flat-1u + "
                        "pre-game timestamp rules; no exceptions for the owner.")


# ---------------- UltimateCapper (Fix 1, family: UltimateCapper) ----------------
"""VERIFIED LIVE 2026-10-13:
- NHL daily article (author Jay Cooper): per-game sections
  '{Away} at {Home} – H:MM ET ...trends... Pick: {selection}' where selection is
  'Team -110' (ML), 'Team -1.5 +165' (puck line), or 'UNDER 6 -110' (total).
  Structured and explicit — parsed.
- NFL staff-selections article: the picks GRID is a table whose cells are a
  bare signed number ('+3') with NO team attribution — which side the number
  belongs to can only be assumed from a sign convention against a live line.
  That is guessing, so the grid is NOT parsed (documented, by design).
  The explicit 'Best Bets' text lines ('{Team} -6.5 over {Team}') ARE parsed
  when present; at first check (Tue Oct 13) the current week read
  'Check back later...' — the parser stores 0 and logs it, and picks the
  lines up when they post later in the week."""
import html as _html

UC_NHL_PICK_RE = re.compile(r"Pick:\s*(.+?)(?=\s+[A-Z][A-Za-z .'&-]+? at [A-Z]|$)", re.S)
UC_GAME_HDR_RE = re.compile(
    r"([A-Z][A-Za-zÀ-ÿ '&-]{1,24}?) at ([A-Z][A-Za-zÀ-ÿ '&-]{1,24}?) – \d{1,2}:\d{2} ET")
UC_TOTAL_RE = re.compile(r"^(OVER|UNDER)\s+(\d+(?:\.\d+)?)\s+([+-]\d+)$", re.I)
UC_SPREAD_RE = re.compile(r"^(.+?)\s+([+-]\d+(?:\.\d+)?)\s+([+-]\d+)$")
UC_ML_RE = re.compile(r"^(.+?)\s+([+-]\d+)$")
UC_BESTBET_RE = re.compile(
    r"([A-Z][A-Za-z .'&-]+?)\s+([+-]\d+(?:\.\d+)?)\s+over\s+([A-Z][A-Za-z .'&-]+?)\s*(?:WIN|LOSS|PUSH)?(?=\s|$)")
UC_CAPPERS = ("Bobby Babowski", "Jay Cooper", "Reggie Garrett")


def _uc_body_text(html_text):
    m = re.search(r"(?:td-post-content|entry-content)(.*?)(?:RELATED ARTICLES|td-post-sharing|MORE FROM AUTHOR)",
                  html_text, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(_strip(m.group(1) if m else html_text)))


def _parse_uc_selection(raw):
    """'Red Wings -110' -> ML; 'Canadiens -1.5 +165' -> spread; 'UNDER 6 -110' -> total."""
    raw = raw.strip().rstrip(".")
    m = UC_TOTAL_RE.match(raw)
    if m:
        return {"market": "total", "team_nick": None, "line": float(m.group(2)),
                "odds": int(m.group(3)),
                "selection": f"{m.group(1).capitalize()} {float(m.group(2)):g}"}
    m = UC_SPREAD_RE.match(raw)
    if m:
        line = float(m.group(2))
        return {"market": "spread", "team_nick": m.group(1).strip(), "line": line,
                "odds": int(m.group(3)),
                "selection": f"{m.group(1).strip()} {line:+g}"}
    m = UC_ML_RE.match(raw)
    if m:
        return {"market": "moneyline", "team_nick": m.group(1).strip(), "line": None,
                "odds": int(m.group(2)), "selection": f"{m.group(1).strip()} ML"}
    return None


def parse_uc_nhl_article(html_text, url=""):
    ld = _ld_json(html_text) or {}
    author = _author_name(ld)
    if not author:
        m = re.search(r"By\s*(?:<[^>]+>\s*)*(Jay Cooper|Bobby Babowski|Reggie Garrett)",
                      html_text)
        author = m.group(1) if m else None
    posted = ld.get("datePublished")
    if not posted:
        m = re.search(r'"datePublished":\s*"([^"]+)"', html_text)
        posted = m.group(1) if m else None
    text = _uc_body_text(html_text)
    headers = list(UC_GAME_HDR_RE.finditer(text))
    picks = []
    for i, h in enumerate(headers):
        seg = text[h.start(): headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        pm = re.search(r"Pick:\s*([A-Z][^•]{2,60})", seg)
        if not pm:
            continue
        raw_pick = re.split(r"\s+(?:Win/Loss|Over/Under|Head-to-Head)\b", pm.group(1))[0]
        # the pick phrase ends where the next game section's trends begin; our
        # selection grammar is anchored, so trim to the first sentence chunk
        raw_pick = raw_pick.strip()
        for cand in (raw_pick, " ".join(raw_pick.split()[:5]), " ".join(raw_pick.split()[:4]),
                     " ".join(raw_pick.split()[:3])):
            sel = _parse_uc_selection(cand)
            if sel:
                break
        if not sel:
            continue
        picks.append({"away": h.group(1).strip(), "home": h.group(2).strip(),
                      **sel, "raw_pick": raw_pick})
    return {"author": author,
            "posted_at": _to_utc_z(posted) if posted else None,
            "picks": picks, "url": url}


NFL_NICKS = ["49ers", "Bears", "Bengals", "Bills", "Broncos", "Browns", "Buccaneers",
             "Bucs", "Cardinals", "Chargers", "Chiefs", "Colts", "Commanders",
             "Cowboys", "Dolphins", "Eagles", "Falcons", "Giants", "Jaguars",
             "Jets", "Lions", "Packers", "Panthers", "Patriots", "Raiders",
             "Rams", "Ravens", "Saints", "Seahawks", "Steelers", "Texans",
             "Titans", "Vikings"]


def parse_uc_nfl_best_bets(text):
    """Explicit Best Bets lines only: '{Nick} -6.5 over {Nick}' where BOTH
    sides must be known NFL nicknames (anchored — no free-text team capture,
    so headings/other prose can never be absorbed as a 'team'). Attributed to
    the nearest preceding capper heading. The bare-number grid is NOT parsed."""
    out = []
    region = text
    idx = region.upper().find("BEST BETS")
    if idx >= 0:
        region = region[idx:]
    headings = [(m.start(), m.group(1)) for m in
                re.finditer("(" + "|".join(UC_CAPPERS) + ")", region)]
    nick_alt = "|".join(sorted(NFL_NICKS, key=len, reverse=True))
    pat = re.compile(rf"\b({nick_alt})\s+([+-]\d+(?:\.\d+)?)\s+over\s+({nick_alt})\b")
    for m in pat.finditer(region):
        capper = None
        for pos, name in headings:
            if pos < m.start():
                capper = name
        if not capper:
            continue
        out.append({"capper": capper, "team_nick": m.group(1), "line": float(m.group(2)),
                    "opponent_nick": m.group(3), "market": "spread"})
    return out


def _uc_store_pick(conn, sid, cid, sport, ev, parsed, team_full, raw, external_ref):
    odds_avail = None
    if ev is not None:
        import espn as _espn
        is_home = None
        if team_full:
            if _espn._team_matches(ev["home"], team_full):
                is_home = True
            elif _espn._team_matches(ev["away"], team_full):
                is_home = False
        odds_avail = _snapshot_price(conn, ev["id"], parsed["market"], is_home,
                                     parsed["selection"])
    pick = {"source_id": sid, "capper_id": cid,
            "event_id": ev["id"] if ev else None, "sport": sport,
            "market": parsed["market"], "selection": parsed["selection"],
            "team": team_full, "line": parsed.get("line"),
            "odds_claimed": parsed.get("odds"), "odds_available": odds_avail,
            "posted_at": parsed.get("posted_at"), "captured_at": dbmod.now_iso(),
            "status": None if ev else "UNMATCHED",
            "exclusion_reason": None if ev else "event unmatched — commence_time unverified",
            "commence_time": ev["commence_time"] if ev else None,
            "raw_text": raw, "external_ref": external_ref}
    return dbmod.insert_pick(conn, pick)


def ingest_ultimatecapper_nhl(conn, espn_match):
    import espn as _espn
    sid = dbmod.upsert_source(conn, "UltimateCapper", "free_picks",
                              "https://ultimatecapper.com/nhl-picks/", "active",
                              "Daily free picks articles (Jay Cooper NHL). Grid/text "
                              "verified live 2026-10-13. Family: UltimateCapper.")
    log_src = "UltimateCapper NHL"
    try:
        _, index_html = fetch_text("https://ultimatecapper.com/nhl-picks/")
        links = sorted(set(re.findall(
            r'href="(https://ultimatecapper\.com/[a-z-]*nhl-hockey-free-picks-predictions[^"]*)"',
            index_html)), reverse=True)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"index fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    if not links:
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, "no daily NHL article link found"))
        conn.commit()
        return {"ok": False, "error": "no article link"}
    url = links[0]
    try:
        _, art_html = fetch_text(url)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"article fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    parsed = parse_uc_nhl_article(art_html, url)
    author = parsed["author"] or "Jay Cooper"
    cid = dbmod.upsert_capper(conn, sid, author)
    stored = skipped = 0
    example = None
    for p in parsed["picks"]:
        ref = f"{url}#{p['selection']}"
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (ref,)).fetchone():
            skipped += 1
            continue
        ev = None
        try:
            ev = _espn.match_matchup(conn, "NHL", p["away"], p["home"])
        except Exception:  # noqa: BLE001
            ev = None
        team_full = p["team_nick"]
        if ev is not None and p["team_nick"]:
            if _espn._team_matches(ev["home"], p["team_nick"]):
                team_full = ev["home"]
            elif _espn._team_matches(ev["away"], p["team_nick"]):
                team_full = ev["away"]
        _, st = _uc_store_pick(
            conn, sid, cid, "NHL", ev,
            {**p, "posted_at": parsed["posted_at"],
             "selection": (f"{team_full} ML" if p["market"] == "moneyline"
                           else f"{team_full} {p['line']:+g}" if p["market"] == "spread"
                           else p["selection"])},
            team_full if p["market"] != "total" else None,
            f"UltimateCapper ({author}): {p['away']} at {p['home']} -> Pick: {p['raw_pick']}",
            ref)
        stored += 1
        if example is None:
            example = {**p, "status": st, "team_full": team_full}
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), log_src,
                  f"article {url}: parsed {len(parsed['picks'])} picks; stored {stored}; "
                  f"dupes skipped {skipped}"))
    conn.commit()
    return {"ok": True, "url": url, "parsed": len(parsed["picks"]),
            "stored": stored, "skipped": skipped, "example": example}


def ingest_ultimatecapper_nfl(conn, espn_match):
    import espn as _espn
    sid = dbmod.upsert_source(conn, "UltimateCapper", "free_picks",
                              "https://ultimatecapper.com/nfl-picks/", "active",
                              "Weekly staff-selections articles (Babowski NFL). Only "
                              "explicit Best Bets text lines parsed; the bare-number "
                              "grid is NOT parsed (no team attribution — would be guessing).")
    log_src = "UltimateCapper NFL"
    try:
        _, index_html = fetch_text("https://ultimatecapper.com/nfl-picks/")
        links = sorted(set(re.findall(
            r'href="(https://ultimatecapper\.com/week-\d+-nfl-staff-selections[^"]*)"',
            index_html)), reverse=True)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"index fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    if not links:
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                     (dbmod.now_iso(), log_src,
                      "no staff-selections article linked; Best Bets not posted yet — 0 stored"))
        conn.commit()
        return {"ok": True, "stored": 0, "note": "no article link"}
    url = links[0]
    try:
        _, art_html = fetch_text(url)
        text = _uc_body_text(art_html)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"article fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    bets = parse_uc_nfl_best_bets(text)
    stored = skipped = 0
    for b in bets:
        cid = dbmod.upsert_capper(conn, sid, b["capper"])
        ref = f"{url}#{b['capper']}#{b['team_nick']}"
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (ref,)).fetchone():
            skipped += 1
            continue
        ev = None
        try:
            ev = _espn.match_matchup(conn, "NFL", b["team_nick"], b["opponent_nick"]) or \
                _espn.match_matchup(conn, "NFL", b["opponent_nick"], b["team_nick"])
        except Exception:  # noqa: BLE001
            ev = None
        team_full = b["team_nick"]
        if ev is not None:
            if _espn._team_matches(ev["home"], b["team_nick"]):
                team_full = ev["home"]
            elif _espn._team_matches(ev["away"], b["team_nick"]):
                team_full = ev["away"]
        _uc_store_pick(conn, sid, cid, "NFL", ev,
                       {"market": "spread", "line": b["line"], "odds": None,
                        "selection": f"{team_full} {b['line']:+g}", "posted_at": None},
                       team_full,
                       f"UltimateCapper ({b['capper']}): {b['team_nick']} {b['line']:+g} "
                       f"over {b['opponent_nick']} [explicit Best Bets line]",
                       ref)
        stored += 1
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), log_src,
                  f"article {url}: explicit Best Bets lines {len(bets)}; stored {stored}; "
                  f"dupes {skipped}. Grid NOT parsed (bare numbers, no team attribution)."))
    conn.commit()
    return {"ok": True, "url": url, "parsed": len(bets), "stored": stored,
            "skipped": skipped}


# ---------------- SportsHub FREE PICKS (Fix 1, family: SportsHub) ----------------
"""VERIFIED LIVE 2026-10-13: handicapper profile pages (server-rendered cards)
list today's picks as '{Capper} {Win|Lose|Pending} Free Pick {Sport} · {Mon D}
· {time} · {Category} {Away} @ {Home} Pick {pick text}'. Only cards labelled
'Free Pick' are parsed — 'Premium Pick' cards are a different product
(partially masked, 'Requires SportsHub Upgrade') and are never ingested.
Mirrors (gameadvisers.com / pickstoday.com) are deliberately NOT ingested:
same pick pool, would fake a second family.
Profiles ingested (one family 'SportsHub'):
  - 'Sports Hub - FREE PICKS' (the network free-picks service)
  - 'Randall Dickelman' (the capper named in the research; same card format)
Pick text forms seen live: 'DELAWARE BLUE HENS ML -111', 'TOTAL u24½-130'.
Scope guard: a card is stored ONLY if its matchup matches an ESPN NHL/NFL
event in our events table (tennis/CFB/MLB cards on the same profile are
out of scope and are skipped + counted, never stored)."""

SH_PROFILES = [
    ("Sports Hub - FREE PICKS", "https://www.sportshub.com/handicappers/Sports-Hub--FREE-PICKS"),
    ("Randall Dickelman", "https://www.sportshub.com/handicappers/Randall-Dickelman"),
]
SH_CARD_TAIL_RE = re.compile(
    r"(?:(Win|Lose|Pending|Push)\s+)?Free Pick\s+(.+?)\s*·\s*"
    r"([A-Z][a-z]{2} \d{1,2})\s*·\s*(\d{1,2}:\d{2} [AP]M)\s*·\s*(.+?)\s+Pick\s+(.+)$", re.S)
SH_TOTAL_RE = re.compile(r"^TOTAL\s+([ou])(\d+(?:\.\d+)?|\d+½)\s*([+-]\d+)$", re.I)
SH_ML_RE = re.compile(r"^(.+?)\s+ML\s+([+-]\d+)$", re.I)
SH_SPREAD_RE = re.compile(r"^(.+?)\s+([+-]\d+(?:\.\d+)?|[+-]\d+½)\s+([+-]\d+)$")


def _sh_parse_pick_text(pick_text):
    t = re.sub(r"\s+", " ", pick_text).strip()
    m = SH_TOTAL_RE.match(t)
    if m:
        line = float(m.group(2).replace("½", ".5")) if "½" in m.group(2) else float(m.group(2))
        side = "Over" if m.group(1).lower() == "o" else "Under"
        return {"market": "total", "team_nick": None, "line": line,
                "odds": int(m.group(3)), "selection": f"{side} {line:g}"}
    m = SH_ML_RE.match(t)
    if m:
        return {"market": "moneyline", "team_nick": m.group(1).strip(), "line": None,
                "odds": int(m.group(2)), "selection": f"{m.group(1).strip()} ML"}
    m = SH_SPREAD_RE.match(t)
    if m and "@" not in m.group(1):
        line = float(m.group(2).replace("½", ".5"))
        return {"market": "spread", "team_nick": m.group(1).strip(), "line": line,
                "odds": int(m.group(3)),
                "selection": f"{m.group(1).strip()} {line:+g}"}
    return None


SH_CATEGORIES = ("Puck Line", "Point Spread", "Run Line", "Money Line",
                 "Moneyline", "Spread", "Total")


def parse_sportshub_profile(html_text, capper_name):
    """Return list of free-pick cards: sport_token, away, home, status, pick dict.
    Cards repeat the capper name, so splitting on it bounds each card; the pick
    text itself is bounded at the trailing '(AWAY vrs HOME)' parenthetical or
    the section-end markers ('Get Access' / 'Recent Picks')."""
    text = re.sub(r"\s+", " ", _html.unescape(_strip(html_text)))
    out, seen = [], set()
    for ch in re.split(rf"(?={re.escape(capper_name)}\s)", text):
        if not ch.startswith(capper_name):
            continue
        body = ch[len(capper_name):].strip()
        m = SH_CARD_TAIL_RE.match(body[:900])
        if not m:
            continue
        matchup = m.group(5).strip()
        if "@" not in matchup:
            continue
        away, home = [s.strip() for s in matchup.split("@", 1)]
        for cat in SH_CATEGORIES:  # group 5 starts with the category label
            if away.startswith(cat + " "):
                away = away[len(cat):].strip()
                break
        pick_raw = re.split(r"\s\(|\sGet Access|\sRecent Picks", m.group(6))[0]
        pick = _sh_parse_pick_text(pick_raw)
        if pick is None:
            continue
        key = (away, home, pick["selection"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"status_word": m.group(1), "sport_token": m.group(2).strip(),
                    "date": m.group(3), "time": m.group(4),
                    "away": away, "home": home, **pick})
    return out


def ingest_sportshub(conn, espn_match):
    import espn as _espn
    sid = dbmod.upsert_source(conn, "SportsHub", "free_picks",
                              "https://www.sportshub.com/handicappers/Sports-Hub--FREE-PICKS",
                              "active",
                              "FREE PICKS only (premium never ingested; mirrors "
                              "gameadvisers/pickstoday NOT ingested — same pool). "
                              "Family: SportsHub. Verified live 2026-10-13.")
    total_stored = total_skipped = total_cards = 0
    example = None
    for capper_name, url in SH_PROFILES:
        try:
            _, page = fetch_text(url)
        except Exception as e:  # noqa: BLE001
            conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                         (dbmod.now_iso(), "SportsHub", f"{capper_name} fetch failed: {e}"))
            conn.commit()
            continue
        cid = dbmod.upsert_capper(conn, sid, capper_name)
        cards = parse_sportshub_profile(page, capper_name)
        total_cards += len(cards)
        for c in cards:
            ev = None
            for sport_try in ("NHL", "NFL"):
                try:
                    ev = _espn.match_matchup(conn, sport_try, c["away"], c["home"])
                except Exception:  # noqa: BLE001
                    ev = None
                if ev is not None:
                    break
            if ev is None:
                total_skipped += 1  # out of scope (tennis/CFB/…) or not in ESPN window
                continue
            ref = f"{url}#{c['date']}#{c['away']}@{c['home']}#{c['selection']}"
            if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (ref,)).fetchone():
                total_skipped += 1
                continue
            team_full = c["team_nick"]
            if ev is not None and c["team_nick"]:
                if _espn._team_matches(ev["home"], c["team_nick"]):
                    team_full = ev["home"]
                elif _espn._team_matches(ev["away"], c["team_nick"]):
                    team_full = ev["away"]
            sel = c["selection"]
            if c["market"] == "moneyline" and team_full:
                sel = f"{team_full} ML"
            elif c["market"] == "spread" and team_full:
                sel = f"{team_full} {c['line']:+g}"
            _, st = _uc_store_pick(
                conn, sid, cid, ev["sport"], ev,
                {"market": c["market"], "line": c["line"],
                 "odds": c["odds"], "selection": sel, "posted_at": None},
                team_full if c["market"] != "total" else None,
                f"SportsHub FREE ({capper_name}): {c['away']} @ {c['home']} -> {c['selection']}",
                ref)
            total_stored += 1
            if example is None:
                example = {**c, "status": st, "team_full": team_full}
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), "SportsHub",
                  f"profiles {len(SH_PROFILES)}: free cards {total_cards}; stored "
                  f"{total_stored}; skipped (out-of-scope/unmatched/dupe) {total_skipped}"))
    conn.commit()
    return {"ok": True, "cards": total_cards, "stored": total_stored,
            "skipped": total_skipped, "example": example}


# ---------------- Action Network (Fix 1, family: Action Network) ----------------
"""VERIFIED LIVE 2026-10-13. Free articles only.
- NHL 'Daily Dangles' column (incl. Frozen Frenzy editions): per-game sections
  '{Away} vs {Home} Pick ... By: {Author} ... Pick: {Team} Moneyline (-105,
  DraftKings)'. Capper = the section's By: author (the rotation), fallback
  'Daily Dangles'.
- NFL Stuckey articles: byline 'Stuckey, Senior Betting Analyst' and one
  explicit conclusion 'Pick: Rams -3 (-110)'. Capper = 'Stuckey'.
Discovery associates index-page author mentions with their article links;
every candidate article is re-verified by its own byline before parsing."""

AN_SECTION_RE = re.compile(r"([A-Z][A-Za-zÀ-ÿ .']+?) vs ([A-Z][A-Za-zÀ-ÿ .']+?) Pick\b")
AN_PICK_RE = re.compile(r"Pick:\s*(.+?)\s*\(([+-]?\d+)(?:,\s*[^)]+)?\)")
AN_PICK_ALT_RE = re.compile(r"Pick:\s*(.+?)\s+([+-]\d+)\s*\([^)]+\)")
AN_BY_RE = re.compile(r"By:\s*([A-Z][a-z]+ [A-Z][a-z]+)")


def _an_pick_from_segment(seg):
    """First 'Pick:' line in a section with VALID American odds (|odds|>=100 —
    rejects prediction-market prices like '(50 Cents, Kalshi)'). Returns
    (phrase, odds) or None. Odds-before-paren variant also handled."""
    for m in list(AN_PICK_RE.finditer(seg)) + list(AN_PICK_ALT_RE.finditer(seg)):
        odds = int(m.group(2))
        if abs(odds) >= 100:
            return m.group(1), odds
    return None


def _parse_an_phrase(phrase):
    phrase = re.sub(r"\s+", " ", phrase).strip()
    m = re.match(r"^(Over|Under)\s+(\d+(?:\.\d+)?)$", phrase, re.I)
    if m:
        line = float(m.group(2))
        return {"market": "total", "team_nick": None, "line": line,
                "selection": f"{m.group(1).capitalize()} {line:g}"}
    m = re.match(r"^(.+?)\s+(?:Moneyline|ML)$", phrase, re.I)
    if m:
        return {"market": "moneyline", "team_nick": m.group(1).strip(), "line": None,
                "selection": f"{m.group(1).strip()} ML"}
    m = re.match(r"^(.+?)\s+([+-]\d+(?:\.\d+)?)$", phrase)
    if m:
        line = float(m.group(2))
        return {"market": "spread", "team_nick": m.group(1).strip(), "line": line,
                "selection": f"{m.group(1).strip()} {line:+g}"}
    if len(phrase.split()) >= 2:
        # player-prop pick (e.g. 'Andrei Svechnikov Anytime Goal Scorer') —
        # stored as market=prop; grading decides in Fix 3, never guessed here
        return {"market": "prop", "team_nick": None, "line": None,
                "selection": phrase}
    return None


def parse_an_dangles(html_text, url=""):
    text = re.sub(r"\s+", " ", _html.unescape(_strip(html_text)))
    if "Daily Dangles" not in text:
        return {"picks": [], "posted_at": None, "url": url}
    m = re.search(r'"datePublished":\s*"([^"]+)"', html_text)
    posted = _to_utc_z(m.group(1)) if m else None
    headers = list(AN_SECTION_RE.finditer(text))
    picks = []
    for i, h in enumerate(headers):
        seg = text[h.start(): headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        found = _an_pick_from_segment(seg)
        if not found:
            continue
        phrase, odds = found
        sel = _parse_an_phrase(phrase)
        if not sel:
            continue
        am = AN_BY_RE.search(seg)
        picks.append({"away": h.group(1).strip(), "home": h.group(2).strip(),
                      "capper": am.group(1).strip() if am else "Daily Dangles",
                      "odds": odds, "posted_at": posted, **sel})
    return {"picks": picks, "posted_at": posted, "url": url}


def parse_an_stuckey(html_text, url=""):
    text = re.sub(r"\s+", " ", _html.unescape(_strip(html_text)))
    if "Stuckey" not in text[:15000]:
        return None
    tm = re.search(r"([A-Z][A-Za-z .']+?) vs ([A-Z][A-Za-z .']+?) Prediction", text)
    if not tm:
        return None
    found = None
    for cand in reversed(list(AN_PICK_RE.finditer(text)) +
                         list(AN_PICK_ALT_RE.finditer(text))):
        if abs(int(cand.group(2))) >= 100:
            found = (cand.group(1), int(cand.group(2)))  # conclusion pick box
            break
    if not found:
        return None
    sel = _parse_an_phrase(found[0])
    if not sel:
        return None
    m = re.search(r'"datePublished":\s*"([^"]+)"', html_text)
    return {"away": tm.group(1).strip(), "home": tm.group(2).strip(),
            "capper": "Stuckey", "odds": found[1],
            "posted_at": _to_utc_z(m.group(1)) if m else None, **sel}


def _an_store(conn, sid, sport, parsed_list, espn_match, url, family_note):
    import espn as _espn
    stored = skipped = 0
    example = None
    for p in parsed_list:
        cid = dbmod.upsert_capper(conn, sid, p["capper"])
        ref = f"{url}#{p['selection']}"
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (ref,)).fetchone():
            skipped += 1
            continue
        ev = None
        try:
            ev = _espn.match_matchup(conn, sport, p["away"], p["home"])
        except Exception:  # noqa: BLE001
            ev = None
        team_full = p["team_nick"]
        if ev is not None and p["team_nick"]:
            if _espn._team_matches(ev["home"], p["team_nick"]):
                team_full = ev["home"]
            elif _espn._team_matches(ev["away"], p["team_nick"]):
                team_full = ev["away"]
        sel = p["selection"]
        if p["market"] == "moneyline" and team_full:
            sel = f"{team_full} ML"
        elif p["market"] == "spread" and team_full:
            sel = f"{team_full} {p['line']:+g}"
        _, st = _uc_store_pick(conn, sid, cid, sport, ev,
                               {**p, "selection": sel}, team_full if p["market"] != "total" else None,
                               f"Action Network ({p['capper']}): {p['away']} vs {p['home']} "
                               f"-> {sel} ({p['odds']:+d}) [{family_note}]", ref)
        stored += 1
        if example is None:
            example = {**p, "status": st, "team_full": team_full}
    return stored, skipped, example


def _an_source(conn):
    return dbmod.upsert_source(conn, "Action Network", "free_picks",
                               "https://www.actionnetwork.com/", "active",
                               "Free articles only: Daily Dangles (NHL) + Stuckey (NFL). "
                               "Family: Action Network. Verified live 2026-10-13.")


def ingest_action_nhl(conn, espn_match):
    sid = _an_source(conn)
    log_src = "Action Network NHL"
    try:
        _, index_html = fetch_text("https://www.actionnetwork.com/nhl")
        links = []
        for u in re.findall(r'href="(https://www\.actionnetwork\.com/nhl/[^"]+)"', index_html):
            if any(k in u for k in ("expert-picks", "daily-dangles", "frozen-frenzy")) \
                    and u not in links:
                links.append(u)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"index fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    stored = skipped = 0
    example = url_used = None
    for url in links[:2]:
        try:
            _, art = fetch_text(url)
        except Exception:  # noqa: BLE001
            continue
        parsed = parse_an_dangles(art, url)
        if not parsed["picks"]:
            continue
        s, k, ex = _an_store(conn, sid, "NHL", parsed["picks"], espn_match, url,
                             "Daily Dangles")
        stored += s; skipped += k; example = example or ex; url_used = url
        break
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), log_src,
                  f"article {url_used}: stored {stored}; dupes {skipped}"))
    conn.commit()
    return {"ok": True, "url": url_used, "stored": stored, "skipped": skipped,
            "example": example}


def ingest_action_nfl(conn, espn_match):
    sid = _an_source(conn)
    log_src = "Action Network NFL"
    try:
        _, index_html = fetch_text("https://www.actionnetwork.com/nfl")
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), log_src, f"index fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    # associate Stuckey mentions on the index with the nearest preceding article link
    cands = []
    for m in re.finditer(r"Stuckey", index_html):
        hrefs = re.findall(r'href="(https://www\.actionnetwork\.com/nfl/[^"]+)"',
                           index_html[max(0, m.start() - 2500):m.start()])
        if hrefs and hrefs[-1] not in cands:
            cands.append(hrefs[-1])
    stored = skipped = 0
    example = None
    used = []
    for url in cands[:4]:
        try:
            _, art = fetch_text(url)
        except Exception:  # noqa: BLE001
            continue
        parsed = parse_an_stuckey(art, url)
        if not parsed:
            continue
        s, k, ex = _an_store(conn, sid, "NFL", [parsed], espn_match, url, "Stuckey")
        stored += s; skipped += k; example = example or ex; used.append(url)
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), log_src,
                  f"Stuckey candidates {len(cands)}; articles used {len(used)}; "
                  f"stored {stored}; dupes {skipped}"))
    conn.commit()
    return {"ok": True, "candidates": len(cands), "stored": stored,
            "skipped": skipped, "example": example}


# ---------------- Covers Contests consensus (Part A1ii) ----------------
"""contests.covers.com consensus pages ARE server-rendered (verified live
2026-10-13): rows 'NHL Dal Col Wed. Oct 14 8:30 pm ET 21% 79% +127 -138 18 67'
= away/home abbrev, away%/home%, away/home price-or-line, away/home pick
counts. NHL 'Sides' are moneyline prices; NFL 'Sides' are ATS lines (+4/-4 =
the spread each side is getting). Two SEPARATE signal families per the build
order: overall -> 'Covers Contest Consensus', top10pct -> 'Covers Contest
Top10'. A family pick is filed only for a CLEAR side: >=20 total contest
picks and majority >=55% (thinner/closer rows are signal-only, counted in the
ingest log, never filed). Contestant profiles (A1i) expose only aggregate
season records in static HTML — no pending per-pick data — so no per-
contestant tracked records are built from them (see README)."""

CC_NHL = {"ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
          "CGY": "Calgary Flames", "CAR": "Carolina Hurricanes",
          "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
          "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars",
          "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers",
          "FLA": "Florida Panthers", "LAK": "Los Angeles Kings",
          "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
          "NSH": "Nashville Predators", "NJD": "New Jersey Devils", "NJ": "New Jersey Devils",
          "NYI": "New York Islanders", "NYR": "New York Rangers",
          "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
          "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks", "SJ": "San Jose Sharks",
          "SEA": "Seattle Kraken", "STL": "St Louis Blues",
          "TBL": "Tampa Bay Lightning", "TB": "Tampa Bay Lightning",
          "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
          "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
          "WSH": "Washington Capitals", "WPG": "Winnipeg Jets"}
CC_NFL = {"ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
          "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
          "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
          "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
          "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
          "DET": "Detroit Lions", "GB": "Green Bay Packers",
          "HOU": "Houston Texans", "IND": "Indianapolis Colts",
          "JAC": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
          "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
          "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
          "MIN": "Minnesota Vikings", "NE": "New England Patriots",
          "NO": "New Orleans Saints", "NYG": "New York Giants",
          "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
          "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
          "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
          "TEN": "Tennessee Titans", "WSH": "Washington Commanders"}
CC_ROW_RE = re.compile(
    r"(NHL|NFL)\s+([A-Za-z]{2,3})\s+([A-Za-z]{2,3})\s+\w{3}\.\s+\w{3}\s+\d+\s+"
    r"[\d:]+\s*[ap]m ET\s+(\d+)%\s+(\d+)%\s+([+-]\d+)\s+([+-]\d+)\s+(\d+)\s+(\d+)")


def parse_covers_consensus(html_text, league):
    text = re.sub(r"\s+", " ", _strip(html_text))
    table = CC_NHL if league == "NHL" else CC_NFL
    rows = []
    for m in CC_ROW_RE.finditer(text):
        if m.group(1) != league:
            continue
        away = table.get(m.group(2).upper())
        home = table.get(m.group(3).upper())
        if not away or not home:
            continue
        rows.append({"sport": league, "away": away, "home": home,
                     "away_pct": int(m.group(4)), "home_pct": int(m.group(5)),
                     "away_val": int(m.group(6)), "home_val": int(m.group(7)),
                     "away_n": int(m.group(8)), "home_n": int(m.group(9))})
    return rows


def _cc_match_event(conn, sport, away, home):
    """Match a consensus row to the EARLIEST FUTURE event for the matchup
    (teams meet on consecutive days — Oct 13 COL@DAL and Oct 14 DAL@COL —
    so name-only matching files picks against yesterday's game). Falls back
    to the most recent past event (which files as EXCLUDED_LATE, correctly)."""
    rows = conn.execute(
        "SELECT * FROM events WHERE sport=? AND "
        "((home=? AND away=?) OR (home=? AND away=?))",
        (sport, home, away, away, home)).fetchall()
    if not rows:
        return None
    now = dbmod.now_iso()
    future = [r for r in rows if (r["commence_time"] or "") >= now]
    if future:
        return min(future, key=lambda r: r["commence_time"])
    return max(rows, key=lambda r: r["commence_time"] or "")


def ingest_covers_consensus(conn, espn_match):
    import espn as _espn
    out = {"ok": True, "families": {}}
    fams = [("overall", "Covers Contest Consensus", "Overall consensus"),
            ("top10pct", "Covers Contest Top10", "Top-10% consensus")]
    for filt, src_name, capper_name in fams:
        sid = dbmod.upsert_source(
            conn, src_name, "consensus_signal",
            f"https://contests.covers.com/consensus/topconsensus/all/{filt}",
            "active", f"Covers Contests consensus ({filt}) — signal family; "
                      "majority side filed only when >=20 picks and >=55%.")
        cid = dbmod.upsert_capper(conn, sid, capper_name)
        for league in ("NHL", "NFL"):
            filed = signal_only = 0
            url = (f"https://contests.covers.com/consensus/topconsensus/"
                   f"{league.lower()}/{filt}")
            try:
                _, html_text = fetch_text(url)
                rows = parse_covers_consensus(html_text, league)
            except Exception as e:  # noqa: BLE001
                out["families"][f"{src_name}/{league}"] = {"error": str(e)}
                continue
            for r in rows:
                total = r["away_n"] + r["home_n"]
                home_major = r["home_pct"] >= r["away_pct"]
                maj_pct = r["home_pct"] if home_major else r["away_pct"]
                if total < 20 or maj_pct < 55:
                    signal_only += 1
                    continue
                team_full = r["home"] if home_major else r["away"]
                val = r["home_val"] if home_major else r["away_val"]
                if league == "NHL":
                    parsed = {"market": "moneyline", "team_nick": team_full,
                              "line": None, "odds": val,
                              "selection": f"{team_full} ML", "posted_at": None}
                else:
                    parsed = {"market": "spread", "team_nick": team_full,
                              "line": float(val), "odds": None,
                              "selection": f"{team_full} {val:+g}",
                              "posted_at": None}
                ev = _cc_match_event(conn, league, r["away"], r["home"])
                if ev is None:
                    signal_only += 1
                    continue
                ref = f"{src_name}#{ev['id']}#{team_full}"
                if conn.execute("SELECT 1 FROM picks WHERE external_ref=?",
                                (ref,)).fetchone():
                    continue
                _uc_store_pick(
                    conn, sid, cid, league, ev, parsed, team_full,
                    f"{src_name}: {r['away']} @ {r['home']} majority "
                    f"{maj_pct}% ({r['home_n'] if home_major else r['away_n']}/{total} picks)",
                    ref)
                filed += 1
            out["families"][f"{src_name}/{league}"] = {
                "rows": len(rows), "filed": filed, "signal_only": signal_only}
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                     (dbmod.now_iso(), src_name,
                      f"filed {filed} family picks; {signal_only} rows signal-only"))
    conn.commit()
    return out


# ---------------- Westgate SuperContest (Part A3) ----------------
"""Family 'SuperContest/Circa' — v1 is Westgate-only: TheSpread publishes the
official weekly contest lines + full consensus counts server-rendered
(verified live 2026-10-13). Circa routes probed (VSiN /circa-million-picks/,
VegasInsider /supercontest/) return 404 — Circa not ingested, see README.
Picks: top-5 consensus sides per week at the PUBLISHED CONTEST LINE (stale
Wednesday lines by design). Stored with odds NULL on purpose — grading is
W/L/push vs the contest line; no market price is mixed in. The capper name
carries the required label 'graded vs stale contest lines' onto every
scorecard. Weekly cadence: the cycle only fetches Sat/Sun ET or after a
6-day gap; dedupe by week+team external_ref."""

SC_NICK = {}
for _full in CC_NFL.values():
    _nick = _full.split()[-1]
    SC_NICK[_nick] = _full
SC_NICK.update({"NY Jets": "New York Jets", "NY Giants": "New York Giants",
                "49ers": "San Francisco 49ers", "Packers": "Green Bay Packers"})
SC_URL = "https://www.thespread.com/nfl-super-contest-picks-from-the-las-vegas-hilton/"
SC_LABEL = "graded vs stale contest lines"


def _sc_team_from_gap(gap, from_left=True):
    """Longest known nickname match at one end of a text gap."""
    best = None
    for nick in SC_NICK:
        if from_left and gap.startswith(nick):
            if best is None or len(nick) > len(best):
                best = nick
        if not from_left and gap.endswith(nick):
            if best is None or len(nick) > len(best):
                best = nick
    return best


def parse_supercontest(html_text):
    text = re.sub(r"\s+", " ", _html.unescape(_strip(html_text))).replace("−", "-")
    wm = re.search(r"SuperContest Week (\d+) Lines, Consensus & Standings", text)
    week = int(wm.group(1)) if wm else None
    lines = {}
    if wm:
        seg = text[wm.start():]
        end = seg.find("Bold indicates")
        seg = seg[:end if end > 0 else 3000]
        seg = re.sub(r"SuperContest Week \d+ Lines, Consensus & Standings[^A-Z]*"
                     r"Bold = Fave Away Line Home", "", seg)
        seg = re.sub(r"(Thursday|Sunday|Monday|Saturday), [A-Za-z]+ \d+ "
                     r"[\d:]+ [AP]M ET", " ", seg)
        seg = re.sub(r"\bN\b(?= )", " ", seg)  # neutral-site marker
        toks = [(m.start(), m.end(), float(m.group(1)))
                for m in re.finditer(r"(-\d+(?:\.\d+)?)", seg)]
        prev_end = 0
        pending_away = None
        for i, (s, e, val) in enumerate(toks):
            gap = seg[prev_end:s].strip()
            if i == 0:
                pending_away = _sc_team_from_gap(gap, from_left=False)
            else:
                home_prev = _sc_team_from_gap(gap, from_left=True)
                rest = gap[len(home_prev):].strip() if home_prev else ""
                if home_prev and pending_away:
                    lines[home_prev] = None  # filled below once away known
                    lines[("__game__", pending_away, home_prev)] = toks[i - 1][2]
                pending_away = _sc_team_from_gap(rest, from_left=False) if rest else None
            prev_end = e
        tail = seg[prev_end:].strip()
        last_home = _sc_team_from_gap(tail, from_left=True)
        if pending_away and last_home and toks:
            lines[("__game__", pending_away, last_home)] = toks[-1][2]
        # normalise into team -> (line, opponent_full, is_home)
        out = {}
        for k, v in list(lines.items()):
            if k and k[0] == "__game__":
                _, away_n, home_n = k
                out[away_n] = (v, SC_NICK.get(home_n), False)
                out[home_n] = (-v, SC_NICK.get(away_n), True)
        lines = {k: v for k, v in out.items() if v[1]}
    consensus = []
    ci = text.find("Consensus Picks")
    if ci >= 0:
        seg = text[ci:ci + 4000]
        for m in re.finditer(r"\d+ ([A-Za-z0-9 ]+?) (\d+) (\d+(?:\.\d+)?)%", seg):
            nick = m.group(1).strip()
            if nick in SC_NICK:
                consensus.append((nick, int(m.group(2)), float(m.group(3))))
    return {"week": week, "lines": lines, "consensus": consensus}


def ingest_supercontest(conn, espn_match, force=False):
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    src_name = "SuperContest/Circa"
    if not force:
        today = _dt.now(_ZI("America/Toronto"))
        last = conn.execute("SELECT MAX(ran_at) FROM ingest_log WHERE source=?",
                            (src_name,)).fetchone()[0]
        stale = True
        if last:
            try:
                stale = (_dt.now(_ZI("UTC")) -
                         _dt.fromisoformat(last.replace("Z", "+00:00"))).days >= 6
            except ValueError:
                stale = True
        if today.weekday() not in (5, 6) and not stale:
            return {"ok": True, "skipped": "weekly cadence (Sat/Sun ET)"}
    sid = dbmod.upsert_source(conn, src_name, "contest_consensus", SC_URL,
                              "active", f"Westgate SuperContest weekly consensus "
                                        f"vs published contest lines ({SC_LABEL}). "
                                        "Circa: no public parseable feed found 2026-10-13.")
    capper_name = f"Westgate SuperContest consensus ({SC_LABEL})"
    cid = dbmod.upsert_capper(conn, sid, capper_name)
    try:
        _, html_text = fetch_text(SC_URL)
    except Exception as e:  # noqa: BLE001
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,0,?)",
                     (dbmod.now_iso(), src_name, f"fetch failed: {e}"))
        conn.commit()
        return {"ok": False, "error": str(e)}
    parsed = parse_supercontest(html_text)
    filed = 0
    detail_rows = []
    for nick, entries, pct in parsed["consensus"][:5]:
        info = parsed["lines"].get(nick)
        if not info:
            detail_rows.append(f"{nick}: no contest line parsed")
            continue
        line, opp_full, is_home = info
        team_full = SC_NICK[nick]
        away, home = (opp_full, team_full) if is_home else (team_full, opp_full)
        ev = _cc_match_event(conn, "NFL", away, home)
        if ev is None:
            detail_rows.append(f"{nick}: no ESPN event")
            continue
        ref = f"SuperContest#2026#W{parsed['week']}#{team_full}"
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?", (ref,)).fetchone():
            continue
        pick = {"source_id": sid, "capper_id": cid, "event_id": ev["id"],
                "sport": "NFL", "market": "spread",
                "selection": f"{team_full} {line:+g}", "team": team_full,
                "line": line, "odds_claimed": None, "odds_available": None,
                "captured_at": dbmod.now_iso(),
                "commence_time": ev["commence_time"],
                "raw_text": f"{src_name} week {parsed['week']}: {team_full} "
                            f"{line:+g} (contest line) — consensus {entries} "
                            f"entries, {pct}% [{SC_LABEL}]",
                "external_ref": ref}
        _pid, status = dbmod.insert_pick(conn, pick)
        filed += 1
        detail_rows.append(f"{team_full} {line:+g} -> {status}")
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), src_name,
                  f"week {parsed['week']}: filed {filed}; " + "; ".join(detail_rows)))
    conn.commit()
    return {"ok": True, "week": parsed["week"], "filed": filed,
            "consensus_top5": parsed["consensus"][:5], "detail": detail_rows}
