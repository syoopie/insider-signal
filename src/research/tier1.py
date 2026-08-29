"""
Tier 1 features: everything already in the database that scoring ignores.

Four of these need no new data source, no new fetching, and no schema change.
They are the cheapest available additions and the plan ranks them first for
exactly that reason.

  net insider demand   91,296 stored sale rows the scorer has never read
  insider track record the realised outcome of that person's earlier buys
  averaging down       buying below your own last purchase price
  cluster intensity    the binary cluster flag, made continuous

Every one is computed strictly point-in-time: a feature attached to a purchase
uses only what had been *disclosed* by that purchase's filing date. Using the
transaction date instead would let a trade that was filed later inform a
decision made before anyone could see it, which is precisely the leak the
`_disclosed_by` fix closed in the backfill.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# Trailing window for firm-level buy/sell balance.
DEMAND_WINDOW_DAYS = 90

# Cluster window, matching src/signals/cluster.py's definition.
CLUSTER_WINDOW_DAYS = 14

# Shrinkage constant for a per-insider track record. With k=3, one prior
# purchase carries a quarter of its raw signal and ten carry three quarters.
# An insider with two lucky trades should not outrank a factor measured on
# thousands of rows.
TRACK_RECORD_K = 3.0


def _as_date(value) -> Optional[date]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, date):
        return value
    ts = pd.Timestamp(value)
    return None if pd.isna(ts) else ts.date()


def net_insider_demand(sales: pd.DataFrame, purchases: pd.DataFrame,
                       window_days: int = DEMAND_WINDOW_DAYS) -> pd.DataFrame:
    """
    Buy/sell balance at the issuer over the trailing window, as of each purchase.

    Lakonishok & Lee's result is a buy-minus-sell spread, and the system has
    only ever used the buy half. Both frames need `cik`, `filed_date` and
    `total_value`; only rows already filed as of the purchase count.

    Returns one row per purchase index with:
      demand_buy_ratio    dollar buys / (buys + sells), 0.5 when neither exists
      demand_net_dollars  buys minus sells
      demand_n_sellers    distinct insiders selling in the window
    """
    out = {
        "demand_buy_ratio": np.full(len(purchases), np.nan),
        "demand_net_dollars": np.full(len(purchases), np.nan),
        "demand_n_sellers": np.zeros(len(purchases)),
    }

    sales_by_cik: dict[str, list[tuple]] = defaultdict(list)
    for cik, filed, value, name in zip(
        sales["cik"], sales["filed_date"], sales["total_value"], sales["insider_name"]
    ):
        d = _as_date(filed)
        if d is not None:
            sales_by_cik[cik].append((d, float(value or 0.0), name))

    buys_by_cik: dict[str, list[tuple]] = defaultdict(list)
    for cik, filed, value in zip(
        purchases["cik"], purchases["filed_date"], purchases["total_value"]
    ):
        d = _as_date(filed)
        if d is not None:
            buys_by_cik[cik].append((d, float(value or 0.0)))

    for pos, (cik, filed) in enumerate(zip(purchases["cik"], purchases["filed_date"])):
        as_of = _as_date(filed)
        if as_of is None:
            continue
        start = as_of - timedelta(days=window_days)

        sold = 0.0
        sellers = set()
        for d, value, name in sales_by_cik.get(cik, ()):
            if start <= d <= as_of:
                sold += value
                sellers.add(name)

        bought = sum(v for d, v in buys_by_cik.get(cik, ()) if start <= d <= as_of)

        total = bought + sold
        out["demand_buy_ratio"][pos] = bought / total if total > 0 else 0.5
        out["demand_net_dollars"][pos] = bought - sold
        out["demand_n_sellers"][pos] = len(sellers)

    return pd.DataFrame(out, index=purchases.index)


def insider_track_record(purchases: pd.DataFrame, label_col: str,
                         k: float = TRACK_RECORD_K) -> pd.DataFrame:
    """
    How that insider's *earlier* purchases actually turned out, shrunk toward zero.

    Supported by Cline, Gokkaya & Liu (2017): persistently profitable insiders
    keep predicting. Strictly point-in-time — a prior purchase counts only if it
    was filed before this one *and* its holding period had already closed, so
    the feature never reads an outcome that had not happened yet. Skipping that
    second condition is the subtle version of look-ahead that makes a track
    record look far stronger than it is.
    """
    n = len(purchases)
    out = {
        "track_n_prior": np.zeros(n),
        "track_mean_excess": np.full(n, np.nan),
        "track_mean_shrunk": np.zeros(n),
    }

    by_insider: dict[str, list[tuple]] = defaultdict(list)
    for pos, (name, filed, exec_d, value) in enumerate(zip(
        purchases["insider_name"], purchases["filed_date"],
        purchases["exec_date"], purchases[label_col]
    )):
        by_insider[name].append((pos, _as_date(filed), _as_date(exec_d), value))

    for name, rows in by_insider.items():
        rows.sort(key=lambda r: (r[1] or date.min))
        for pos, filed, _exec_d, _value in rows:
            if filed is None:
                continue
            priors = [
                v for _p, f, e, v in rows
                if f is not None and f < filed and e is not None
                and not pd.isna(v)
                # The prior trade's outcome must have been observable by now.
                and e + timedelta(days=90) < filed
            ]
            if not priors:
                continue
            mean = float(np.mean(priors))
            out["track_n_prior"][pos] = len(priors)
            out["track_mean_excess"][pos] = mean
            out["track_mean_shrunk"][pos] = mean * (len(priors) / (len(priors) + k))

    return pd.DataFrame(out, index=purchases.index)


def averaging_down(purchases: pd.DataFrame) -> pd.DataFrame:
    """
    Is this insider paying less than they paid last time, at the same issuer?

    The worst outcome in the whole history, LGF at -63.1% over 180 days, is
    recorded in CLAUDE.md as insiders averaging down with no filter against it.
    This is that filter. `avg_down_pct` is negative when the new purchase is
    cheaper than the previous one.
    """
    n = len(purchases)
    out = {
        "prior_purchase_price": np.full(n, np.nan),
        "avg_down_pct": np.full(n, np.nan),
        "is_averaging_down": np.zeros(n),
    }

    by_key: dict[tuple, list[tuple]] = defaultdict(list)
    for pos, (name, cik, filed, price) in enumerate(zip(
        purchases["insider_name"], purchases["cik"],
        purchases["filed_date"], purchases["price_per_share"]
    )):
        by_key[(name, cik)].append((pos, _as_date(filed), price))

    for rows in by_key.values():
        rows.sort(key=lambda r: (r[1] or date.min))
        for i, (pos, filed, price) in enumerate(rows):
            if filed is None or price is None or pd.isna(price) or price <= 0:
                continue
            earlier = [p for _p, f, p in rows[:i]
                       if f is not None and f < filed and p and not pd.isna(p) and p > 0]
            if not earlier:
                continue
            last = earlier[-1]
            out["prior_purchase_price"][pos] = last
            out["avg_down_pct"][pos] = (price - last) / last * 100.0
            out["is_averaging_down"][pos] = 1.0 if price < last else 0.0

    return pd.DataFrame(out, index=purchases.index)


def cluster_intensity(purchases: pd.DataFrame,
                      window_days: int = CLUSTER_WINDOW_DAYS) -> pd.DataFrame:
    """
    The cluster, as numbers rather than a flag.

    `cluster_flag` collapses "three insiders bought" and "eleven insiders bought
    two million dollars each" into the same boolean. Counted over purchases
    already disclosed at this filing date, so a cluster cannot form out of
    filings the market had not seen.
    """
    n = len(purchases)
    out = {
        "cluster_n_buyers": np.ones(n),
        "cluster_dollars": np.zeros(n),
        "cluster_n_roles": np.ones(n),
        "cluster_span_days": np.zeros(n),
    }

    by_cik: dict[str, list[tuple]] = defaultdict(list)
    for pos, (cik, filed, tx_date, name, role, value, direct) in enumerate(zip(
        purchases["cik"], purchases["filed_date"], purchases["transaction_date"],
        purchases["insider_name"], purchases["role_category"],
        purchases["total_value"], purchases["is_direct"]
    )):
        by_cik[cik].append((pos, _as_date(filed), _as_date(tx_date), name, role,
                            float(value or 0.0), bool(direct)))

    for rows in by_cik.values():
        for pos, filed, tx_date, _name, _role, _value, _direct in rows:
            if filed is None or tx_date is None:
                continue
            start = tx_date - timedelta(days=window_days)
            peers = [
                r for r in rows
                if r[1] is not None and r[1] <= filed          # already disclosed
                and r[2] is not None and start <= r[2] <= tx_date
                and r[6]                                        # direct only
            ]
            if not peers:
                continue
            names = {r[3] for r in peers}
            dates = [r[2] for r in peers]
            out["cluster_n_buyers"][pos] = len(names)
            out["cluster_dollars"][pos] = sum(r[5] for r in peers)
            out["cluster_n_roles"][pos] = len({r[4] for r in peers if r[4]})
            out["cluster_span_days"][pos] = (max(dates) - min(dates)).days

    return pd.DataFrame(out, index=purchases.index)


def value_vs_own_history(purchases: pd.DataFrame) -> pd.DataFrame:
    """
    This purchase's size against that insider's trailing average.

    A $50k buy means something different from a director who usually buys $5k
    than from one who usually buys $500k. Raw dollar value was removed from the
    model in round 4 for negative lift; this is the normalised form, which is a
    different question and has to be tested rather than assumed to inherit that.
    """
    n = len(purchases)
    out = {"value_vs_own_mean": np.full(n, np.nan)}

    by_insider: dict[str, list[tuple]] = defaultdict(list)
    for pos, (name, filed, value) in enumerate(zip(
        purchases["insider_name"], purchases["filed_date"], purchases["total_value"]
    )):
        by_insider[name].append((pos, _as_date(filed), float(value or 0.0)))

    for rows in by_insider.values():
        rows.sort(key=lambda r: (r[1] or date.min))
        for i, (pos, filed, value) in enumerate(rows):
            earlier = [v for _p, f, v in rows[:i] if f is not None and filed and f < filed and v > 0]
            if not earlier or value <= 0:
                continue
            out["value_vs_own_mean"][pos] = value / float(np.mean(earlier))

    return pd.DataFrame(out, index=purchases.index)
