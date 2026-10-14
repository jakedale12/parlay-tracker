"""Fix 1: UltimateCapper family parsers (offline, against captured formats)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_fix1"

import sources  # noqa: E402

NHL_ARTICLE = """
<html><head><script type="application/ld+json">{"@type":"NewsArticle",
"headline":"NHL picks","datePublished":"2026-10-13T09:00:00-07:00"}</script></head>
<body><div class="td-post-content">By Jay Cooper - October 13, 2026
<p>New Jersey at Detroit – 6:07 ET Win/Loss Hot: Devils are 4-1. Pick: Red Wings -110</p>
<p>Buffalo at Montreal – 6:37 ET Win/Loss Hot: Sabres are 9-2. Pick: Canadiens -1.5 +165</p>
<p>Vancouver at NY Islanders – 7:52 ET Over/Under Hot: Over is 4-0. Pick: UNDER 6 -110</p>
</div><div>RELATED ARTICLES</div></body></html>"""


def test_uc_nhl_article_parse():
    p = sources.parse_uc_nhl_article(NHL_ARTICLE, "http://x")
    assert p["author"] == "Jay Cooper"
    assert p["posted_at"] == "2026-10-13T16:00:00Z"
    assert len(p["picks"]) == 3
    ml, sp, tot = p["picks"]
    assert (ml["away"], ml["home"]) == ("New Jersey", "Detroit")
    assert ml["market"] == "moneyline" and ml["team_nick"] == "Red Wings" and ml["odds"] == -110
    assert sp["market"] == "spread" and sp["line"] == -1.5 and sp["odds"] == 165
    assert tot["market"] == "total" and tot["line"] == 6.0 and tot["selection"] == "Under 6"


def test_uc_nfl_best_bets_explicit_only():
    sample = ("BEST BETS FOR WEEK 5: Bobby Babowski Bengals -6.5 over Dolphins WIN "
              "Cowboys -7.5 over Bucs LOSS Jay Cooper Rams -3 over Bills")
    bets = sources.parse_uc_nfl_best_bets(sample)
    assert [(b["capper"], b["team_nick"], b["line"]) for b in bets] == [
        ("Bobby Babowski", "Bengals", -6.5), ("Bobby Babowski", "Cowboys", -7.5),
        ("Jay Cooper", "Rams", -3.0)]
    # 'Check back later' weeks and grid numbers yield NOTHING (no guessing)
    assert sources.parse_uc_nfl_best_bets(
        "BEST BETS Bobby Babowski Check back later… Seattle @ Denver +3 -3 +3") == []
