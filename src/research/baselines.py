"""
The four things any proposed model has to beat, out of sample.

None of these are strawmen. "Buy every eligible insider purchase" is a real
strategy with real published support, and a scoring model that cannot beat it is
adding complexity for nothing. Measuring against it was impossible until the
negative class existed, which is why five rounds of tuning never did.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.protocol import PRIMARY_HORIZON, Stat, summarise, top_k_by

RANDOM_SEED = 20260830


def all_eligible(frame: pd.DataFrame) -> pd.DataFrame:
    """Every eligible purchase, equal weight. The 'no model' baseline."""
    return frame


def small_cap_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Lakonishok & Lee's headline cut, applied without any scoring."""
    return frame[frame["cap_tier"] == "small"]


def current_model(frame: pd.DataFrame, k: int) -> pd.DataFrame:
    """The model as it stands today, at the same selection count as a challenger."""
    return top_k_by(frame, "score", k)


def random_ranking(frame: pd.DataFrame, k: int, seed: int = RANDOM_SEED) -> pd.DataFrame:
    """
    A random pick of the same size.

    The floor a ranking has to clear. With a fat-tailed return distribution a
    lucky draw of 300 can look like skill, so this is the reminder of how much
    of any result could be that.
    """
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(frame))[:k]
    return frame.iloc[idx]


def evaluate_baselines(frame: pd.DataFrame, k: int,
                       horizon: int = PRIMARY_HORIZON) -> dict[str, Stat]:
    """
    All four, on the same split, at the same k where k applies.

    `k` is the number of trades a challenger selects. Two of the baselines take
    everything and ignore it; the comparison that matters is against the two
    that select the same count.
    """
    return {
        "all eligible purchases": summarise(all_eligible(frame), horizon),
        "small-cap only": summarise(small_cap_only(frame), horizon),
        f"current model, top {k}": summarise(current_model(frame, k), horizon),
        f"random ranking, {k}": summarise(random_ranking(frame, k), horizon),
    }


def random_ranking_distribution(frame: pd.DataFrame, k: int, draws: int = 500,
                                horizon: int = PRIMARY_HORIZON,
                                seed: int = RANDOM_SEED) -> pd.Series:
    """
    Mean excess return of `draws` random selections of size k.

    Gives a challenger's mean a percentile to be read against, which is a
    blunter and more honest test than a t-stat when the returns are this
    skewed.
    """
    col = f"excess_spy_{horizon}d"
    values = frame[col].dropna().to_numpy()
    rng = np.random.default_rng(seed)
    means = [rng.choice(values, size=min(k, len(values)), replace=False).mean()
             for _ in range(draws)]
    return pd.Series(means)
