"""
The estimators are hand-rolled on numpy, so they get pinned against known answers.

The whole point of Phase 5 is that the previous procedure could not tell signal
from noise. An estimator with a bug in it would be the same failure wearing
better clothes.
"""
import numpy as np
import pytest

from src.research.estimate import (
    auc,
    benjamini_hochberg,
    logistic_score,
    normal_sf,
    ols_clustered,
    ridge_logistic,
    standardize,
)
import pandas as pd


# ── OLS ──────────────────────────────────────────────────────────────────────

def test_ols_recovers_a_known_slope():
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = 3.0 * x + rng.normal(scale=0.1, size=400)
    coefs = ols_clustered(x.reshape(-1, 1), y, np.arange(400).astype(str), ["x"])
    assert coefs[0].beta == pytest.approx(3.0, abs=0.05)


def test_ols_finds_nothing_in_pure_noise():
    rng = np.random.default_rng(1)
    x = rng.normal(size=500)
    y = rng.normal(size=500)
    coefs = ols_clustered(x.reshape(-1, 1), y, np.arange(500).astype(str), ["x"])
    assert abs(coefs[0].t) < 3.0


def test_clustering_widens_the_error_when_errors_share_a_cluster():
    """
    Fifty tickers, ten overlapping holds each, with a shock common to each
    ticker. Treating the 500 rows as independent understates the error; that is
    exactly how 8,000 overlapping observations on 1,300 tickers would report the
    precision of 8,000 independent ones.
    """
    rng = np.random.default_rng(2)
    n_clusters, per_cluster = 50, 10
    groups = np.repeat([f"T{i}" for i in range(n_clusters)], per_cluster)
    n = n_clusters * per_cluster
    # Both the regressor and the error carry a ticker-level component, which is
    # the real case: cap_tier is a property of the ticker, and so is whatever
    # moved that ticker over the holding window.
    x = np.repeat(rng.normal(size=n_clusters), per_cluster) + rng.normal(scale=0.2, size=n)
    shock = np.repeat(rng.normal(scale=3.0, size=n_clusters), per_cluster)
    y = 1.5 * x + shock + rng.normal(scale=0.5, size=n)

    naive = ols_clustered(x.reshape(-1, 1), y,
                          np.arange(len(x)).astype(str), ["x"])
    clustered = ols_clustered(x.reshape(-1, 1), y, groups, ["x"])
    assert clustered[0].se > naive[0].se


def test_ols_needs_at_least_two_clusters():
    x = np.arange(10, dtype=float)
    y = x * 2
    assert ols_clustered(x.reshape(-1, 1), y, np.array(["A"] * 10), ["x"]) == []


# ── multiple comparisons ─────────────────────────────────────────────────────

def test_benjamini_hochberg_never_lowers_a_p_value():
    raw = [0.001, 0.01, 0.03, 0.2, 0.5]
    adjusted = benjamini_hochberg(raw)
    assert all(a >= r for a, r in zip(adjusted, raw))


def test_benjamini_hochberg_is_monotone_in_rank():
    raw = [0.001, 0.008, 0.02, 0.04, 0.9]
    adjusted = benjamini_hochberg(raw)
    ordered = [adjusted[i] for i in np.argsort(raw)]
    assert ordered == sorted(ordered)


def test_twenty_null_tests_do_not_all_survive():
    """At a nominal 5%, one in twenty passes by chance. That is the thing to stop."""
    raw = [0.04] + [0.5] * 19
    assert benjamini_hochberg(raw)[0] > 0.05


def test_a_genuinely_strong_result_still_survives_correction():
    raw = [1e-8] + [0.5] * 19
    assert benjamini_hochberg(raw)[0] < 0.05


def test_empty_input_is_handled():
    assert benjamini_hochberg([]) == []


# ── standardisation ──────────────────────────────────────────────────────────

def test_standardize_centres_and_scales():
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0]})
    X, names = standardize(frame, ["a"])
    assert names == ["a"]
    assert X[:, 0].mean() == pytest.approx(0.0, abs=1e-12)
    assert X[:, 0].std() == pytest.approx(1.0)


def test_a_constant_column_is_dropped_not_divided_by_zero():
    frame = pd.DataFrame({"a": [1.0, 1.0, 1.0], "b": [1.0, 2.0, 3.0]})
    _, names = standardize(frame, ["a", "b"])
    assert names == ["b"]


def test_missing_values_take_the_column_mean():
    frame = pd.DataFrame({"a": [1.0, None, 3.0]})
    X, _ = standardize(frame, ["a"])
    assert np.isfinite(X).all()


def test_an_all_missing_column_is_dropped():
    frame = pd.DataFrame({"a": [None, None], "b": [1.0, 2.0]})
    _, names = standardize(frame, ["a", "b"])
    assert names == ["b"]


# ── logistic ─────────────────────────────────────────────────────────────────

def test_ridge_logistic_separates_a_clean_signal():
    rng = np.random.default_rng(3)
    x = rng.normal(size=600)
    y = (x + rng.normal(scale=0.3, size=600) > 0).astype(float)
    beta = ridge_logistic(x.reshape(-1, 1), y, alpha=0.1)
    assert beta is not None
    assert beta[1] > 0
    assert auc(y, logistic_score(x.reshape(-1, 1), beta)) > 0.85


def test_a_stronger_penalty_shrinks_the_coefficient():
    rng = np.random.default_rng(4)
    x = rng.normal(size=400)
    y = (x > 0).astype(float)
    weak = ridge_logistic(x.reshape(-1, 1), y, alpha=0.1)
    strong = ridge_logistic(x.reshape(-1, 1), y, alpha=100.0)
    assert abs(strong[1]) < abs(weak[1])


def test_predicted_probabilities_stay_in_range():
    rng = np.random.default_rng(5)
    x = rng.normal(size=200) * 50
    y = (x > 0).astype(float)
    p = logistic_score(x.reshape(-1, 1), ridge_logistic(x.reshape(-1, 1), y))
    assert p.min() >= 0.0 and p.max() <= 1.0


# ── auc ──────────────────────────────────────────────────────────────────────

def test_auc_of_a_perfect_ranking_is_one():
    assert auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)


def test_auc_of_a_reversed_ranking_is_zero():
    assert auc(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(0.0)


def test_auc_needs_both_classes():
    assert auc(np.array([1, 1, 1]), np.array([0.1, 0.2, 0.3])) is None


def test_normal_tail_matches_known_values():
    assert normal_sf(1.959964) == pytest.approx(0.05, abs=1e-4)
    assert normal_sf(0.0) == pytest.approx(1.0)
