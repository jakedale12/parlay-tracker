"""Fix 1 (cont.): Action Network parsers — Dangles sections + Stuckey."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_fix1c"

import sources  # noqa: E402

DANGLES = """
<html><head><script type="application/ld+json">{"datePublished":
"2026-10-13T14:00:00.000Z"}</script></head><body>
<p>This is Daily Dangles, a column featuring our top expert's NHL picks.</p>
<h3>Devils vs Red Wings Pick</h3><p>Tuesday, Oct 13 By: Frank Ammirante
The Devils are rested. Pick: Devils Moneyline (-105, DraftKings)</p>
<h3>Capitals vs Hurricanes Pick</h3><p>By: Greg Liodice A scorer play.
Pick: Andrei Svechnikov Anytime Goal Scorer (+180, FanDuel)</p>
<h3>Jets vs Wild Pick</h3><p>By: Nick Martin A market price, not a book price.
Pick: Over 6.5 (50 Cents, Kalshi)</p>
</body></html>"""

STUCKEY = """
<html><head><script type="application/ld+json">{"datePublished":
"2026-10-12T22:56:30.000Z"}</script></head><body>
<h1>Bills vs Rams Prediction, Expert Monday Night Football Picks</h1>
<p>Stuckey Senior Betting Analyst</p>
<p>Long analysis of both offenses. Pick: Rams -3 (-110)</p>
</body></html>"""


def test_action_dangles_sections():
    d = sources.parse_an_dangles(DANGLES, "http://x")
    assert len(d["picks"]) == 2  # Kalshi-priced 'pick' rejected (not book odds)
    ml, prop = d["picks"]
    assert ml["market"] == "moneyline" and ml["selection"] == "Devils ML"
    assert ml["odds"] == -105 and ml["capper"] == "Frank Ammirante"
    assert prop["market"] == "prop" and "Svechnikov" in prop["selection"]
    assert prop["odds"] == 180


def test_action_stuckey_single_pick():
    p = sources.parse_an_stuckey(STUCKEY, "http://x")
    assert p["capper"] == "Stuckey" and p["market"] == "spread"
    assert p["team_nick"] == "Rams" and p["line"] == -3.0 and p["odds"] == -110
    assert (p["away"], p["home"]) == ("Bills", "Rams")
    assert sources.parse_an_stuckey("<html>no byline here</html>") is None
