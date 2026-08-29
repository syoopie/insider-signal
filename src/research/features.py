"""
The candidate feature set, in one place.

Named here rather than in each script so the estimation, the model fit and the
evaluation all see the same columns. `analyze_factors.py` kept its own
hard-coded ALL_FACTORS list, which drifted until it was computing lift for
factors the scorer could no longer emit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# What the model already uses, as the scorer's own factor columns.
CURRENT_FACTORS = [
    "f_indirect_purchase",
    "f_role_cfo", "f_role_director", "f_role_coo", "f_role_officer",
    "f_role_ceo", "f_role_chairman", "f_role_other",
    "f_cap_small", "f_cap_unknown",
    "f_holdings_increase_5pct",
    "f_sequenced_buying_30d", "f_prior_purchase_31_365d",
    "f_first_purchase_12mo", "f_first_purchase_unverifiable",
]

# Raw forms of the same information, unbucketed. A bucketed factor throws away
# the difference between a 5% holdings increase and a 400% one.
RAW_TERMS = [
    "pct_holdings_increase",
    "total_value",
    "filing_lag_days",
]

TIER1 = [
    "demand_buy_ratio", "demand_net_dollars", "demand_n_sellers",
    "track_n_prior", "track_mean_shrunk",
    "avg_down_pct", "is_averaging_down",
    "cluster_n_buyers", "cluster_dollars", "cluster_n_roles", "cluster_span_days",
    "value_vs_own_mean",
]

TIER2 = [
    "tx_pct_below_52wk_high", "tx_pct_above_52wk_low",
    "tx_ret_21d", "tx_ret_63d", "tx_ret_252d",
    "tx_vol_21d", "tx_dollar_vol_21d",
    "price_deviation_pct",
]

ALL_CANDIDATES = CURRENT_FACTORS + RAW_TERMS + TIER1 + TIER2


def winsorize(frame: pd.DataFrame, columns, lower: float = 0.01,
              upper: float = 0.99) -> pd.DataFrame:
    """
    Clip the tails of heavy-tailed columns.

    `demand_net_dollars` and `cluster_dollars` span several orders of magnitude,
    and one filer error becomes the whole regression. Clipping at the 1st and
    99th percentile keeps the ranking and removes the leverage. Quantiles come
    from whichever frame is passed, so fitting must pass the training split and
    nothing else.
    """
    out = frame.copy()
    for name in columns:
        if name not in out.columns:
            continue
        values = pd.to_numeric(out[name], errors="coerce")
        lo, hi = values.quantile(lower), values.quantile(upper)
        if pd.notna(lo) and pd.notna(hi) and hi > lo:
            out[name] = values.clip(lo, hi)
    return out


LOG_SCALED = ["total_value", "demand_net_dollars", "cluster_dollars", "tx_dollar_vol_21d"]


def log_scale(frame: pd.DataFrame, columns=LOG_SCALED) -> pd.DataFrame:
    """
    Signed log1p for dollar amounts, so a doubling counts the same everywhere.

    Untransformed, a $200M cluster is two thousand times a $100k one and the
    regression sees only the handful of largest. Signed, because
    `demand_net_dollars` is negative whenever insiders are net sellers, and that
    sign is the whole point of the feature.
    """
    out = frame.copy()
    for name in columns:
        if name not in out.columns:
            continue
        values = pd.to_numeric(out[name], errors="coerce").to_numpy(dtype="float64")
        out[name] = np.sign(values) * np.log1p(np.abs(values))
    return out
