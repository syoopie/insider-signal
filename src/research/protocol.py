"""
What every research number is measured on: the horizon, the label, and which
rows may carry a verdict.

The ruler itself is `walkforward.py`. This holds the definitions it and the
dataset builder share, so a label or an eligibility rule cannot mean one thing
in one script and another in the next.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# Extra gap beyond the horizon, so a training exit cannot sit against the very
# first predicted entry.
EMBARGO_DAYS = 5

PRIMARY_HORIZON = 90


# What a purchase is charged against. "spy" is what every published number was
# measured on and stays the default.
#
# "vol" exists because the raw label is wildly heteroscedastic: excess-vs-SPY has
# a standard deviation of 11.8pp in the calmest volatility quintile and 58.4pp in
# the wildest, so a monthly mean is mostly a report on whichever picks landed in
# the fifth. Dividing by realised volatility at the trade date flattens that to
# 0.52 through 0.62. It is not free, because part of the discount screen's raw
# alpha is a volatility tilt and this removes it, which is why it is a second
# label and not a replacement.
#
# "sector" charges a purchase against its own industry, the confound the placebo
# control could not remove.
LABEL_FAMILIES = {
    "spy": "excess_spy_{horizon}d",
    "iwm": "excess_iwm_{horizon}d",
    "vol": "excess_vol_{horizon}d",
    "sector": "excess_sector_{horizon}d",
}


def label_column(horizon: int = PRIMARY_HORIZON, family: str = "spy") -> str:
    return LABEL_FAMILIES[family].format(horizon=horizon)


def evaluable(frame: pd.DataFrame, horizon: int = PRIMARY_HORIZON,
              label: Optional[str] = None) -> pd.DataFrame:
    """
    Rows that can carry a verdict: eligible, scored, exit already in the past,
    and a measured excess return.
    """
    col = label or label_column(horizon)
    keep = (
        frame["eligible"]
        & ~frame["scorer_disqualified"].fillna(True)
        & ~frame[f"exit_in_future_{horizon}d"]
        & frame[col].notna()
    )
    return frame[keep].copy()
