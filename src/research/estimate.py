"""
Estimation: multivariate, clustered, and corrected for multiple comparisons.

Everything the old tuning did wrong in one place, done the other way.

Univariate lift, `avg(return | factor) - avg(return | no factor)`, attributes
shared variance to every correlated factor at once. `cap_small` and
`role_director` co-occur on three quarters of stored signals, so both were
credited with the same effect. Regression separates them.

Standard errors were never computed at all, so a factor measured on two
observations sat in the same table as one measured on a thousand. Errors here
are clustered on ticker, because overlapping holds on one name are not
independent draws.

And 27 candidate factors were screened over five rounds against one sample with
no correction, which at a nominal 5% is more than one false positive expected by
construction. Benjamini-Hochberg is applied to the whole candidate set at once.

Implemented on numpy rather than statsmodels: it is a hundred lines, the project
has no scientific stack, and CI installs with --no-dev.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

RIDGE_DEFAULT = 1.0
IRLS_MAX_ITER = 60
IRLS_TOL = 1e-8


@dataclass(frozen=True)
class Coefficient:
    name: str
    beta: float
    se: float
    t: float
    p: float
    p_adjusted: float
    n_nonzero: int

    @property
    def significant(self) -> bool:
        return self.p_adjusted < 0.05

    def line(self) -> str:
        mark = "  *" if self.significant else "   "
        return (f"  {self.name:<28} beta={self.beta:+8.3f}  se={self.se:6.3f}  "
                f"t={self.t:+6.2f}  p={self.p:6.4f}  p_adj={self.p_adjusted:6.4f}  "
                f"n={self.n_nonzero:>5}{mark}")


def normal_sf(z: float) -> float:
    """Two-sided tail probability of the standard normal."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def standardize(frame: pd.DataFrame, columns: Sequence[str]) -> tuple[np.ndarray, list[str]]:
    """
    Zero mean, unit variance, missing filled with the column mean.

    Standardising is what makes coefficients comparable: raw `cluster_dollars`
    is in millions and `is_averaging_down` is 0 or 1, so unstandardised betas
    say more about units than about effect. A column with no variance is dropped
    rather than producing a singular fit.
    """
    kept, vectors = [], []
    for name in columns:
        if name not in frame.columns:
            continue
        values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype="float64")
        values = np.where(np.isfinite(values), values, np.nan)
        if np.all(np.isnan(values)):
            continue
        mean = np.nanmean(values)
        values = np.where(np.isnan(values), mean, values)
        sd = values.std()
        if sd <= 1e-12:
            continue
        kept.append(name)
        vectors.append((values - mean) / sd)
    if not kept:
        return np.empty((len(frame), 0)), []
    return np.column_stack(vectors), kept


def _with_intercept(X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X])


def ols_clustered(X: np.ndarray, y: np.ndarray, clusters: np.ndarray,
                  names: Sequence[str]) -> list[Coefficient]:
    """
    OLS with cluster-robust (CR1) standard errors.

    The sandwich groups residuals by cluster, so repeated holds on one ticker
    inflate the error rather than shrinking it. Without this, 8,000 overlapping
    observations on 1,300 tickers would report the precision of 8,000
    independent ones.
    """
    Xi = _with_intercept(X)
    n, k = Xi.shape
    beta, *_ = np.linalg.lstsq(Xi, y, rcond=None)
    resid = y - Xi @ beta

    xtx_inv = np.linalg.pinv(Xi.T @ Xi)
    meat = np.zeros((k, k))
    unique = pd.unique(clusters)
    for group in unique:
        mask = clusters == group
        xg = Xi[mask]
        ug = resid[mask]
        s = xg.T @ ug
        meat += np.outer(s, s)

    g = len(unique)
    if g < 2 or n <= k:
        return []
    correction = (g / (g - 1)) * ((n - 1) / (n - k))
    cov = xtx_inv @ meat @ xtx_inv * correction
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))

    raw = []
    for i, name in enumerate(names, start=1):
        s = se[i]
        t = beta[i] / s if s > 0 else 0.0
        raw.append((name, float(beta[i]), float(s), float(t), normal_sf(t),
                    int(np.count_nonzero(X[:, i - 1]))))

    adjusted = benjamini_hochberg([r[4] for r in raw])
    return [
        Coefficient(name, b, s, t, p, p_adj, nz)
        for (name, b, s, t, p, nz), p_adj in zip(raw, adjusted)
    ]


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """
    Step-up FDR adjustment. Controls the expected share of false discoveries.

    Bonferroni would be stricter but would reject almost everything at this
    sample size. The point is not to be harsh, it is to stop reading a table of
    27 raw p-values as if each stood alone.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = np.argsort(pvalues)
    adjusted = np.empty(m)
    running = 1.0
    for rank, idx in enumerate(reversed(order), start=1):
        position = m - rank + 1
        running = min(running, pvalues[idx] * m / position)
        adjusted[idx] = running
    return [float(x) for x in adjusted]


def ridge_logistic(X: np.ndarray, y: np.ndarray, alpha: float = RIDGE_DEFAULT,
                   max_iter: int = IRLS_MAX_ITER) -> Optional[np.ndarray]:
    """
    L2-penalised logistic regression by IRLS, intercept unpenalised.

    Chosen over gradient boosting for production because the training split has
    a few thousand observations clustered into far fewer independent cells, and
    a model with that much capacity will fit noise the protocol cannot reliably
    catch. Ridge also keeps the coefficients readable, which matters because
    /how-it-works explains the model to the user.
    """
    Xi = _with_intercept(X)
    n, k = Xi.shape
    beta = np.zeros(k)
    penalty = np.eye(k) * alpha
    penalty[0, 0] = 0.0

    for _ in range(max_iter):
        eta = np.clip(Xi @ beta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        z = eta + (y - mu) / w
        wx = Xi * w[:, None]
        try:
            new = np.linalg.solve(Xi.T @ wx + penalty, wx.T @ z)
        except np.linalg.LinAlgError:
            return None
        if np.max(np.abs(new - beta)) < IRLS_TOL:
            return new
        beta = new
    return beta


def logistic_score(X: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """Predicted probability from a fitted ridge_logistic beta."""
    eta = np.clip(_with_intercept(X) @ beta, -30, 30)
    return 1.0 / (1.0 + np.exp(-eta))


def auc(y_true: np.ndarray, scores: np.ndarray) -> Optional[float]:
    """Rank-based AUC. No sklearn, and ties handled by average rank."""
    positives = y_true == 1
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = pd.Series(scores).rank().to_numpy()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
