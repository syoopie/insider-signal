"""
The three candidate model forms, and the fitting they share.

  A  recalibrated additive score — the current point system, weights refitted
  B  regularised logistic regression on standardised features
  C  gradient-boosted trees, as a ceiling check only

The plan recommends B for production and C only to learn what the achievable
ceiling looks like. The training split holds a few thousand observations
clustered into far fewer independent cells, and a model with tree capacity will
fit noise the protocol cannot reliably catch. B also keeps coefficients
readable, which matters because /how-it-works explains the model to a person.

Everything is fitted on the training split alone. Standardisation constants,
winsorisation quantiles and the ridge penalty all come from training and are
then applied unchanged to validation and test. Recomputing them on the split
being scored is a leak, and a quiet one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from src.research.estimate import logistic_score, ols_clustered, ridge_logistic
from src.research.features import log_scale, winsorize


@dataclass(frozen=True)
class Standardizer:
    """
    Column means and standard deviations, learned once on training data.

    Kept as an object rather than recomputed per split because recomputing is
    the leak: scoring validation with validation's own mean tells the model
    something about the period it is being tested on.
    """
    columns: list[str]
    means: np.ndarray
    sds: np.ndarray
    clips: dict[str, tuple[float, float]]

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        prepared = log_scale(frame)
        vectors = []
        for i, name in enumerate(self.columns):
            values = pd.to_numeric(prepared.get(name), errors="coerce").to_numpy(dtype="float64")
            lo, hi = self.clips[name]
            values = np.clip(values, lo, hi)
            values = np.where(np.isfinite(values), values, self.means[i])
            vectors.append((values - self.means[i]) / self.sds[i])
        return np.column_stack(vectors) if vectors else np.empty((len(frame), 0))


def fit_standardizer(train: pd.DataFrame, columns: Sequence[str],
                     lower: float = 0.01, upper: float = 0.99) -> Standardizer:
    """Learn clips, means and scales from the training split only."""
    prepared = winsorize(log_scale(train), columns, lower, upper)
    kept, means, sds, clips = [], [], [], {}
    raw = log_scale(train)
    for name in columns:
        if name not in prepared.columns:
            continue
        values = pd.to_numeric(prepared[name], errors="coerce")
        if values.notna().sum() < 30:
            continue
        mean = float(values.mean())
        sd = float(values.std())
        if not np.isfinite(sd) or sd <= 1e-12:
            continue
        source = pd.to_numeric(raw[name], errors="coerce")
        clips[name] = (float(source.quantile(lower)), float(source.quantile(upper)))
        kept.append(name)
        means.append(mean)
        sds.append(sd)
    return Standardizer(kept, np.array(means), np.array(sds), clips)


@dataclass
class FittedModel:
    name: str
    standardizer: Standardizer
    beta: np.ndarray
    kind: str  # "logistic" | "linear"

    def raw_score(self, frame: pd.DataFrame) -> np.ndarray:
        X = self.standardizer.transform(frame)
        if self.kind == "logistic":
            return logistic_score(X, self.beta)
        return np.column_stack([np.ones(len(X)), X]) @ self.beta

    def points(self, frame: pd.DataFrame, reference: Optional[np.ndarray] = None) -> np.ndarray:
        """
        The raw score mapped onto 0-100 by its percentile in `reference`.

        A percentile map is what makes the number a ranking again. The current
        score tops out at 61 against a threshold of 60, so it is a conjunction
        wearing a scale; anything replacing it has to spread across the range it
        claims to use.
        """
        values = self.raw_score(frame)
        base = reference if reference is not None else values
        ranks = np.searchsorted(np.sort(base), values, side="right") / max(len(base), 1)
        return np.clip(ranks * 100.0, 0.0, 100.0)

    def coefficient_table(self) -> pd.DataFrame:
        return pd.DataFrame({
            "feature": self.standardizer.columns,
            "beta": self.beta[1:],
        }).sort_values("beta", key=lambda s: s.abs(), ascending=False)


def fit_logistic(train: pd.DataFrame, columns: Sequence[str], label: str,
                 alpha: float) -> Optional[FittedModel]:
    """Model B. Predicts P(excess return > 0)."""
    standardizer = fit_standardizer(train, columns)
    if not standardizer.columns:
        return None
    X = standardizer.transform(train)
    y = (train[label].to_numpy(dtype="float64") > 0).astype("float64")
    beta = ridge_logistic(X, y, alpha=alpha)
    if beta is None:
        return None
    return FittedModel(f"logistic(alpha={alpha:g})", standardizer, beta, "logistic")


def fit_linear(train: pd.DataFrame, columns: Sequence[str], label: str,
               clusters: Optional[np.ndarray] = None) -> Optional[FittedModel]:
    """
    Model A's engine. Predicts the excess return itself.

    Coefficients come from the same clustered OLS the factor table uses, so the
    weights a recalibrated point system would carry are the weights that were
    reported as significant, rather than a second set fitted a different way.
    """
    standardizer = fit_standardizer(train, columns)
    if not standardizer.columns:
        return None
    X = standardizer.transform(train)
    y = train[label].to_numpy(dtype="float64")
    groups = clusters if clusters is not None else train["ticker"].to_numpy()
    coefficients = ols_clustered(X, y, groups, standardizer.columns)
    if not coefficients:
        return None
    intercept = float(y.mean())
    beta = np.concatenate([[intercept], np.array([c.beta for c in coefficients])])
    return FittedModel("linear", standardizer, beta, "linear")


@dataclass(frozen=True)
class RankModel:
    """
    Features mapped through a training-set empirical CDF, then weighted linearly.

    Two problems this solves at once. Levels drift across the sample, and a
    weight fitted on one level regime is reading a clock; ranks are invariant to
    any monotone shift. And the metric being optimised is a within-month
    ordering, so fitting on the raw excess return spends most of its effort on
    the month effect, which no ranking can capture.

    The empirical CDF comes from training only. Ranking a purchase against the
    other purchases of its own month would be the natural cross-sectional
    transform and is what quant equity does, but it would need the rest of that
    month's filings to score the first one, which is a look-ahead the deployed
    pipeline could not reproduce.
    """
    columns: list[str]
    knots: dict[str, np.ndarray]
    beta: np.ndarray

    def _ranked(self, frame: pd.DataFrame) -> np.ndarray:
        prepared = log_scale(frame)
        vectors = []
        for name in self.columns:
            values = pd.to_numeric(prepared.get(name), errors="coerce").to_numpy(dtype="float64")
            grid = self.knots[name]
            ranks = np.searchsorted(grid, values, side="right") / max(len(grid), 1)
            vectors.append(np.where(np.isfinite(values), ranks, 0.5) - 0.5)
        return np.column_stack(vectors) if vectors else np.empty((len(frame), 0))

    def raw_score(self, frame: pd.DataFrame) -> np.ndarray:
        return self._ranked(frame) @ self.beta

    def coefficient_table(self) -> pd.DataFrame:
        return pd.DataFrame({"feature": self.columns, "beta": self.beta}) \
            .sort_values("beta", key=lambda s: s.abs(), ascending=False)


def fit_rank_model(train: pd.DataFrame, columns: Sequence[str], label: str,
                   alpha: float = 10.0) -> Optional[RankModel]:
    """Ridge on rank-transformed features, targeting the within-month rank of the label."""
    prepared = log_scale(train)
    kept, knots = [], {}
    for name in columns:
        if name not in prepared.columns:
            continue
        values = pd.to_numeric(prepared[name], errors="coerce").dropna()
        if len(values) < 30 or values.nunique() < 3:
            continue
        kept.append(name)
        knots[name] = np.sort(values.to_numpy(dtype="float64"))
    if not kept:
        return None

    model = RankModel(kept, knots, np.zeros(len(kept)))
    X = model._ranked(train)
    months = pd.to_datetime(train["exec_date"]).dt.to_period("M")
    y = train.groupby(months)[label].rank(pct=True).to_numpy(dtype="float64") - 0.5
    good = np.isfinite(y)
    if good.sum() < 50:
        return None
    X, y = X[good], y[good]
    gram = X.T @ X + alpha * np.eye(X.shape[1])
    try:
        beta = np.linalg.solve(gram, X.T @ y)
    except np.linalg.LinAlgError:
        return None
    return RankModel(kept, knots, beta)


def fit_linear_significant_only(train: pd.DataFrame, columns: Sequence[str],
                                label: str) -> Optional[FittedModel]:
    """
    Model A, restricted to features that survived multiple-comparison correction.

    Fitting on everything and shipping everything is how 27 candidates became a
    weight table. Anything that did not clear the false-discovery rate gets a
    weight of zero, which is the honest encoding of "we could not measure it".
    """
    standardizer = fit_standardizer(train, columns)
    if not standardizer.columns:
        return None
    X = standardizer.transform(train)
    y = train[label].to_numpy(dtype="float64")
    coefficients = ols_clustered(X, y, train["ticker"].to_numpy(), standardizer.columns)
    if not coefficients:
        return None
    kept = [c.name for c in coefficients if c.significant]
    if not kept:
        return None
    return fit_linear(train, kept, label)
