"""Part A1(ii): Covers Contests consensus row parsing (NHL ML / NFL ATS)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARLAY_DATA"] = "/tmp/parlay_test_partA"

import sources  # noqa: E402

PAGE = """
<html><body><table>
<tr><td>NHL Dal Col Wed. Oct 14 8:30 pm ET 21% 79% +127 -138 18 67 Details</td></tr>
<tr><td>NFL Ind Jac Sun. Oct 18 1:00 pm ET 30% 70% +4 -4 35 83 Details</td></tr>
<tr><td>NHL NJ Tor Thu. Oct 15 7:00 pm ET 40% 60% +120 -140 10 15 Details</td></tr>
</table></body></html>"""


SC_PAGE = """
<html><body><p>Westgate SuperContest Week 9 Lines, Consensus &amp; Standings
Nov 1 &ndash; Nov 7, 2026 Bold = Fave Away Line Home
Thursday, November 5 8:15 PM ET Buccaneers &minus;8.5 Cowboys
Sunday, November 8 1:00 PM ET Colts &minus;2.5 Steelers
Bold indicates the spread favorite.</p>
<p>Consensus Picks # Team Entries Pct 1 Steelers 214 36.0% 2 Buccaneers 150 25.0%</p>
</body></html>"""


def test_supercontest_parse():
    p = sources.parse_supercontest(SC_PAGE)
    assert p["week"] == 9
    assert p["lines"]["Buccaneers"] == (-8.5, "Dallas Cowboys", False)
    assert p["lines"]["Steelers"] == (2.5, "Indianapolis Colts", True)
    assert p["consensus"][0] == ("Steelers", 214, 36.0)


def test_covers_consensus_parse():
    nhl = sources.parse_covers_consensus(PAGE, "NHL")
    assert len(nhl) == 2
    r = nhl[0]
    assert (r["away"], r["home"]) == ("Dallas Stars", "Colorado Avalanche")
    assert r["away_pct"] == 21 and r["home_pct"] == 79
    assert r["away_val"] == 127 and r["home_val"] == -138  # ML prices
    assert (r["away_n"], r["home_n"]) == (18, 67)
    assert nhl[1]["away"] == "New Jersey Devils"  # 2-letter abbrev NJ handled
    nfl = sources.parse_covers_consensus(PAGE, "NFL")
    assert len(nfl) == 1 and nfl[0]["home_val"] == -4  # ATS line, not a price
