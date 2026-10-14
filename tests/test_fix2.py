"""Fix 2: probability-based devigged CLV (pp) for ML/spread/total.

Worked pair (constructed, documented): captured +109, closing picked side
-104, closing other side -109.
  breakeven(captured)      = 100/209          = 47.85%
  raw implied close        = 104/204          = 50.98%  -> naive diff +3.13pp
  old ratio formula        = 2.09/1.9615 - 1   = +6.55%
  devigged fair close      = 50.98/(50.98+52.15) = 49.43%
  TRUE CLV                 = 49.43 - 47.85     = +1.58pp  (~ the report's +1.7
  vs naive +3.2 relationship: devigged is roughly half the naive figure.)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_fix2"

import grading  # noqa: E402
import oddsmath  # noqa: E402


def test_devigged_clv_pair():
    devigged = oddsmath.clv_pct(109, -104, -109)
    naive_pp = round((oddsmath.american_to_implied(-104)
                      - oddsmath.american_to_implied(109)) * 100, 2)
    cap, close = oddsmath.american_to_decimal(109), oddsmath.american_to_decimal(-104)
    ratio = round((cap / close - 1.0) * 100, 2)
    assert 1.4 <= devigged <= 1.8, devigged          # ~+1.58pp
    assert 3.0 <= naive_pp <= 3.3, naive_pp           # ~+3.13pp
    assert ratio >= 6.0                               # old formula flattered most
    assert devigged < naive_pp < ratio
    # one-arg fallback (no other side): raw implied diff
    assert oddsmath.clv_pct(120, 100) > 0
    # devig sanity: fair probs sum to 1
    a, b = oddsmath.devig_two_way(-110, -110)
    assert abs(a - 0.5) < 1e-9 and abs(a + b - 1.0) < 1e-9


def test_clv_for_spread_and_total():
    closing = {"home_ml": -150, "away_ml": 130, "spread": -1.5, "total": 6.5,
               "spread_home_price": -110, "spread_away_price": -110,
               "over_price": -115, "under_price": -105}
    # spread pick: away +1.5 captured at -105; closing away price -110 devigged
    # vs -110 -> fair 50.0%, breakeven(-105)=51.22% -> CLV ~ -1.22pp, note shows move 0
    pick = {"market": "spread", "team": "Away Team", "line": 1.5,
            "selection": "Away Team +1.5", "odds_claimed": -105, "odds_available": None}
    clv, c_odds, note = grading._clv_for(pick, closing, False, True)
    assert c_odds == -110 and clv is not None and abs(clv + 1.22) < 0.05
    assert "moved +0" in note or "moved -0" in note or "moved 0" in note
    # total pick: over 6.5 captured -110; closing over -115/under -105 devigged
    pick_t = {"market": "total", "team": None, "line": 6.5,
              "selection": "Over 6.5", "odds_claimed": -110, "odds_available": None}
    clv_t, c_odds_t, _ = grading._clv_for(pick_t, closing, False, False)
    assert c_odds_t == -115 and clv_t is not None
    # line moved: over captured at 5.5, close 6.5 -> moved +1.0 pt in favour
    pick_m = {**pick_t, "line": 5.5, "selection": "Over 5.5"}
    _, _, note_m = grading._clv_for(pick_m, closing, False, False)
    assert "moved +1" in note_m
