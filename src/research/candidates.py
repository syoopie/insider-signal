"""
The rankings being raced, each one a function from a training fold to a scorer.

Registering them here rather than inside the harness keeps the ruler frozen
while the hypotheses change. `scripts/hillclimb.py` imports this dict and knows
nothing else about what it is measuring.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from src.research.features import ALL_CANDIDATES, CURRENT_FACTORS, TIER1, TIER2
from src.research.models import fit_logistic, fit_rank_model
from src.signals.discount import discount_score
from src.research.walkforward import Fitter, Scorer, feature_fitter, score_of

RAW_TERMS_PRESENT = ["pct_holdings_increase", "total_value"]

# Everything except the timing factors, whose prevalence tracks how far back
# ingest reaches rather than anything an insider did.
STABLE_ALL = [c for c in ALL_CANDIDATES
              if not c.startswith(("f_first_purchase", "f_prior_purchase",
                                   "f_sequenced_buying"))]

DISCOUNT = "tx_pct_below_52wk_high"

# What the filing itself says, with nothing about the price. Kept separate so a
# gated model can be asked whether insider detail adds anything once the price
# screen has already fired.
INSIDER_ONLY = [c for c in CURRENT_FACTORS if not c.startswith("f_cap")] + TIER1 + [
    "pct_holdings_increase", "total_value",
]


def ridge(columns: Sequence[str], alpha: float) -> Fitter:
    """Regularised logistic on P(excess > 0), refitted every fold."""
    def fitter(train: pd.DataFrame, label: str) -> Optional[Scorer]:
        model = fit_logistic(train, list(columns), label, alpha)
        if model is None:
            return None
        return model.raw_score
    return fitter


def rank(columns: Sequence[str], alpha: float = 10.0) -> Fitter:
    """Ridge on rank-transformed features, targeting the within-month label rank."""
    def fitter(train: pd.DataFrame, label: str) -> Optional[Scorer]:
        model = fit_rank_model(train, list(columns), label, alpha)
        if model is None:
            return None
        return model.raw_score
    return fitter


def tail_gate(column: str, quantile: float = 0.90,
              inner: Optional[Sequence[str]] = None,
              alpha: float = 10.0) -> Fitter:
    """
    A hard gate at a training-set quantile, then a ranking inside the gate.

    The shape the data actually has. Within-month deciles of distance below the
    52-week high are flat from the 1st to the 9th, mean between -0.8% and +2.9%
    and a median that is negative in all of them, and then the 10th jumps to
    +17.5% mean, +6.6% median and a 57.7% hit rate against about 44% everywhere
    else. That is a threshold, not a slope, which is why every rank-transformed
    linear model here scores zero: it spends its capacity on the nine deciles
    where there is nothing to order.

    The cutoff comes from the training fold, so it moves with the sample rather
    than being pinned to a number chosen after looking.
    """
    def fitter(train: pd.DataFrame, label: str) -> Optional[Scorer]:
        values = pd.to_numeric(train.get(column), errors="coerce").dropna()
        if len(values) < 100:
            return None
        cutoff = float(values.quantile(quantile))
        model = fit_rank_model(train, list(inner), label, alpha) if inner else None

        def score(frame: pd.DataFrame) -> np.ndarray:
            gate = pd.to_numeric(frame.get(column), errors="coerce").to_numpy(dtype="float64")
            passes = np.where(np.isfinite(gate), gate >= cutoff, False).astype("float64")
            if model is None:
                # Rank inside the gate by how far past it the purchase sits.
                depth = np.where(np.isfinite(gate), gate, cutoff) - cutoff
                return passes * 1e6 + depth
            within = np.asarray(model.raw_score(frame), dtype="float64")
            return passes * 1e6 + np.nan_to_num(within)
        return score
    return fitter


def shipped_scorer() -> Fitter:
    """
    The production scoring function, run over the research frame.

    Not a re-implementation of the winning ranking but a call into
    `src/signals/discount.py`, so the harness measures what ingest will actually
    write. If the shipped number ever stops matching the researched one, this is
    where it shows up.
    """
    def fitter(_train: pd.DataFrame, _label: str) -> Scorer:
        def score(frame: pd.DataFrame) -> np.ndarray:
            values = pd.to_numeric(frame.get(DISCOUNT), errors="coerce")
            return np.array([
                np.nan if (v is None or v != v) else float(discount_score(v))
                for v in values
            ])
        return score
    return fitter


def only_where(inner: Fitter, column: str, keep: Sequence) -> Fitter:
    """`inner`, but nothing outside `keep` can ever be picked."""
    allowed = set(keep)

    def fitter(train: pd.DataFrame, label: str) -> Optional[Scorer]:
        scorer = inner(train, label)
        if scorer is None:
            return None

        def score(frame: pd.DataFrame) -> np.ndarray:
            values = np.asarray(scorer(frame), dtype="float64")
            eligible = frame[column].isin(allowed).to_numpy() \
                if column in frame.columns else np.ones(len(frame), dtype=bool)
            return np.where(eligible, values, -np.inf)
        return score
    return fitter


def constant() -> Fitter:
    def fitter(_train: pd.DataFrame, _label: str) -> Scorer:
        return lambda frame: np.zeros(len(frame))
    return fitter


def noise(seed: int = 7) -> Fitter:
    rng = np.random.default_rng(seed)

    def fitter(_train: pd.DataFrame, _label: str) -> Scorer:
        return lambda frame: rng.random(len(frame))
    return fitter


CANDIDATES: dict[str, Fitter] = {
    "current score": score_of("score"),
    "noise": noise(),
    "ridge current a=10": ridge(CURRENT_FACTORS, 10.0),
    "ridge all a=10": ridge(ALL_CANDIDATES, 10.0),
    "ridge tier1 a=10": ridge(TIER1, 10.0),
    "ridge tier2 a=10": ridge(TIER2, 10.0),
    "below 52wk high": feature_fitter("tx_pct_below_52wk_high"),
    "rank tier2": rank(TIER2),
    "rank tier1+2": rank(TIER1 + TIER2),
    "rank stable all": rank(STABLE_ALL),
    "gate p90": tail_gate(DISCOUNT, 0.90),
    "gate p80": tail_gate(DISCOUNT, 0.80),
    "gate p95": tail_gate(DISCOUNT, 0.95),
    "gate p90 + insiders": tail_gate(DISCOUNT, 0.90, INSIDER_ONLY),
    "gate p90 + current score": tail_gate(DISCOUNT, 0.90, ["score"]),
    "discount, small cap only": only_where(
        feature_fitter(DISCOUNT), "cap_tier", ["small"]),
    "discount, not large cap": only_where(
        feature_fitter(DISCOUNT), "cap_tier", ["small", "mid", "unknown"]),
    "ridge discount+trend+liquidity": ridge(
        [DISCOUNT, "tx_ret_252d", "tx_dollar_vol_21d"], 10.0),
    "ridge discount alone": ridge([DISCOUNT], 10.0),
    "SHIPPED scorer": shipped_scorer(),
}
