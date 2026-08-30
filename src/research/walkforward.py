"""
Walk-forward evaluation, and the two metrics that survive a three-month sample.

The first protocol split the history once, into train, validation and one test
window. That window turned out to be 762 rows across three months, 77% of them
in a single month whose mean excess return was +10.7%. Every number computed on
it was therefore mostly a measurement of May 2026, and the model that "failed to
replicate" and the one that would have passed are separated by less noise than
one month of market weather. A single-draw random baseline sat next to it,
seeded once, so the floor a challenger had to clear was itself a coin flip: its
draw came in at +15.78% against a distribution whose median is +8.2%.

Two changes fix the ruler.

**Roll the origin.** Refit every month on everything whose hold had already
closed, predict the month, move on. Every row from the first month with enough
history onward gets a genuinely out-of-sample prediction, so the sample is
thousands of rows across twenty months instead of hundreds across three, and no
single month can carry the verdict.

**Judge within the month.** 97.5% of the variance in excess-vs-SPY return is
within-month, but the 2.5% between months is exactly what a top-k pick can
harvest by accident: tilt the selection toward the months that went up and the
mean rises with no ranking skill at all. Both metrics here compare a purchase
only against the other purchases available the same month. Rank IC is the
correlation between prediction and outcome inside each month. Selection alpha is
what the picks returned minus what that month's whole eligible pool returned.
Both average across months and are tested on the spread between months, which is
the honest unit of independence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from src.research.protocol import EMBARGO_DAYS, PRIMARY_HORIZON, label_column

# A month is only predicted once the expanding window behind it holds enough
# closed holds to fit on. Below this the coefficients are noise and the fold
# reports the noise as an out-of-sample result.
MIN_TRAIN_ROWS = 400
MIN_TRAIN_MONTHS = 3

# A month with fewer than this many eligible purchases cannot support a
# within-month ranking; its IC would be a two-point correlation.
MIN_MONTH_ROWS = 20

Scorer = Callable[[pd.DataFrame], np.ndarray]
Fitter = Callable[[pd.DataFrame, str], Optional[Scorer]]


@dataclass(frozen=True)
class Fold:
    month: pd.Period
    train: pd.DataFrame
    predict: pd.DataFrame


def month_of(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["exec_date"]).dt.to_period("M")


def folds(frame: pd.DataFrame, horizon: int = PRIMARY_HORIZON,
          min_train_rows: int = MIN_TRAIN_ROWS,
          min_month_rows: int = MIN_MONTH_ROWS) -> list[Fold]:
    """
    One expanding-window fold per predictable month.

    A training row qualifies only when its hold closed before the month opened,
    which is the same purge the fixed splits applied, applied twenty times
    instead of twice. `exec_date + horizon + embargo < month start` is the whole
    rule, and it is what makes the prediction genuinely out of sample rather
    than merely out of period.
    """
    work = frame.copy()
    work["_m"] = month_of(work)
    exec_date = pd.to_datetime(work["exec_date"])
    closed_by = exec_date + pd.Timedelta(days=horizon + EMBARGO_DAYS)

    out = []
    for month in sorted(work["_m"].dropna().unique()):
        opens = month.to_timestamp()
        predict = work[work["_m"] == month]
        if len(predict) < min_month_rows:
            continue
        train = work[closed_by < opens]
        if len(train) < min_train_rows or train["_m"].nunique() < MIN_TRAIN_MONTHS:
            continue
        out.append(Fold(month, train.drop(columns="_m"), predict.drop(columns="_m")))
    return out


def walk_forward(frame: pd.DataFrame, fitter: Fitter,
                 horizon: int = PRIMARY_HORIZON,
                 column: str = "oos") -> pd.DataFrame:
    """
    Every predictable row, carrying a prediction made without seeing it.

    `fitter` receives one fold's training rows and the label column name, and
    returns something that scores a frame. Returning None skips the fold, which
    is what a fit that fails to converge should do rather than emitting zeros
    that would be silently ranked.
    """
    label = label_column(horizon)
    pieces = []
    for fold in folds(frame, horizon):
        scorer = fitter(fold.train, label)
        if scorer is None:
            continue
        block = fold.predict.copy()
        block[column] = np.asarray(scorer(block), dtype="float64")
        pieces.append(block)
    if not pieces:
        return frame.iloc[0:0].assign(**{column: pd.Series(dtype="float64")})
    return pd.concat(pieces, ignore_index=True)


def _spearman(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 3:
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    sa, sb = ra.std(), rb.std()
    if sa <= 1e-12 or sb <= 1e-12:
        return None
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


@dataclass(frozen=True)
class MonthlyStat:
    """A per-month series and the test on its mean. Months are the sample size."""
    per_month: pd.DataFrame
    mean: Optional[float]
    n_months: int
    n_rows: int

    @property
    def t_stat(self) -> Optional[float]:
        if self.n_months < 2 or self.mean is None:
            return None
        sd = float(self.per_month["value"].std(ddof=1))
        if not np.isfinite(sd) or sd <= 1e-12:
            return None
        return self.mean / (sd / np.sqrt(self.n_months))

    def line(self, label: str) -> str:
        if self.mean is None:
            return f"  {label:<34}  no months"
        t = self.t_stat
        tail = f"t={t:+5.2f}" if t is not None else "t=   n/a"
        return (f"  {label:<34}  months={self.n_months:>3}  n={self.n_rows:>5}  "
                f"mean={self.mean:+7.3f}  {tail}")


def rank_ic(scored: pd.DataFrame, column: str = "oos",
            horizon: int = PRIMARY_HORIZON,
            min_month_rows: int = MIN_MONTH_ROWS) -> MonthlyStat:
    """
    Mean within-month Spearman correlation between prediction and outcome.

    This is the metric the product actually needs. Given the purchases filed
    this month, does the model put the ones that went on to beat the market
    nearer the top. It cannot be won by preferring good months, because it never
    compares across them.
    """
    label = label_column(horizon)
    work = scored[scored[column].notna() & scored[label].notna()].copy()
    work["month"] = month_of(work)

    rows = []
    for month, block in work.groupby("month"):
        if len(block) < min_month_rows:
            continue
        ic = _spearman(block[column].to_numpy(), block[label].to_numpy())
        if ic is not None:
            rows.append({"month": month, "n": len(block), "value": ic})
    if not rows:
        return MonthlyStat(pd.DataFrame(columns=["month", "n", "value"]), None, 0, 0)
    frame = pd.DataFrame(rows)
    return MonthlyStat(frame, float(frame["value"].mean()), len(frame),
                       int(frame["n"].sum()))


# Risk buckets for the stratified benchmark. Realised volatility before the
# purchase splits the sample into things that cannot be compared directly.
RISK_COLUMN = "tx_vol_21d"
RISK_BUCKETS = 5


def risk_bucket(frame: pd.DataFrame, column: str = RISK_COLUMN,
                buckets: int = RISK_BUCKETS) -> pd.Series:
    """
    Which volatility quintile each purchase sat in, inside its own month.

    Bucketing within the month rather than across the sample keeps the control
    honest when the whole market's volatility moves, which it does.
    """
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype="float64")
    values = pd.to_numeric(frame[column], errors="coerce")
    months = month_of(frame)
    out = pd.Series(np.nan, index=frame.index, dtype="float64")
    for _month, idx in months.groupby(months).groups.items():
        block = values.loc[idx]
        # `rank(method="first")` gives a constant column n distinct ranks, and
        # qcut would then split it into quintiles that mean nothing and charge
        # each pick against an arbitrary slice of row order.
        if block.notna().sum() < buckets * 2 or block.nunique(dropna=True) < buckets:
            out.loc[idx] = 0.0
            continue
        out.loc[idx] = pd.qcut(block.rank(method="first"), buckets,
                               labels=False, duplicates="drop")
    return out.fillna(-1.0)


def selection_alpha(scored: pd.DataFrame, column: str = "oos",
                    rate: float = 0.10, horizon: int = PRIMARY_HORIZON,
                    min_month_rows: int = MIN_MONTH_ROWS,
                    min_picks: int = 2, statistic: str = "mean",
                    risk_matched: bool = False) -> MonthlyStat:
    """
    What the top `rate` of each month returned, minus what its benchmark returned.

    Selecting within the month rather than across the pooled sample is the point.
    A pooled top-k can put every pick in one month and collect that month's
    weather; this cannot, because the month it picks in is the month it is
    charged against.

    `risk_matched` charges each pick against its own volatility quintile inside
    that month rather than against the month as a whole. Without it the metric
    rewards a pure risk tilt: ranking purchases by prior 21-day volatility alone
    scores +11.9pp with t=3.13 here, which is leverage on a rising market and not
    a judgement about insiders. With it, the question becomes whether the model
    picks better than the other purchases of comparable riskiness.

    `statistic` of "median" answers the other half. A fat right tail lifts a mean
    without any of the picks being reliably good, and the shipped-model postmortem
    already turned on exactly that gap between mean and median.
    """
    label = label_column(horizon)
    work = scored[scored[column].notna() & scored[label].notna()].copy()
    work["month"] = month_of(work)
    work["_bucket"] = risk_bucket(work) if risk_matched else 0.0
    aggregate = (lambda s: float(s.median())) if statistic == "median" \
        else (lambda s: float(s.mean()))

    rows = []
    for month, block in work.groupby("month"):
        if len(block) < min_month_rows:
            continue
        k = max(min_picks, int(round(len(block) * rate)))
        picks = block.nlargest(k, column)
        reference = block.groupby("_bucket")[label].apply(aggregate)
        expected = picks["_bucket"].map(reference).astype("float64")
        rows.append({"month": month, "n": len(picks),
                     "value": aggregate(picks[label]) - aggregate(expected)})
    if not rows:
        return MonthlyStat(pd.DataFrame(columns=["month", "n", "value"]), None, 0, 0)
    frame = pd.DataFrame(rows)
    return MonthlyStat(frame, float(frame["value"].mean()), len(frame),
                       int(frame["n"].sum()))


def random_selection_alpha(scored: pd.DataFrame, draws: int = 400,
                           rate: float = 0.10, horizon: int = PRIMARY_HORIZON,
                           seed: int = 20260830,
                           min_month_rows: int = MIN_MONTH_ROWS,
                           statistic: str = "mean",
                           risk_matched: bool = False) -> np.ndarray:
    """
    The same statistic under `draws` random rankings. The coin flip, drawn properly.

    The first protocol compared against one seeded draw, which came in at the
    93rd percentile of this distribution and was reported as the bar to beat.
    A percentile against the whole distribution is the comparison that was meant.
    """
    rng = np.random.default_rng(seed)
    work = scored.copy()
    out = []
    for _ in range(draws):
        work["_r"] = rng.random(len(work))
        stat = selection_alpha(work, "_r", rate, horizon, min_month_rows,
                               statistic=statistic, risk_matched=risk_matched)
        if stat.mean is not None:
            out.append(stat.mean)
    return np.array(out)


def permutation_alpha(frame: pd.DataFrame, fitter: Fitter, draws: int = 200,
                      rate: float = 0.10, horizon: int = PRIMARY_HORIZON,
                      seed: int = 20260830, statistic: str = "mean",
                      risk_matched: bool = True) -> np.ndarray:
    """
    The whole pipeline's statistic under labels shuffled inside each month.

    `random_selection_alpha` asks whether one fixed set of predictions beats a
    random pick. It cannot answer the question that actually threatens this
    work, which is whether *fitting a model* to eight features on eighteen
    monthly folds and reporting the best of several candidates manufactures an
    edge on its own. Permuting the label and re-running the fit answers exactly
    that: every draw pays the same price in search that the real run paid, and
    the only thing removed is the link between a purchase and its outcome.

    Shuffling within the month rather than across the sample keeps each month's
    return distribution intact, so the null is "this model cannot tell these
    purchases apart", not the far weaker "months differ".
    """
    label = label_column(horizon)
    rng = np.random.default_rng(seed)
    work = frame.reset_index(drop=True)
    months = month_of(work).to_numpy()
    blocks = [np.flatnonzero(months == m) for m in pd.unique(months)]
    original = work[label].to_numpy(dtype="float64").copy()

    out = []
    for _ in range(draws):
        shuffled = original.copy()
        for positions in blocks:
            shuffled[positions] = rng.permutation(shuffled[positions])
        work[label] = shuffled
        scored = walk_forward(work, fitter, horizon)
        if scored.empty:
            continue
        stat = selection_alpha(scored, "oos", rate, horizon,
                               statistic=statistic, risk_matched=risk_matched)
        if stat.mean is not None:
            out.append(stat.mean)
    return np.array(out)


def amputation_curve(scored: pd.DataFrame, column: str = "oos",
                     drops: Sequence[int] = (0, 1, 3, 5, 10, 20),
                     draws: int = 300, rate: float = 0.10,
                     horizon: int = PRIMARY_HORIZON,
                     seed: int = 20260830) -> pd.DataFrame:
    """
    What survives when the biggest-contributing tickers are removed, against a
    null that has had the same thing done to it.

    Removing the names that contributed most destroys any strategy with skewed
    returns, including a real one, so the raw curve reads as fragility whatever
    the truth is. Ranking purchases by distance below the 52-week high falls
    from +11.13 to -0.10 once twenty tickers of a hundred and eighty are cut,
    which looks fatal until random rankings are cut the same way and fall from
    -0.07 to -2.37. The percentile against that matched null is the number that
    means something.
    """
    label = label_column(horizon)
    work = scored.copy()
    work["month"] = month_of(work)

    def curve(col: str) -> list[Optional[float]]:
        picks = []
        for _month, block in work.groupby("month"):
            if len(block) < MIN_MONTH_ROWS:
                continue
            picks.append(block.nlargest(max(2, int(round(len(block) * rate))), col))
        if not picks:
            return [None] * len(drops)
        contrib = pd.concat(picks).groupby("ticker")[label].sum() \
            .sort_values(ascending=False)
        out = []
        for n in drops:
            sub = work[~work["ticker"].isin(set(contrib.head(n).index))]
            out.append(selection_alpha(sub, col, rate, horizon,
                                       risk_matched=True).mean)
        return out

    real = curve(column)
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(draws):
        work["_r"] = rng.random(len(work))
        null.append(curve("_r"))
    null = np.array([[np.nan if v is None else v for v in row] for row in null])

    rows = []
    for i, n in enumerate(drops):
        col = null[:, i]
        col = col[np.isfinite(col)]
        value = real[i]
        rows.append({
            "dropped": n,
            "alpha": value,
            "null_p50": float(np.percentile(col, 50)) if col.size else None,
            "null_p95": float(np.percentile(col, 95)) if col.size else None,
            "percentile": float((col < value).mean() * 100)
            if col.size and value is not None else None,
        })
    return pd.DataFrame(rows)


def percentile_of(value: Optional[float], draws: np.ndarray) -> Optional[float]:
    if value is None or draws.size == 0:
        return None
    return float((draws < value).mean() * 100)


def score_of(column: str) -> Fitter:
    """A fitter that ignores training and reads a column already on the frame."""
    def fitter(_train: pd.DataFrame, _label: str) -> Scorer:
        return lambda frame: pd.to_numeric(frame[column], errors="coerce").to_numpy()
    return fitter


def feature_fitter(column: str, sign: float = 1.0) -> Fitter:
    """A single raw feature used as the whole ranking, for the ablation table."""
    def fitter(_train: pd.DataFrame, _label: str) -> Scorer:
        def score(frame: pd.DataFrame) -> np.ndarray:
            values = pd.to_numeric(frame.get(column), errors="coerce").to_numpy(dtype="float64")
            return sign * np.where(np.isfinite(values), values, np.nan)
        return score
    return fitter


def stable_across_folds(frame: pd.DataFrame, columns: Sequence[str],
                        horizon: int = PRIMARY_HORIZON,
                        max_drift: float = 0.5) -> list[str]:
    """
    Features whose average holds still from the first fold's training window to
    the last fold's prediction window.

    The drift guard in `protocol.stable_features` compares two fixed splits.
    Walk-forward refits every month, so the comparison that matters is between
    the earliest thing a model is fitted on and the latest thing it is asked to
    score. `first_purchase_unverifiable` fires on 62% of the earliest window and
    2% of the latest, because it is a fact about how far back ingest reaches.
    """
    made = folds(frame, horizon)
    if len(made) < 2:
        return list(columns)
    first, last = made[0].train, made[-1].predict
    keep = []
    for name in columns:
        if name not in first.columns or name not in last.columns:
            continue
        a = pd.to_numeric(first[name], errors="coerce")
        b = pd.to_numeric(last[name], errors="coerce")
        sd = a.std()
        if not np.isfinite(sd) or sd <= 1e-12:
            continue
        if abs(b.mean() - a.mean()) / sd <= max_drift:
            keep.append(name)
    return keep
