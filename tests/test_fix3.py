"""Fix 3: NHL prop text parsing + settlement via game-log values."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_fix3"

import nhlprops  # noqa: E402


def test_parse_prop_forms():
    assert nhlprops.parse_prop("Andrei Svechnikov Anytime Goal Scorer") == \
        ("Andrei Svechnikov", "goals", 0.5, "over")
    assert nhlprops.parse_prop("Andrei Svechnikov Anytime Goalscorer")[1] == "goals"
    assert nhlprops.parse_prop("William Nylander Over 2.5 Shots on Goal") == \
        ("William Nylander", "shots", 2.5, "over")
    assert nhlprops.parse_prop("Nathan MacKinnon Over 1.5 Points") == \
        ("Nathan MacKinnon", "points", 1.5, "over")
    p, reason = nhlprops.parse_prop("Robert Thomas and Jimmy Snuggerud 1+ Point Each")
    assert p is None and "combo" in reason
    p2, reason2 = nhlprops.parse_prop("Some Goalie Over 24.5")  # no stat word
    assert p2 is None and "not parseable" in reason2


def test_prop_settlement_math():
    # monkeypatch the API layer: roster + game log for a fake player
    orig = nhlprops._get

    def fake(path):
        if path.startswith("/roster/"):
            return {"forwards": [{"id": 99, "firstName": {"default": "Test"},
                                  "lastName": {"default": "Player"}}],
                    "defensemen": [], "goalies": []}
        return {"gameLog": [{"gameDate": "2026-10-13", "opponentAbbrev": "CAR",
                             "goals": 2, "assists": 0, "points": 2, "shots": 5}]}
    nhlprops._get = fake
    try:
        pick = {"selection": "Test Player Anytime Goal Scorer"}
        ev = {"home": "Carolina Hurricanes", "away": "Washington Capitals",
              "commence_time": "2026-10-13T23:00:00Z"}
        res, detail, term = nhlprops.try_grade(pick, ev)
        assert res == "win" and "goals=2" in detail and term
        pick2 = {"selection": "Test Player Over 5.5 Shots on Goal"}
        res2, detail2, _ = nhlprops.try_grade(pick2, ev)
        assert res2 == "loss" and "shots=5" in detail2
    finally:
        nhlprops._get = orig
