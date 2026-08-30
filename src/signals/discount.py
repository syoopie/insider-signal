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
# Below this a purchase is left unranked rather than ranked against the fixed
# table: the two rules disagree, and mixing them means a signal's meaning depends
# on how busy the filing calendar happened to be. Measured on 18 months, the four
# picks that came from the fallback averaged -34.07pp against the ranked picks'
# +15.59pp. Unrankable is the conservative answer and it is never alerted.
MIN_REFERENCE = 120

# The trailing window the percentile is taken against, in days of filings.
#
# A fixed table is not enough, and measuring the shipped rule is what found that
# out. The table was built on two years, so "the 90th percentile" meant the top
# decile of *that whole period*, not of the moment. The market moves every
# stock's discount together, so a fixed cutoff selected 2.0% of one month's
# purchases and 23.7% of another's, and in the heavy months it reached well past
# the top decile into the nine flat ones. It returned +4.19pp with a median of
# -2.33pp against the top-decile-of-month rule's +11.10pp and +7.38pp. More than
# half the effect was being given away.
#
# The rule the research validated is "the top decile of the current
# cross-section", which cannot be computed at filing time without knowing the
# rest of the month. A trailing window is the causal approximation of it, and
# the shorter the window the closer the approximation. That is testable
# independently of returns, by asking what fraction of each month clears the
# cutoff, and it holds monotonically:
#
#   window  refs   mean      t   median     t   share of month selected
#       14   210  +13.60  +2.39   +7.95  +1.47   0.0% to 12.6%
#       21   306  +11.37  +2.51   +8.75  +1.68   0.0% to 13.7%
#       30   424  +11.05  +2.61   +7.42  +1.87   0.8% to 15.6%
#       45   518   +8.98  +2.15   +2.73  +0.56   2.5% to 18.1%
#       60   687   +8.02  +2.05   +1.09  +0.24   2.0% to 20.0%
#       90  1060   +9.29  +2.21   +1.16  +0.24   2.5% to 20.4%
#      180  2073   +9.29  +2.08   +1.93  +0.42   3.0% to 20.4%
#      400  4323   +7.07  +1.79   +3.30  +0.75   1.5% to 22.2%
#   month      -  +11.10  +2.28   +7.38  +1.33   9.1% to 10.1%  (the ceiling)
#
# The spread narrows as the window shortens, from 20.7 points at 400 days to
# 12.6 at 14, and the returns follow it. That is a mechanism agreeing with an
# outcome rather than a maximum picked out of a sweep.
#
# 30 days, for three reasons that are not "it scored highest", though it does.
# It has the best t on both the mean and the median of any window here. It rests
# on 424 reference purchases, two to three times what 14 and 21 days give. And it
# is the shortest window that never leaves a month with no signals at all: 14 and
# 21 days both have months where nothing clears the cutoff, which is a product
# failure whatever it measures.
#
# An earlier version of this comment argued for 60 days and dismissed the short
# end as a spike. That was measured against a reference inflated 1.23x by
# counting broker fills as separate purchases, and without the share-of-month
# diagnostic that shows the trend is monotone rather than spiky.
REFERENCE_DAYS = 30


def discount_score(pct_below_52wk_high: Optional[float],
                   reference: Optional[Sequence[float]] = None) -> Optional[int]:
    """
    The percentile of this discount among recent purchases, 0 to 100.

    `reference` is the discounts of purchases disclosed in the preceding
    `REFERENCE_DAYS`, from `store.get_discount_reference`. Given enough of them
    the score is this purchase's rank inside that window; given too few, the
    purchase is unrankable and this returns None rather than falling back, so a
    quiet filing week cannot silently change what a score means.

    Passing no reference at all is a different case, and gets the fixed table.
    That is for callers with no database: the tests, and the web explainer's
    mirror of this function.

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

    if reference is not None:
        if len(reference) < MIN_REFERENCE:
            return None
        below = np.searchsorted(reference, value, side="left")
        # Truncate rather than round, so `score >= 90` means exactly "at or above
        # the 90th percentile". Rounding admits everything from 89.5, which is
        # the top 10.5% and not the decile the effect was measured on.
        return min(100, int(below / len(reference) * 100))

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
