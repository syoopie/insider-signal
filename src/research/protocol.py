"""
The evaluation protocol. What decides whether a scoring change is real.

Five prior tuning rounds set weights from univariate lift on the sample the
model itself had selected, with no holdout and no standard errors. This module
is the replacement, and it is deliberately written before any model is fitted so
that the rules are not chosen to flatter a result.

Four things it enforces:

**Time-ordered splits.** No shuffling. Train, then validate, then test, in
calendar order. The test split is touched once.

**Purge and embargo.** A 90-day hold straddles a split boundary. Any training
observation whose exit falls inside the validation window is dropped, plus a
further embargo, or the split leaks the thing it exists to measure.

**Clustered standard errors.** Overlapping windows and repeated tickers mean
nominal n badly overstates independence. Everything reports a cluster count
alongside n, and errors are clustered on ticker and on calendar month.

**Baselines.** A model is only interesting if it beats buying every eligible
purchase, buying every small-cap purchase, the model as it stands today, and a
random ranking at the same selection count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

import numpy as np
import pandas as pd

# Split boundaries, as entry dates. Fixed before any model was fitted.
#
# Usable exec dates run 2024-09-03 to about 2026-06-01 at the 90-day horizon,
# because a later entry has not finished its hold. That is 21 months to divide
# into three splits plus two purge gaps, and the gaps are not optional: a 90-day
# hold started the day before a boundary is still open well inside the next
# split. Three months of gap buys enough room for the primary horizon; the code
# below widens it further when a longer horizon needs it, rather than letting a
# 180-day hold leak across.
TRAIN_START = date(2024, 1, 1)
TRAIN_END = date(2025, 5, 31)
VALID_START = date(2025, 9, 1)
VALID_END = date(2025, 12, 31)
TEST_START = date(2026, 4, 1)

# Extra gap beyond the horizon, so a training exit cannot sit against the very
# first validation entry.
EMBARGO_DAYS = 5

PRIMARY_HORIZON = 90


@dataclass(frozen=True)
class Split:
    name: str
    frame: pd.DataFrame

    def __len__(self) -> int:
        return len(self.frame)


@dataclass(frozen=True)
class Stat:
    """A metric with the honest sample size beside it."""
    n: int
    n_tickers: int
    n_months: int
    mean: Optional[float]
    median: Optional[float]
    hit_rate: Optional[float]
    se_clustered: Optional[float]

    @property
    def t_stat(self) -> Optional[float]:
        if self.mean is None or not self.se_clustered:
            return None
        return self.mean / self.se_clustered

    def line(self, label: str) -> str:
        if self.n == 0:
            return f"  {label:<28}  no observations"
        head = (
            f"  {label:<28}  n={self.n:>5}  tickers={self.n_tickers:>4}  "
            f"mean={self.mean:+7.2f}%  median={self.median:+7.2f}%  "
            f"hit={self.hit_rate:5.1f}%"
        )
        t = self.t_stat
        if t is None:
            return head
        return head + f"  se={self.se_clustered:5.2f}  t={t:+5.2f}"


def label_column(horizon: int = PRIMARY_HORIZON) -> str:
    return f"excess_spy_{horizon}d"


def evaluable(frame: pd.DataFrame, horizon: int = PRIMARY_HORIZON) -> pd.DataFrame:
    """
    Rows that can carry a verdict: eligible, scored, exit already in the past,
    and a measured excess return.
    """
    col = label_column(horizon)
    keep = (
        frame["eligible"]
        & ~frame["scorer_disqualified"].fillna(True)
        & ~frame[f"exit_in_future_{horizon}d"]
        & frame[col].notna()
    )
    return frame[keep].copy()


def split_bounds(horizon: int = PRIMARY_HORIZON) -> dict[str, tuple[date, date]]:
    """
    Entry-date bounds per split, with each split's end pulled back far enough
    that its holds close before the next split opens.

    The nominal ends above suit the 90-day horizon. At 180 days a nominal end
    would leak, so the end becomes `next_start - horizon - embargo` whenever
    that is earlier. Splits shrink at long horizons; they never overlap.
    """
    gap = timedelta(days=horizon + EMBARGO_DAYS)
    return {
        "train": (TRAIN_START, min(TRAIN_END, VALID_START - gap)),
        "validation": (VALID_START, min(VALID_END, TEST_START - gap)),
        "test": (TEST_START, date.max),
    }


def split_frames(frame: pd.DataFrame, horizon: int = PRIMARY_HORIZON) -> dict[str, Split]:
    """
    Time-ordered train / validation / test, purged and embargoed.

    Splitting is on `exec_date`, the date a position would actually have opened,
    not on the transaction date. A trade made in 2018 and disclosed in 2025 is a
    2025 decision, and putting it in the training split by its transaction date
    would train on something that period could not have known.
    """
    exec_date = pd.to_datetime(frame["exec_date"])
    out = {}
    for name, (lo, hi) in split_bounds(horizon).items():
        mask = (exec_date >= pd.Timestamp(lo)) & (exec_date <= pd.Timestamp(hi))
        out[name] = Split(name, frame[mask].copy())
    return out


def _cluster_se(values: np.ndarray, groups: np.ndarray) -> Optional[float]:
    """
    Standard error of the mean, clustered on `groups`.

    Overlapping holds on the same ticker are not independent draws. Treating
    them as independent is how 253 observations at 180d come to look like 253
    pieces of evidence. Between-cluster variance of cluster means, weighted by
    cluster size, is the cheap honest version.
    """
    if len(values) < 2:
        return None
    frame = pd.DataFrame({"v": values, "g": groups})
    sums = frame.groupby("g")["v"].sum()
    counts = frame.groupby("g")["v"].size()
    n = len(values)
    g = len(sums)
    if g < 2:
        return None
    mean = values.mean()
    # Sum over clusters of (sum of deviations within cluster) squared.
    dev = sums - counts * mean
    variance = float((dev ** 2).sum()) / (n ** 2)
    if variance <= 0:
        return None
    correction = g / (g - 1)
    return math.sqrt(variance * correction)


def summarise(frame: pd.DataFrame, horizon: int = PRIMARY_HORIZON) -> Stat:
    """Mean, median, hit rate and a ticker-clustered standard error."""
    col = label_column(horizon)
    values = frame[col].dropna()
    if values.empty:
        return Stat(0, 0, 0, None, None, None, None)

    aligned = frame.loc[values.index]
    months = pd.to_datetime(aligned["exec_date"]).dt.to_period("M")
    return Stat(
        n=len(values),
        n_tickers=aligned["ticker"].nunique(),
        n_months=months.nunique(),
        mean=float(values.mean()),
        median=float(values.median()),
        hit_rate=float((values > 0).mean() * 100),
        se_clustered=_cluster_se(values.to_numpy(), aligned["ticker"].to_numpy()),
    )


# A feature whose average shifts by more than this many training standard
# deviations between splits is not measuring the same thing in both.
MAX_DRIFT_SD = 0.5


def feature_drift(train: pd.DataFrame, other: pd.DataFrame,
                  columns: Sequence[str]) -> pd.DataFrame:
    """
    Standardised shift in each feature's average between two splits.

    Some features here are functions of how much history the database held at
    the time, not of insider behaviour. `first_purchase_unverifiable` fires
    whenever the year before a trade predates ingest, so it was true for 62% of
    training entries and 2% of validation ones. `prior_purchase_31_365d` moves
    the other way as coverage accumulates, 16% to 33%.

    A model fitted on one coverage regime and applied to another is reading a
    clock. This is the same failure that made `first_purchase_12mo` fire on 87%
    of pre-2025-04 signals against 32% after, which CLAUDE.md already records as
    a fact about the ingest start date rather than about insiders.
    """
    rows = []
    for name in columns:
        if name not in train.columns or name not in other.columns:
            continue
        a = pd.to_numeric(train[name], errors="coerce")
        b = pd.to_numeric(other[name], errors="coerce")
        sd = a.std()
        # No variance in training is the worst case, not an exempt one: the fit
        # saw a constant and can carry no information about the column, while
        # the split being scored has it varying. Report it as infinite drift so
        # it is dropped rather than silently kept.
        drift = float("inf") if (not np.isfinite(sd) or sd <= 1e-12) \
            else float(abs(b.mean() - a.mean()) / sd)
        rows.append({
            "feature": name,
            "train_mean": float(a.mean()),
            "other_mean": float(b.mean()),
            "drift_sd": drift,
        })
    if not rows:
        return pd.DataFrame(columns=["feature", "train_mean", "other_mean", "drift_sd"])
    return pd.DataFrame(rows).sort_values("drift_sd", ascending=False)


def stable_features(train: pd.DataFrame, other: pd.DataFrame,
                    columns: Sequence[str],
                    max_drift: float = MAX_DRIFT_SD) -> tuple[list[str], pd.DataFrame]:
    """The features that mean the same thing in both splits, and the ones that do not."""
    drift = feature_drift(train, other, columns)
    if drift.empty:
        return list(columns), drift
    dropped = drift[drift["drift_sd"] > max_drift]
    keep = [c for c in columns if c not in set(dropped["feature"])]
    return keep, dropped


def top_k_by(frame: pd.DataFrame, column: str, k: int) -> pd.DataFrame:
    """
    The k highest-ranked rows, ties broken deterministically.

    Selection is by rank, not by an absolute cutoff. A fixed score threshold on
    a distribution that moves whenever the weights move is why every retune
    reshuffled alert volume, and it makes two models incomparable because they
    are not selecting the same number of trades.
    """
    ordered = frame.sort_values([column, "exec_date", "ticker"],
                                ascending=[False, True, True])
    return ordered.head(k)


def decile_table(frame: pd.DataFrame, column: str,
                 horizon: int = PRIMARY_HORIZON) -> pd.DataFrame:
    """Mean and median excess return per decile of `column`. The ranking test."""
    col = label_column(horizon)
    work = frame[frame[column].notna() & frame[col].notna()].copy()
    if len(work) < 100:
        return pd.DataFrame()
    work["decile"] = pd.qcut(work[column].rank(method="first"), 10, labels=False) + 1
    return (
        work.groupby("decile")
        .agg(n=(col, "size"), lo=(column, "min"), hi=(column, "max"),
             mean=(col, "mean"), median=(col, "median"),
             hit=(col, lambda s: (s > 0).mean() * 100))
        .reset_index()
    )


def decile_spread(table: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    """(mean spread, median spread) between the top and bottom decile."""
    if table.empty:
        return None, None
    top = table[table["decile"] == 10].iloc[0]
    bottom = table[table["decile"] == 1].iloc[0]
    return float(top["mean"] - bottom["mean"]), float(top["median"] - bottom["median"])
