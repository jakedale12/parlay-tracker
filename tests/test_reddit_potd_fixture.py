"""Reddit POTD parser — fixture tests from a REAL thread feed.

Fixture: tests/fixtures/potd_comments_20261014.xml, fetched live from the
botserver 2026-10-14 (r/sportsbook "Pick of the Day - 10/14/26"). That
day's thread was mostly soccer/esports/other sports — exactly why it is a
good fixture: NHL/NFL picks must parse, other-sport picks must parse and
sit UNMATCHED, parlays/BTTS/double-chance must classify 'unsupported',
and replies/chatter must not count as submissions at all."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_reddit_fixture"

import reddit  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "potd_comments_20261014.xml")
TEAMS = ["Montreal Canadiens", "Washington Capitals", "Toronto Maple Leafs",
         "Dallas Stars", "Colorado Avalanche", "Buffalo Bills",
         "Miami Dolphins"]


def _comments():
    with open(FIXTURE) as f:
        return reddit.parse_comments(f.read())


def _by_author(author):
    for c in _comments():
        if c["author"] == author:
            return c
    raise AssertionError(f"author {author} not in fixture")


def test_fixture_submission_split():
    comments = _comments()
    assert len(comments) == 91
    subs = [c for c in comments
            if c["author"] and c["author"] not in ("AutoModerator",
                                                    "sbpotdbot")
            and reddit.is_potd_submission(c["text"])]
    # the thread bot's template post is excluded by author, replies and
    # chatter (62 in this thread) are not submissions
    assert len(subs) == 25, len(subs)
    kinds = [reddit.classify_potd_comment(c["text"], TEAMS)[0] for c in subs]
    assert kinds.count("bet") == 15, kinds
    assert kinds.count("unsupported") == 8, kinds
    assert kinds.count("unparsed") == 2, kinds


def test_fixture_real_bet_formats():
    # NHL player prop, slash-pair odds: "Lane Hutson o1.5 SOG -120/1.83"
    kind, p = reddit.classify_potd_comment(
        _by_author("Gits_N-Shiggles")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "prop"
    assert p["selection"] == "Lane Hutson Over 1.5 Shots on Goal"
    assert p["odds"] == -120 and p["line"] == 1.5
    # MLB prop with separate Odds field: "Blake Snell O5.5 strikeouts ...
    # Odds: -150 (Draftkings)"
    kind, p = reddit.classify_potd_comment(_by_author("hg7998")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "prop" and p["odds"] == -150
    assert p["selection"] == "Blake Snell Over 5.5 Strikeouts"
    # NCAAF spread at decimal odds: "Sam Houston Bearkats +1.5 at 1.95"
    kind, p = reddit.classify_potd_comment(
        _by_author("Recent-Reading5530")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "spread" and p["line"] == 1.5
    assert p["odds"] == -105  # 1.95 decimal converted
    # soccer ML, American in parens with book name: "Real Madrid ML (-106 bet365)"
    kind, p = reddit.classify_potd_comment(
        _by_author("BrokenJar_v2")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "moneyline" and p["odds"] == -106
    # game total, NOT a player prop: "Bodø/Glimt vs Borussia Dortmund
    # Over 3.5 goals @ 1.90"
    kind, p = reddit.classify_potd_comment(
        _by_author("Substantial_Panic_44")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "total" and p["line"] == 3.5
    assert p["odds"] == -111  # 1.90 decimal converted
    # NBA spread, decimal in parens: "Brooklyn Nets +7.5 ($1.91)"
    kind, p = reddit.classify_potd_comment(_by_author("Staroxt")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "spread" and p["odds"] == -110
    # attached-stat prop: "Robert Gasser o0.5BB (-200)"
    kind, p = reddit.classify_potd_comment(
        _by_author("New_Bison_3079")["text"], TEAMS)
    assert kind == "bet" and p["market"] == "prop" and p["line"] == 0.5
    assert p["selection"] == "Robert Gasser Over 0.5 BB" and p["odds"] == -200


def test_fixture_unsupported_and_unparsed():
    # BTTS / double chance / 2-leg parlays: identifiable, not stored
    for author in ("keepyouropinion", "ParmesanTaco", "Aggressive_Tip_9471",
                   "averageBotDootDoot", "Hercules-sonofZeus", "amikamsi"):
        kind, _ = reddit.classify_potd_comment(_by_author(author)["text"],
                                               TEAMS)
        assert kind == "unsupported", (author, kind)
    # NHL ML with NO price anywhere (gematria post): fails the
    # line-or-odds identifiability rule -> unparsed, never invented
    kind, _ = reddit.classify_potd_comment(
        _by_author("Zestyclose_Bit_5680")["text"], TEAMS)
    assert kind == "unparsed"
    # a plain reply is not a submission at all
    assert not reddit.is_potd_submission(_by_author("Little_Tie142")["text"])


def test_nickname_only_team_mention():
    # Regression: a nickname-only mention ("Leafs") used to crash the
    # ingest (full name searched in a segment that only had the nickname).
    p = reddit.parse_potd_comment("POTD: Leafs ML (-140) tonight", TEAMS)
    assert p["market"] == "moneyline"
    assert p["team_nick"] == "Toronto Maple Leafs" and p["odds"] == -140
    kind, p = reddit.classify_potd_comment(
        "Record: 5-2 Today's Pick: Stars -1.5 (-110)", TEAMS)
    assert kind == "bet" and p["team_nick"] == "Dallas Stars"
    assert p["market"] == "spread" and p["line"] == -1.5


def test_decimal_to_american():
    assert reddit.decimal_to_american(1.95) == -105
    assert reddit.decimal_to_american(2.15) == 115
    assert reddit.decimal_to_american(1.91) == -110
    assert reddit.decimal_to_american(1.60) == -167
    assert reddit.decimal_to_american(2.0) == 100
    assert reddit.decimal_to_american(0.5) is None
    assert reddit.decimal_to_american(25) is None
