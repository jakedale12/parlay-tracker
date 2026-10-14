"""Part B: Reddit r/sportsbook POTD bridge via public RSS (Atom).

Evidence (2026-10-13): RSS is dead from the build VM (403) but the parent
verified HTTP 200 from the botserver with real POTD threads in hot/.rss;
a container retest minutes later hit 429 — Reddit rate-limits bursts, so
this bridge is deliberately polite:
  * runs only inside the tracker container (botserver egress),
  * at most ONE listing fetch per 30 min and ONE comment fetch per cycle,
  * descriptive UA, and on HTTP 429 it backs off for 2 hours (ingest_log
    state), never retries in a loop.
search.rss is NOT used (429 even when paced — parent-verified).
RSS itself dies 2026-11-13 (Reddit announcement); the OAuth route is
prepared separately in REDDIT-API-REQUEST.md. Posters' self-stated
records/units in POTD comments are CLAIMED metadata — kept in raw_text
only, never treated as a record. Family: 'Reddit POTD', capper = poster."""
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import netutil

UA = {"User-Agent": "parlay-tracker/1.0 (personal research; POTD RSS bridge; "
                   "single polite fetch per cycle)"}
ATOM = {"a": "http://www.w3.org/2005/Atom"}
SOURCE_NAME = "Reddit POTD"
LISTING_URL = "https://www.reddit.com/r/sportsbook/hot/.rss?limit=25"


def _fetch(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with netutil.open_url(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return f"ERR:{e}", ""


def parse_listing(atom_xml):
    """[{title, post_id, link}] for entries in the subreddit listing feed."""
    out = []
    root = ET.fromstring(atom_xml)
    for entry in root.findall("a:entry", ATOM):
        title = (entry.findtext("a:title", default="", namespaces=ATOM) or "")
        link = ""
        for lnk in entry.findall("a:link", ATOM):
            if lnk.get("rel") in (None, "alternate"):
                link = lnk.get("href") or ""
        m = re.search(r"/comments/([a-z0-9]+)/", link)
        if m:
            out.append({"title": title.strip(), "post_id": m.group(1),
                        "link": link})
    return out


def find_potd_thread(entries, now_utc):
    """Today's (ET) 'Pick of the Day' thread; falls back to the most recent
    POTD thread in the listing (weekend threads stay hot for a while)."""
    potd = [e for e in entries if "pick of the day" in e["title"].lower()]
    if not potd:
        return None
    from zoneinfo import ZoneInfo
    today = now_utc.astimezone(ZoneInfo("America/Toronto"))
    for e in potd:
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", e["title"])
        if m and int(m.group(1)) == today.month and int(m.group(2)) == today.day:
            return e
    return potd[0]


def parse_comments(atom_xml):
    """[{author, text, updated}] comment entries of a thread feed. The Atom
    feed is FLAT — it carries no parent/depth field, so replies are mixed
    in with top-level comments. POTD submissions are told apart from
    replies/chatter by the thread's own rules (see is_potd_submission):
    a submission comment must carry the poster's record line AND pick."""
    import html as _html
    out = []
    root = ET.fromstring(atom_xml)
    for entry in root.findall("a:entry", ATOM):
        author = entry.findtext("a:author/a:name", default="",
                                namespaces=ATOM)
        content = entry.findtext("a:content", default="", namespaces=ATOM)
        text = re.sub(r"<[^>]+>", " ", content)
        text = _html.unescape(re.sub(r"\s+", " ", text)).strip()
        out.append({"author": author.replace("/u/", "").strip(),
                    "text": text,
                    "updated": entry.findtext("a:updated", default="",
                                              namespaces=ATOM)})
    return out


# ---------------------------------------------------------------------------
# POTD comment parsing — widened 2026-10-14 from REAL thread samples
# (tests/fixtures/potd_comments_20261014.xml). Formats actually seen:
#   markers:  "Pick:", "Today's Pick:", "Today's POTD:", "POTD:" — always
#             with a "Last Pick:"/“Last POTD:” recap segment to EXCLUDE.
#   odds:     decimal is dominant — "@ 1.95", "2.15 odds", "($1.91)",
#             "(1.60)"; American in parens "(-106)", "-105 @ DK",
#             slash pair "-120/1.83", separate field "Odds: -150".
#   bets:     team ML ("Team ML", "Team to win @ 1.63"), spreads
#             ("Team +1.5 at 1.95"), totals ("Over 3.5 goals @ 1.90"),
#             player props ("Blake Snell O5.5 strikeouts Odds: -150",
#             "Lane Hutson o1.5 SOG -120/1.83").
#   NOT single bets we can represent (identifiable -> "unsupported",
#   counted, never stored): 2-leg parlays/combined odds, BTTS, double
#   chance, Asian handicap, corners.
# A bet still needs team/player + market + (line OR odds).
# ---------------------------------------------------------------------------

_RECORD_RE = re.compile(
    r"(POTD\s+Record|Overall\s+record|Record\s*:|Net\s+Units)", re.I)
_MARKER_RE = re.compile(
    r"(Today[’']s\s+(?:POTD|Pick)|Pick\s+of\s+the\s+Day|POTD|Pick)\s*[:\-–—]",
    re.I)
_SECTION_CUT_RE = re.compile(
    r"(Write\s?-?\s?up|Recap|Reasoning|Analysis)\s*[:\-]", re.I)
_UNSUPPORTED_RE = re.compile(
    r"(BTTS|both teams to score|double chance|asian handicap|corners|"
    r"combined odds|2\s?-leg|two-leg|parlay)", re.I)
_PROP_STATS = (r"strikeouts|shots on goal|SOG|shots|points|goals|assists|"
               r"rebounds|saves|headshots|kills|passing yards|rushing yards|"
               r"receiving yards")
_PROP_RE = re.compile(
    r"([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,2})\s+"
    r"(Over|Under|O|U)\s?(\d+(?:\.\d+)?)\s*(" + _PROP_STATS + r"|BB)\b", re.I)
_TOTAL_RE = re.compile(
    r"\b(Over|Under|O|U)\s?(\d+(?:\.\d+)?)(?!\.)\s*(goals|points|runs)?\b",
    re.I)
_STAT_NORM = {"sog": "Shots on Goal", "shots": "Shots",
              "shots on goal": "Shots on Goal", "bb": "BB"}


def decimal_to_american(d):
    """Decimal odds -> American int (1.95 -> -105, 2.15 -> +115)."""
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    if d <= 1.01 or d >= 20:
        return None
    if d >= 2.0:
        return int(round((d - 1.0) * 100))
    return -int(round(100.0 / (d - 1.0)))


def _find_odds(seg):
    """First plausible price in a pick segment, in the formats posters
    actually use. Returns an American int or None."""
    m = re.search(r"\(([+-]\d{3,4})\)", seg)                    # (-106)
    if m:
        return int(m.group(1))
    m = re.search(r"\(\$?(\d+\.\d{1,3})\)", seg)                # ($1.91) (1.60)
    if m:
        return decimal_to_american(m.group(1))
    m = re.search(r"@\s*(\d+\.\d{1,3})\b", seg)                 # @ 1.95
    if m:
        return decimal_to_american(m.group(1))
    m = re.search(r"([+-]\d{3,4})\s*@", seg)                    # -105 @ DK
    if m:
        return int(m.group(1))
    m = re.search(r"([+-]\d{3,4})\s*/\s*\d+\.\d+", seg)         # -120/1.83
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+\.\d{1,3})\s*(?:decimal\s+)?odds\b", seg, re.I)
    if m:                                                       # 2.15 odds
        return decimal_to_american(m.group(1))
    m = re.search(r"\bat\s+(\d+\.\d{1,3})\b", seg)              # at 1.95
    if m:
        return decimal_to_american(m.group(1))
    m = re.search(r"(?<![\d.])([+-]\d{3,4})(?![\d.])", seg)    # bare -152
    if m:
        return int(m.group(1))
    return None


def _find_team_in_text(seg, team_names):
    """(full_name, start, span_len) of a team mention in seg: longest
    full name first, else a distinctive nickname (last word, >=4 chars)
    as a whole word. The span matters — a nickname match must not be
    located by searching for the full name (that throws, and did)."""
    low = seg.lower()
    for name in sorted(team_names, key=len, reverse=True):
        i = low.find(name.lower())
        if i >= 0:
            return name, i, len(name)
    for name in sorted(team_names, key=len, reverse=True):
        nick = name.split()[-1]
        if len(nick) >= 4:
            m = re.search(r'\b' + re.escape(nick.lower()) + r'\b', low)
            if m:
                return name, m.start(), len(nick)
    return None


def _pick_segments(text):
    """Candidate pick segments: text after each Pick/POTD marker, minus
    'Last Pick' recaps and write-up sections. Last candidate wins ties
    (today's pick is stated after the recap of the previous one)."""
    segs = []
    for m in _MARKER_RE.finditer(text):
        before = text[max(0, m.start() - 18):m.start()].lower()
        if "last" in before:
            continue
        seg = text[m.end():m.end() + 340]
        cut = _SECTION_CUT_RE.search(seg)
        if cut:
            seg = seg[:cut.start()]
        seg = re.split(r"\shttps?://", seg)[0].strip(" |–—-")
        if seg:
            segs.append(seg)
    return segs


def is_potd_submission(text):
    """Thread rules require a submission comment to carry the poster's
    record AND their pick. Replies/chatter don't — this is the practical
    top-level-submission test for the flat Atom feed."""
    if not _RECORD_RE.search(text):
        return False
    return bool(_MARKER_RE.search(text)) or bool(
        re.search(r"(to win|ML|@\s*\d+\.\d+)", text, re.I))


def _parse_segment(seg, team_names):
    """-> ('bet', parsed) | ('unsupported', reason) | ('unparsed', None)"""
    low = seg.lower()
    if _UNSUPPORTED_RE.search(seg):
        return "unsupported", _UNSUPPORTED_RE.search(seg).group(1)
    if len(_TOTAL_RE.findall(seg)) >= 2 or \
            len(re.findall(r"\bto win\b", low)) >= 2 or \
            (len(re.findall(r"\bwin\b", low)) >= 2 and "+" in seg):
        return "unsupported", "multi-leg / parlay"
    # player prop: Name O/U line stat — but not when the "name" is a team
    # in a matchup ("Borussia Dortmund Over 3.5 goals" is a game total).
    m = _PROP_RE.search(seg)
    if m and (re.search(r"\bvs\.?\b", seg[:m.start()], re.I) or
              m.group(1).lower().startswith("vs")):
        m = None
    if m:
        ou = {"o": "Over", "u": "Under"}.get(m.group(2).lower(),
                                             m.group(2).capitalize())
        stat = _STAT_NORM.get(m.group(4).lower(), m.group(4).title())
        line = float(m.group(3))
        return "bet", {"market": "prop", "team_nick": None,
                       "team_raw": None, "line": line,
                       "selection": f"{m.group(1).strip()} {ou} {line:g} {stat}",
                       "odds": _find_odds(seg[m.end():] + " " +
                                          seg[max(0, m.start() - 40):m.start()])}
    # game total
    m = _TOTAL_RE.search(seg)
    if m:
        ou = {"o": "Over", "u": "Under"}.get(m.group(1).lower(),
                                             m.group(1).capitalize())
        line = float(m.group(2))
        return "bet", {"market": "total", "team_nick": None,
                       "team_raw": None, "line": line,
                       "selection": f"{ou} {line:g}",
                       "odds": _find_odds(seg[m.end():])}
    # team bet: known event team named in the segment
    found = _find_team_in_text(seg, team_names)
    if found:
        team, tstart, tlen = found
        rest = seg[tstart + tlen:tstart + tlen + 60]
        line_m = re.match(r"\s*([+-]\d+(?:\.\d+)?)\b", rest)
        odds = _find_odds(seg)
        if line_m:
            line = float(line_m.group(1))
            return "bet", {"market": "spread", "team_nick": team,
                           "team_raw": team, "line": line,
                           "selection": f"{team} {line:+g}", "odds": odds}
        if re.search(r"\bML\b|\bto win\b", rest, re.I) and odds is not None:
            return "bet", {"market": "moneyline", "team_nick": team,
                           "team_raw": team, "line": None,
                           "selection": f"{team} ML", "odds": odds}
        return "unparsed", None  # team named but no line and no price
    # team bet: raw team phrase (other sports — stored UNMATCHED)
    m = re.search(r"([A-Z][A-Za-zÀ-ÿ .']{2,30}?)\s+([+-]\d+(?:\.\d+)?)\b", seg)
    if m:
        line = float(m.group(2))
        return "bet", {"market": "spread", "team_nick": None,
                       "team_raw": m.group(1).strip(), "line": line,
                       "selection": f"{m.group(1).strip()} {line:+g}",
                       "odds": _find_odds(seg[m.end():] + " " + seg[:m.start()])}
    m = re.search(r"([A-Z][A-Za-zÀ-ÿ .']{2,30}?)\s+(?:ML|to win)\b", seg, re.I)
    if m:
        odds = _find_odds(seg)
        if odds is None:
            return "unparsed", None  # ML with no line and no price
        return "bet", {"market": "moneyline", "team_nick": None,
                       "team_raw": m.group(1).strip(), "line": None,
                       "selection": f"{m.group(1).strip()} ML",
                       "odds": odds}
    return "unparsed", None


def classify_potd_comment(text, team_names):
    """-> ('bet', parsed) | ('unsupported', reason) | ('unparsed', None).
    Tries each pick-marker segment (last first); falls back to a whole-text
    scan for header-style posts (record + 'to win @' legs, no Pick: line)."""
    segs = _pick_segments(text)
    fallback = None
    for seg in reversed(segs):
        kind, val = _parse_segment(seg, team_names)
        if kind == "bet":
            return "bet", val
        if kind == "unsupported" and fallback is None:
            fallback = ("unsupported", val)
    if fallback:
        return fallback
    if not segs:
        head = text[:500]
        if len(re.findall(r"to win\s*@\s*\d+\.\d+", head, re.I)) >= 2 or \
                _UNSUPPORTED_RE.search(head):
            return "unsupported", "multi-leg / unsupported market (no Pick: line)"
    return "unparsed", None


def parse_potd_comment(text, team_names):
    """Parsed-pick dict for a POTD comment, or None. Needs an explicit
    Pick/POTD marker segment carrying an identifiable bet (team/player +
    market + line or odds) — see classify_potd_comment for the widened
    format set and the unsupported-market split."""
    kind, val = classify_potd_comment(text, team_names)
    return val if kind == "bet" else None


def _match_team(fragment, team_names):
    f = fragment.strip().lower()
    for name in team_names:
        n = name.lower()
        if f == n or f == n.split()[-1] or n.endswith(" " + f) or f in n:
            return name
    return None


def _gate(conn, dbmod):
    """(allowed, reason): >=30 min between listing fetches; 2h backoff
    after a 429 (recorded as an ingest_log row with ok=0, detail 'http 429')."""
    row = conn.execute("SELECT ran_at, ok, detail FROM ingest_log "
                       "WHERE source=? ORDER BY id DESC LIMIT 1",
                       (SOURCE_NAME,)).fetchone()
    if not row:
        return True, ""
    try:
        last = datetime.fromisoformat(row["ran_at"].replace("Z", "+00:00"))
    except ValueError:
        return True, ""
    age = datetime.now(timezone.utc) - last
    if row["ok"] == 0 and "429" in (row["detail"] or "") and age < timedelta(hours=2):
        return False, f"429 backoff ({age.seconds // 60}m elapsed)"
    if age < timedelta(minutes=30):
        return False, f"polite pacing ({age.seconds // 60}m since last fetch)"
    return True, ""


def ingest_reddit_potd(conn, dbmod, espn_match, force=False):
    # Two-phase by design: Reddit's limiter passes roughly one request per
    # window from this host (listing 200, immediate follow-up 429 — observed
    # live 2026-10-14). Phase 1 (listing) logs the found thread; phase 2
    # (comments for that thread) runs on a LATER cycle. Never both at once.
    last = conn.execute("SELECT ran_at, detail FROM ingest_log WHERE source=? "
                        "ORDER BY id DESC LIMIT 1", (SOURCE_NAME,)).fetchone()
    pending = None
    if last and (last["detail"] or "").startswith("thread_found "):
        parts = last["detail"].split(" ", 2)
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(
                last["ran_at"].replace("Z", "+00:00"))
        except ValueError:
            age = timedelta(days=1)
        if age < timedelta(hours=3):
            pending = {"post_id": parts[1], "title": parts[2] if len(parts) > 2 else ""}
    if pending is None:
        allowed, why = (True, "") if force else _gate(conn, dbmod)
        if not allowed:
            return {"ok": True, "skipped": why}
        status, listing = _fetch(LISTING_URL)
        if status != 200:
            conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) "
                         "VALUES(?,?,0,?)", (dbmod.now_iso(), SOURCE_NAME,
                                             f"listing fetch http {status}"))
            conn.commit()
            return {"ok": False, "error": f"listing http {status}"}
        thread = find_potd_thread(parse_listing(listing),
                                  datetime.now(timezone.utc))
        if not thread:
            conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) "
                         "VALUES(?,?,1,?)", (dbmod.now_iso(), SOURCE_NAME,
                                             "no POTD thread in hot listing"))
            conn.commit()
            return {"ok": True, "thread": None}
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) "
                     "VALUES(?,?,1,?)", (dbmod.now_iso(), SOURCE_NAME,
                                         f"thread_found {thread['post_id']} "
                                         f"{thread['title'][:60]}"))
        conn.commit()
        return {"ok": True, "phase": "listing", "thread": thread["title"],
                "note": "comments fetched on a later cycle (rate-limit pacing)"}
    thread = pending
    status, feed = _fetch(
        f"https://www.reddit.com/r/sportsbook/comments/{thread['post_id']}"
        f"/.rss?limit=100")
    if status != 200:
        conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) "
                     "VALUES(?,?,0,?)", (dbmod.now_iso(), SOURCE_NAME,
                                         f"comment fetch http {status}"))
        conn.commit()
        return {"ok": False, "error": f"comments http {status}"}
    sid = dbmod.upsert_source(conn, SOURCE_NAME, "forum",
                              "https://www.reddit.com/r/sportsbook/",
                              "active", "POTD thread via RSS bridge (RSS dies "
                                        "2026-11-13); poster = capper; "
                                        "self-stated records are CLAIMED only.")
    team_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT home FROM events UNION SELECT DISTINCT away FROM events")]
    comments = parse_comments(feed)
    stored = unmatched = unsupported = unparsed = chatter = dupes = 0
    submissions = 0
    sides = {}
    import sources as _sources

    def _earliest_future(rows):
        now = dbmod.now_iso()
        future = [r for r in rows if (r["commence_time"] or "") >= now]
        return future[-1] if future else (rows[0] if rows else None)

    for c in comments:
        if not c["author"] or c["author"] in ("AutoModerator", "sbpotdbot"):
            continue  # thread bot posts the rules/template — not a capper
        if not is_potd_submission(c["text"]):
            chatter += 1  # replies/discussion in the flat feed, not picks
            continue
        submissions += 1
        kind, parsed = classify_potd_comment(c["text"], team_names)
        if kind == "unsupported":
            unsupported += 1  # identifiable pick, market we can't grade
            continue
        if kind != "bet":
            unparsed += 1  # a submission whose bet we could not extract
            continue
        cid = dbmod.upsert_capper(conn, sid, c["author"])
        ref = f"reddit#{thread['post_id']}#{c['author']}"
        if conn.execute("SELECT 1 FROM picks WHERE external_ref=?",
                        (ref,)).fetchone():
            dupes += 1
            continue
        ev = None
        if parsed["team_nick"]:
            rows = conn.execute(
                "SELECT * FROM events WHERE home=? OR away=? "
                "ORDER BY commence_time DESC", (parsed["team_nick"],
                                                parsed["team_nick"])).fetchall()
            ev = _earliest_future(rows)
        else:  # totals/props/raw: match by event teams named in the text.
            # Order matters: teams in the pick segment first, then the rest
            # of the comment (a "Last Pick" recap names OTHER teams), and
            # prefer a team with a FUTURE event over one whose only event
            # in the window is already final.
            haystacks = _pick_segments(c["text"]) + [c["text"]]
            candidates = []
            for hay in haystacks:
                for name in team_names:
                    if name.lower() in hay.lower() and name not in candidates:
                        candidates.append(name)
            fallback = None
            now = dbmod.now_iso()
            for name in candidates:
                rows = conn.execute(
                    "SELECT * FROM events WHERE home=? OR away=? "
                    "ORDER BY commence_time DESC", (name, name)).fetchall()
                if not rows:
                    continue
                got = _earliest_future(rows)
                if got is not None and (got["commence_time"] or "") >= now:
                    ev = got
                    break
                if fallback is None:
                    fallback = got
            if ev is None:
                ev = fallback
        parsed = {**parsed, "posted_at": c["updated"] or None}
        _pid, st = _sources._uc_store_pick(
            conn, sid, cid, (ev["sport"] if ev else "NHL"), ev, parsed,
            parsed["team_nick"] or parsed.get("team_raw"),
            f"Reddit POTD by u/{c['author']}: {c['text'][:280]}", ref)
        stored += 1
        if st == "UNMATCHED":
            unmatched += 1
        if ev:
            sides[parsed["selection"]] = sides.get(parsed["selection"], 0) + 1
    top = max(sides.items(), key=lambda kv: kv[1]) if sides else None
    detail = (f"thread '{thread['title'][:60]}': comments {len(comments)}, "
              f"submissions {submissions}, stored {stored} "
              f"(unmatched/other-sport {unmatched}), unsupported {unsupported}, "
              f"unparsed {unparsed}, chatter {chatter}, dupes {dupes}")
    if top:
        detail += (f"; most-picked {top[0]} x{top[1]} "
                   "(count noted only — same posters, not an independent family)")
    conn.execute("INSERT INTO ingest_log(ran_at,source,ok,detail) VALUES(?,?,1,?)",
                 (dbmod.now_iso(), SOURCE_NAME, detail))
    conn.commit()
    return {"ok": True, "thread": thread["title"], "comments": len(comments),
            "submissions": submissions, "stored": stored,
            "unmatched": unmatched, "unsupported": unsupported,
            "unparsed": unparsed, "chatter": chatter}
