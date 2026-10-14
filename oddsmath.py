"""Odds conversion + flat-1u profit math. Pure functions, unit-tested."""


def american_to_decimal(american):
    if american is None:
        return None
    a = float(american)
    if a == 0:
        return None
    return 1.0 + (a / 100.0) if a > 0 else 1.0 + (100.0 / abs(a))


def decimal_to_implied(decimal_odds):
    if not decimal_odds or decimal_odds <= 1:
        return None
    return 1.0 / decimal_odds


def american_to_implied(american):
    return decimal_to_implied(american_to_decimal(american))


def profit_units(result, american_odds, stake=1.0):
    """Flat-stake profit: win -> stake*(dec-1), loss -> -stake, push/void -> 0."""
    if result == "win":
        dec = american_to_decimal(american_odds)
        return None if dec is None else round(stake * (dec - 1.0), 4)
    if result == "loss":
        return -float(stake)
    if result in ("push", "void"):
        return 0.0
    return None  # ungraded


def roi_pct(profits):
    """ROI % on flat 1u stakes: sum(profit)/n_bets * 100 (pushes count as staked, 0 profit)."""
    graded = [p for p in profits if p is not None]
    if not graded:
        return None
    return round(sum(graded) / len(graded) * 100.0, 2)


def devig_two_way(price_a, price_b):
    """Proportional devig of a two-way market. Returns (fair_p_a, fair_p_b)
    or (None, None) if either price is missing/invalid."""
    pa, pb = american_to_implied(price_a), american_to_implied(price_b)
    if not pa or not pb:
        return None, None
    s = pa + pb
    return pa / s, pb / s


def clv_pct(captured_american, closing_american, closing_other_american=None):
    """Approx-CLV, PROBABILITY-BASED, in percentage points (Fix 2, 2026-10-13).

    CLV_pp = fair_p_close(picked side) - breakeven_p(captured price), where
    fair_p_close is the closing price DEVIGGED proportionally against the
    other side of the same closing market when that price is available
    (moneyline: both closing MLs; spread: both closing spread prices at the
    closing line; total: closing over/under prices at the closing line).
    Without the other side, the raw closing implied prob is used.
    Positive = you beat the ESPN/DK close. This replaces the old raw
    decimal-price ratio, which flattered results (it ignored the vig):
    e.g. captured +109 vs close -104/-109 reads +6.55% as a ratio and
    +3.13pp as a raw implied-prob diff, but only +1.58pp devigged.
    For spread/total picks the grade note also records line movement in
    points (your line -> closing line); the pp figure is the price part at
    the closing line, documented in README (Fix 2)."""
    breakeven = american_to_implied(captured_american)
    if not breakeven:
        return None
    if closing_other_american is not None:
        fair_close, _ = devig_two_way(closing_american, closing_other_american)
    else:
        fair_close = american_to_implied(closing_american)
    if not fair_close:
        return None
    return round((fair_close - breakeven) * 100.0, 2)
