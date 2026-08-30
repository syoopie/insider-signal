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
from src.research.models import fit_logistic
from src.research.walkforward import Fitter, Scorer, score_of

RAW_TERMS_PRESENT = ["pct_holdings_increase", "total_value"]


def ridge(columns: Sequence[str], alpha: float) -> Fitter:
    """Regularised logistic on P(excess > 0), refitted every fold."""
    def fitter(train: pd.DataFrame, label: str) -> Optional[Scorer]:
        model = fit_logistic(train, list(columns), label, alpha)
        if model is None:
            return None
        return model.raw_score
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
}
