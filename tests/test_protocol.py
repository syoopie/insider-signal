"""
The evaluation protocol's splits must not leak.

A 90-day hold opened the day before a boundary is still open well inside the
next split. If that row stays in training, the validation result is partly a
measurement of training data, and the whole protocol is decoration.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.research.protocol import (
    EMBARGO_DAYS,
    TEST_START,
    VALID_START,
    _cluster_se,
    decile_spread,
    decile_table,
    evaluable,
    feature_drift,
    split_bounds,
    split_frames,
    stable_features,
    summarise,
)


def _frame(exec_dates, excess=None, tickers=None, horizon=90):
    n = len(exec_dates)
    return pd.DataFrame({
        "exec_date": [pd.Timestamp(d) for d in exec_dates],
        "ticker": tickers if tickers is not None else [f"T{i%7}" for i in range(n)],
        "eligible": [True] * n,
        "scorer_disqualified": [False] * n,
        f"exit_in_future_{horizon}d": [False] * n,
        f"excess_spy_{horizon}d": excess if excess is not None else list(range(n)),
        "score": list(range(n)),
        "cap_tier": ["small"] * n,
    })


# ── purge ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("horizon", [30, 60, 90, 180])
def test_a_training_hold_always_closes_before_validation_opens(horizon):
    bounds = split_bounds(horizon)
    train_end = bounds["train"][1]
    assert train_end + timedelta(days=horizon) < VALID_START


@pytest.mark.parametrize("horizon", [30, 60, 90, 180])
def test_a_validation_hold_always_closes_before_test_opens(horizon):
    bounds = split_bounds(horizon)
    valid_end = bounds["validation"][1]
    assert valid_end + timedelta(days=horizon) < TEST_START


def test_a_longer_horizon_pulls_the_boundary_back_rather_than_leaking():
    assert split_bounds(180)["train"][1] < split_bounds(30)["train"][1]


def test_the_embargo_is_more_than_the_horizon_alone():
    bounds = split_bounds(90)
    slack = (VALID_START - bounds["train"][1]).days - 90
    assert slack >= EMBARGO_DAYS


def test_splits_never_share_a_row():
    dates = [date(2024, 10, 1) + timedelta(days=i * 11) for i in range(70)]
    splits = split_frames(_frame(dates))
    seen = [set(s.frame.index) for s in splits.values()]
    assert not (seen[0] & seen[1])
    assert not (seen[1] & seen[2])
    assert not (seen[0] & seen[2])


def test_splits_are_in_calendar_order():
    dates = [date(2024, 10, 1) + timedelta(days=i * 11) for i in range(70)]
    splits = split_frames(_frame(dates))
    train, valid, test = (splits[k].frame for k in ("train", "validation", "test"))
    if len(train) and len(valid):
        assert train["exec_date"].max() < valid["exec_date"].min()
    if len(valid) and len(test):
        assert valid["exec_date"].max() < test["exec_date"].min()


# ── evaluable ────────────────────────────────────────────────────────────────

def test_an_unfinished_hold_is_not_evaluable():
    frame = _frame([date(2026, 1, 1)] * 3)
    frame.loc[0, "exit_in_future_90d"] = True
    assert len(evaluable(frame)) == 2


def test_a_disqualified_purchase_is_not_evaluable():
    frame = _frame([date(2026, 1, 1)] * 3)
    frame.loc[0, "scorer_disqualified"] = True
    assert len(evaluable(frame)) == 2


def test_a_missing_label_is_not_evaluable():
    frame = _frame([date(2026, 1, 1)] * 3, excess=[1.0, None, 3.0])
    assert len(evaluable(frame)) == 2


# ── clustered standard errors ────────────────────────────────────────────────

def test_repeating_one_ticker_does_not_shrink_its_standard_error():
    """
    Ten holds on one ticker are not ten independent draws. A naive SE falls by
    sqrt(10); a clustered one must not.
    """
    values = np.array([5.0, -3.0, 7.0, -1.0, 2.0] * 4)
    one_ticker = np.array(["AAA"] * 20)
    many_tickers = np.array([f"T{i}" for i in range(20)])
    assert _cluster_se(values, one_ticker) is None or \
        _cluster_se(values, one_ticker) > _cluster_se(values, many_tickers)


def test_standard_error_needs_more_than_one_cluster():
    assert _cluster_se(np.array([1.0, 2.0, 3.0]), np.array(["A", "A", "A"])) is None


def test_summarise_reports_the_cluster_count_beside_n():
    frame = _frame([date(2026, 1, 1)] * 14, excess=[1.0] * 7 + [-1.0] * 7)
    stat = summarise(frame)
    assert stat.n == 14
    assert stat.n_tickers == 7
    assert stat.hit_rate == pytest.approx(50.0)


# ── ranking ──────────────────────────────────────────────────────────────────

def test_a_perfect_ranking_has_a_positive_decile_spread():
    n = 500
    frame = _frame([date(2026, 1, 1)] * n, excess=list(range(n)))
    frame["score"] = list(range(n))
    mean_spread, median_spread = decile_spread(decile_table(frame, "score"))
    assert mean_spread > 0
    assert median_spread > 0


def test_a_reversed_ranking_has_a_negative_decile_spread():
    n = 500
    frame = _frame([date(2026, 1, 1)] * n, excess=list(range(n)))
    frame["score"] = list(range(n))[::-1]
    mean_spread, _ = decile_spread(decile_table(frame, "score"))
    assert mean_spread < 0


def test_too_few_rows_produce_no_decile_table_rather_than_a_noisy_one():
    assert decile_table(_frame([date(2026, 1, 1)] * 20), "score").empty


# ── feature stability ────────────────────────────────────────────────────────

def test_a_feature_that_only_fires_in_one_split_is_dropped():
    """
    first_purchase_12mo never fired in training and fired on 46% of validation,
    because it depends on how far back the database reaches, not on the insider.
    A model fitted on that is reading a clock.
    """
    train = pd.DataFrame({"a": [0.0] * 100, "b": np.linspace(0, 1, 100)})
    valid = pd.DataFrame({"a": [1.0] * 100, "b": np.linspace(0, 1, 100)})
    keep, dropped = stable_features(train, valid, ["a", "b"])
    assert "a" not in keep
    assert "b" in keep


def test_a_stable_feature_survives():
    rng = np.random.default_rng(0)
    train = pd.DataFrame({"a": rng.normal(size=500)})
    valid = pd.DataFrame({"a": rng.normal(size=500)})
    keep, _ = stable_features(train, valid, ["a"])
    assert keep == ["a"]


def test_drift_is_measured_in_training_standard_deviations():
    train = pd.DataFrame({"a": [0.0, 2.0] * 50})       # mean 1, sd ~1
    valid = pd.DataFrame({"a": [3.0] * 100})           # mean 3
    drift = feature_drift(train, valid, ["a"])
    assert drift.iloc[0]["drift_sd"] == pytest.approx(2.0, abs=0.05)


def test_a_feature_constant_in_training_is_dropped_not_exempted():
    """The fit saw a constant, so it can carry no information about the column."""
    train = pd.DataFrame({"a": [1.0] * 50})
    valid = pd.DataFrame({"a": [5.0] * 50})
    assert feature_drift(train, valid, ["a"]).iloc[0]["drift_sd"] == float("inf")
    assert stable_features(train, valid, ["a"])[0] == []


def test_drift_on_no_shared_columns_returns_an_empty_table_not_a_crash():
    assert feature_drift(pd.DataFrame({"a": [1.0]}), pd.DataFrame({"b": [1.0]}),
                         ["a", "b"]).empty
