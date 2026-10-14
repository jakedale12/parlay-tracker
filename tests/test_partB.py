"""Part B: Reddit POTD RSS bridge — Atom parsing + conservative pick parse."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_partB"

import reddit  # noqa: E402

LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry><title>Pick of the Day - 10/14/26 (Wednesday)</title>
 <link href="https://www.reddit.com/r/sportsbook/comments/abc123/pick_of_the_day_101426/"/></entry>
 <entry><title>MLB playoffs thread</title>
 <link href="https://www.reddit.com/r/sportsbook/comments/def456/mlb/"/></entry>
</feed>"""

COMMENTS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry><author><name>/u/sharpbettor99</name></author>
 <content type="html">&lt;p&gt;POTD: Toronto Maple Leafs ML (-140)&lt;/p&gt;
 &lt;p&gt;Record: 12-3, +8.2u&lt;/p&gt;</content>
 <updated>2026-10-14T15:00:00+00:00</updated></entry>
 <entry><author><name>/u/casualfan</name></author>
 <content type="html">&lt;p&gt;Great thread today, good luck everyone&lt;/p&gt;</content>
 <updated>2026-10-14T15:05:00+00:00</updated></entry>
</feed>"""

TEAMS = ["Toronto Maple Leafs", "Utah Mammoth", "Dallas Stars"]


def test_reddit_listing_and_thread():
    entries = reddit.parse_listing(LISTING)
    assert len(entries) == 2 and entries[0]["post_id"] == "abc123"
    th = reddit.find_potd_thread(entries, datetime(2026, 10, 14, 16, 0,
                                                    tzinfo=timezone.utc))
    assert th["post_id"] == "abc123"


def test_reddit_comment_pick_parse():
    comments = reddit.parse_comments(COMMENTS)
    assert comments[0]["author"] == "sharpbettor99"
    p = reddit.parse_potd_comment(comments[0]["text"], TEAMS)
    assert p["market"] == "moneyline" and p["team_nick"] == "Toronto Maple Leafs"
    assert p["odds"] == -140
    # no marker / no priced bet -> not stored
    assert reddit.parse_potd_comment(comments[1]["text"], TEAMS) is None
    assert reddit.parse_potd_comment("Maple Leafs ML (-140) lock it in",
                                     TEAMS) is None  # no POTD/Pick marker
    t = reddit.parse_potd_comment("POTD: Over 6.5 (-110) in the late game", TEAMS)
    assert t["market"] == "total" and t["line"] == 6.5
