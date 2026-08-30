"""
The ranking factor: how far below its 52-week high a stock sat when the insider bought.

Measured walk-forward across 18 months and 6,690 out-of-sample purchases, the
top decile of this returns +11.13 percentage points above the other purchases of
its own month and its own volatility quintile, with a median of +7.39pp and a
t of +2.29 across months. Against 5,000 random rankings drawn the same way it is
above every draw on both statistics. The score it replaces measures +0.78pp with
a t of +0.40 and a permutation p of 0.27, which is a coin flip.

The same screen applied to stocks nobody bought, on the same dates and with the
same holding windows, returns +5.55pp on the mean and **−1.30pp on the median**.
Deeply discounted stocks in general are a lottery ticket. Deeply discounted
stocks an insider bought are not. The Form 4 is the gate and this is the ranker.

Full account and every robustness check in `docs/scoring-improvement-plan.md`
section 7b.

**The effect is a threshold, not a slope.** Within-month deciles 1 through 9 are
flat, mean between −0.8% and +2.9% with a negative median in every one of them,
and then the tenth returns +17.5% mean and +6.6% median at a 57.7% hit rate
against about 44% everywhere else. Depth still orders the tail, so the score
stays monotone rather than becoming a flag, but the classification cutoff sits
where the jump is.
"""

from __future__ import annotations

from typing import Optional

# Empirical distribution of pct_below_52wk_high over the 8,289 eligible,
# labelled purchases in the research sample, at every fifth percentile.
#
# A fixed table rather than a live quantile query, for three reasons. It is a
# pure function, so the live path and the backfill cannot disagree. It is
# deterministic, so rescoring a two-year-old purchase next year returns what it
# returns today. And it needs no database read during scoring.
#
# The cost is that alert volume moves with the market. In a broad drawdown more
# purchases clear the BUY cutoff, because more of them genuinely are in deeply
# discounted stocks. That is the intended behaviour, but it does mean volume is
# not pinned, and a regime shift will show up as a change in alert count before
# it shows up anywhere else.
KNOTS: list[tuple[float, int]] = [
    (0.00, 0), (0.67, 5), (3.32, 10), (6.42, 15), (9.27, 20),
    (12.14, 25), (14.87, 30), (17.00, 35), (19.54, 40), (22.06, 45),
    (24.87, 50), (28.01, 55), (31.55, 60), (35.24, 65), (39.07, 70),
    (43.00, 75), (47.61, 80), (52.47, 85), (60.12, 90), (69.66, 95),
    (99.15, 100),
]

# The tenth decile of the table above. A purchase at or beyond this is in the
# bucket that carries the entire measured effect.
DEEP_DISCOUNT_PCT = KNOTS[-3][0]


def discount_score(pct_below_52wk_high: Optional[float]) -> Optional[int]:
    """
    The percentile of this discount in the research distribution, 0 to 100.

    None when the context is missing. A caller must not substitute a default:
    an unrankable purchase is unrankable, and scoring it at the median would
    place it in WATCH on no evidence.
    """
    if pct_below_52wk_high is None:
        return None
    try:
        value = float(pct_below_52wk_high)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None

    if value <= KNOTS[0][0]:
        return KNOTS[0][1]
    if value >= KNOTS[-1][0]:
        return KNOTS[-1][1]

    for (lo_value, lo_score), (hi_value, hi_score) in zip(KNOTS, KNOTS[1:]):
        if value <= hi_value:
            if hi_value <= lo_value:
                return hi_score
            fraction = (value - lo_value) / (hi_value - lo_value)
            return int(round(lo_score + fraction * (hi_score - lo_score)))
    return KNOTS[-1][1]
