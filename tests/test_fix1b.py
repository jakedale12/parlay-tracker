"""Fix 1 (cont.): SportsHub FREE PICKS parser — free-only, premium excluded."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_fix1b"

import sources  # noqa: E402

PROFILE = """
<html><body><div>Today's Picks (3)</div>
<div>Sports Hub - FREE PICKS Pending Free Pick Ice Hockey · Oct 14 · 7:00 PM ·
Moneyline Toronto Maple Leafs @ Boston Bruins Pick TORONTO MAPLE LEAFS ML -120</div>
<div>Sports Hub - FREE PICKS Pending Premium Pick Ice Hockey · Oct 14 · 7:30 PM ·
Moneyline Colorado Avalanche @ Dallas Stars Pick Colorado Avalanche @ Dallas Stars
-110 Requires SportsHub Upgrade</div>
<div>Sports Hub - FREE PICKS Pending Free Pick Ice Hockey · Oct 14 · 8:00 PM ·
Total Minnesota Wild @ Winnipeg Jets Pick TOTAL u6½-120 (WINNIPEG JETS vrs
MINNESOTA WILD)</div>
<div>Get Access to All Premium Picks</div></body></html>"""


def test_sportshub_free_only_and_totals():
    cards = sources.parse_sportshub_profile(PROFILE, "Sports Hub - FREE PICKS")
    assert len(cards) == 2  # the Premium card is NOT a free pick — excluded
    ml, tot = cards
    assert ml["away"] == "Toronto Maple Leafs" and ml["home"] == "Boston Bruins"
    assert ml["market"] == "moneyline" and ml["odds"] == -120
    assert tot["market"] == "total" and tot["selection"] == "Under 6.5"
    assert tot["line"] == 6.5 and tot["odds"] == -120  # ½ handled
    # spread form
    p = sources._sh_parse_pick_text("NEW JERSEY DEVILS +1½ -278")
    assert p["market"] == "spread" and p["line"] == 1.5 and p["odds"] == -278
