"""
As-of lookups into the price panel must never look forward.

Every point-in-time feature the scorer will read — 52-week high as of the trade
date, trailing momentum, realised volatility — is an index into a PanelSeries.
If that index can land on a bar dated after the transaction, the feature carries
information the insider did not have and the backtest measures nothing.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.market.panel import (
    PANEL_COLUMNS,
    PanelSeries,
    _frame_from_result,
    merge_panels,
)

# A deliberately gappy calendar: a weekend, then a holiday (2024-07-04).
_DATES = [date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3),
          date(2024, 7, 5), date(2024, 7, 8)]


def _series() -> PanelSeries:
    return PanelSeries(
        symbol="TEST",
        dates=np.array(_DATES, dtype="datetime64[D]"),
        close=np.array([10.0, 11.0, 12.0, 13.0, 14.0]),
        adj_close=np.array([9.0, 9.9, 10.8, 11.7, 12.6]),
        volume=np.array([100.0] * 5),
    )


def test_as_of_lands_on_the_same_day_when_it_traded():
    assert _series().index_as_of(date(2024, 7, 2)) == 1


def test_as_of_never_looks_past_a_closed_market():
    """July 4 is a holiday. The answer is July 3, never July 5."""
    s = _series()
    i = s.index_as_of(date(2024, 7, 4))
    assert s.dates[i] == np.datetime64("2024-07-03")


def test_as_of_over_a_weekend_returns_the_friday():
    s = _series()
    i = s.index_as_of(date(2024, 7, 7))
    assert s.dates[i] == np.datetime64("2024-07-05")


def test_as_of_before_the_series_starts_has_no_answer():
    assert _series().index_as_of(date(2020, 1, 1)) == -1


def test_as_of_after_the_series_ends_returns_the_last_bar():
    s = _series()
    assert s.index_as_of(date(2030, 1, 1)) == len(s) - 1


def test_on_or_after_never_looks_back():
    s = _series()
    j = s.index_on_or_after(date(2024, 7, 4))
    assert s.dates[j] == np.datetime64("2024-07-05")
    assert s.index_on_or_after(date(2024, 7, 8)) == 4
    assert s.index_on_or_after(date(2030, 1, 1)) == -1


def test_refetching_a_symbol_replaces_its_rows_rather_than_duplicating():
    """A resumed or repeated build must converge, not accumulate."""
    first = pd.DataFrame({
        "symbol": ["A", "A"], "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "close": [1.0, 2.0], "adj_close": [1.0, 2.0], "volume": [10.0, 10.0],
    })[PANEL_COLUMNS]
    other = pd.DataFrame({
        "symbol": ["B"], "date": pd.to_datetime(["2024-01-02"]),
        "close": [5.0], "adj_close": [5.0], "volume": [10.0],
    })[PANEL_COLUMNS]

    panel = merge_panels(None, [first, other])
    assert len(panel) == 3

    again = merge_panels(panel, [first])
    assert len(again) == 3
    assert set(again["symbol"]) == {"A", "B"}


def test_missing_adjclose_falls_back_to_raw_closes():
    """Dropping a symbol for a missing adjclose array would lose the observation."""
    result = {
        "timestamp": [1719802800, 1719889200],
        "indicators": {"quote": [{"close": [10.0, 11.0], "volume": [1.0, 2.0]}]},
    }
    frame = _frame_from_result("TEST", result)
    assert list(frame["adj_close"]) == [10.0, 11.0]


def test_a_result_with_no_bars_is_not_an_empty_frame():
    assert _frame_from_result("TEST", {"timestamp": []}) is None


@pytest.mark.parametrize("probe,expected", [
    (date(2024, 7, 1), "2024-07-01"),
    (date(2024, 7, 3), "2024-07-03"),
    (date(2024, 7, 6), "2024-07-05"),
])
def test_as_of_is_monotone(probe, expected):
    s = _series()
    assert s.dates[s.index_as_of(probe)] == np.datetime64(expected)
