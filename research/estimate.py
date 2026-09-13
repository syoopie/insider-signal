"""
The numerical pieces the research harness shares, on numpy.

Benjamini-Hochberg is what `research/scripts/gates.py` applies across its candidate set,
so a table of raw p-values is not read as if each stood alone. The ridge
logistic backs the regularised candidates in `candidates.py`.

Implemented on numpy rather than statsmodels: it is short, and the pipeline has
no scientific stack.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

RIDGE_DEFAULT = 1.0
IRLS_MAX_ITER = 60
IRLS_TOL = 1e-8


def normal_sf(z: float) -> float:
    """Two-sided tail probability of the standard normal."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def _with_intercept(X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X])


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """
    Step-up FDR adjustment. Controls the expected share of false discoveries.

    Bonferroni would be stricter but would reject almost everything at this
    sample size.
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
    """L2-penalised logistic regression by IRLS, intercept unpenalised."""
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
