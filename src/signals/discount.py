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

from typing import Optional, Sequence

import numpy as np

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


# How many recent purchases a trailing reference needs before it is trusted.
# Below this the percentile is noise and the fixed table is the better answer.
MIN_REFERENCE = 120

# The trailing window the percentile is taken against, in days of filings.
#
# The fixed table alone is not enough, and measuring it is what found that out.
# It was built on two years, so "the 90th percentile" means the top decile of
# *that whole period*, not of the moment. The market moves every stock's discount
# together, so a fixed cutoff selects 2.0% of one month's purchases and 23.7% of
# another's, and in the heavy months it reaches well past the top decile into the
# nine flat ones. Measured over 18 months: the fixed cutoff returns +4.19pp with
# a median of **-2.33pp**, while the top decile of each month returns +11.10pp
# with a median of +7.38pp. More than half the effect was being given away.
#
# Ranking against the purchases disclosed in the preceding 60 days recovers most
# of it, at +9.74pp with t=+2.35 and a median of +3.01pp, and halves the spread
# in how much of each month gets selected. It stays point-in-time because the
# window holds only filings that already existed.
#
# 60 days is chosen on the mechanism rather than on the number. A 21-day window
# scored higher on both statistics, +11.03pp and a median of +10.97pp, but it
# rests on about 150 reference purchases, leaves two months unscoreable, and sits
# alone as a spike beside a flat run from 30 to 180 days. Picking it because it
# won would be selecting on the metric, which is the failure this whole exercise
# exists to correct.
REFERENCE_DAYS = 60


def discount_score(pct_below_52wk_high: Optional[float],
                   reference: Optional[Sequence[float]] = None) -> Optional[int]:
    """
    The percentile of this discount among recent purchases, 0 to 100.

    `reference` is the discounts of purchases disclosed in the preceding
    `REFERENCE_DAYS`, from `store.get_discount_reference`. Given enough of them
    the score is this purchase's rank inside that window. Without them it falls
    back to the fixed table, which is what the first day of ingest and the
    earliest backfilled filings get.

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

    if reference is not None and len(reference) >= MIN_REFERENCE:
        below = np.searchsorted(reference, value, side="left")
        return int(round(below / len(reference) * 100))

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
