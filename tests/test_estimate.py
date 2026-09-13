"""
The estimators are hand-rolled on numpy, so they get pinned against known answers.
"""
import numpy as np
import pytest

from research.estimate import (
    benjamini_hochberg,
    logistic_score,
    normal_sf,
    ridge_logistic,
)


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


# ── logistic ─────────────────────────────────────────────────────────────────

def test_ridge_logistic_separates_a_clean_signal():
    rng = np.random.default_rng(3)
    x = rng.normal(size=600)
    y = (x + rng.normal(scale=0.3, size=600) > 0).astype(float)
    beta = ridge_logistic(x.reshape(-1, 1), y, alpha=0.1)
    assert beta is not None
    p = logistic_score(x.reshape(-1, 1), beta)
    assert ((p > 0.5) == (y == 1)).mean() > 0.85


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


def test_normal_tail_matches_known_values():
    assert normal_sf(1.959964) == pytest.approx(0.05, abs=1e-4)
    assert normal_sf(0.0) == pytest.approx(1.0)
