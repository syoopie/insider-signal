"""
Point-in-time features must not read a bar the insider had not seen.

The 52-week-low factor was removed from the scorer because it compared a
purchase price against *today's* low rather than the low as of the trade. These
tests pin the replacement to the trade date, and pin the two failure modes that
matter: too little history behind a "52-week" window, and a symbol whose series
stops mid-hold.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from src.market.features import price_context, price_on, window_return
from src.market.panel import PanelSeries


def _series(prices, start=date(2024, 1, 1), adj=None, volume=None):
    """A synthetic series on consecutive calendar days — gaps are tested elsewhere."""
    dates = np.array([start + timedelta(days=i) for i in range(len(prices))],
                     dtype="datetime64[D]")
    return PanelSeries(
        symbol="TEST",
        dates=dates,
        close=np.array(prices, dtype="float64"),
        adj_close=np.array(adj if adj is not None else prices, dtype="float64"),
        volume=np.array(volume if volume is not None else [1000.0] * len(prices)),
    )


# ── window_return ────────────────────────────────────────────────────────────

def test_window_return_is_measured_on_adjusted_closes():
    """Raw closes omit dividends; the return must come from adj_close."""
    s = _series([100.0, 110.0], adj=[100.0, 120.0])
    r = window_return(s, date(2024, 1, 1), date(2024, 1, 2))
    assert r.ok
    assert r.pct == pytest.approx(20.0)


def test_missing_symbol_is_distinct_from_a_delisting():
    assert window_return(None, date(2024, 1, 1), date(2024, 3, 1)).status == "no_symbol"


def test_a_series_that_stops_during_the_hold_reports_no_exit():
    """This is what a delisting looks like, and it is not the same as a bad fetch."""
    s = _series([10.0, 11.0, 12.0])
    r = window_return(s, date(2024, 1, 1), date(2024, 6, 1))
    assert r.status == "no_exit"


def test_a_series_that_starts_after_entry_reports_no_entry():
    s = _series([10.0, 11.0], start=date(2025, 1, 1))
    assert window_return(s, date(2024, 1, 1), date(2024, 3, 1)).status == "no_entry"


def test_a_bar_a_few_days_stale_still_stands_for_the_exit_date():
    """Long weekends and holidays are normal; only a real gap means delisted."""
    s = _series([10.0] * 5, start=date(2024, 1, 1))   # last bar 2024-01-05
    r = window_return(s, date(2024, 1, 1), date(2024, 1, 8))
    assert r.status == "ok"


def test_exit_uses_the_last_bar_on_or_before_the_exit_date():
    s = _series([10.0, 11.0, 12.0, 99.0])
    r = window_return(s, date(2024, 1, 1), date(2024, 1, 3))
    assert r.pct == pytest.approx(20.0)   # 10 -> 12, never the 99 on day four


# ── price_context ────────────────────────────────────────────────────────────

def test_context_reads_no_bar_after_the_trade_date():
    """The spike on day four must not enter a 52-week high taken on day three."""
    s = _series([10.0, 12.0, 11.0, 500.0])
    ctx = price_context(s, date(2024, 1, 3))
    assert ctx["px_close"] == 11.0
    assert ctx["px_52wk_high"] == 12.0
    assert ctx["px_52wk_low"] == 10.0


def test_distance_from_high_and_low_are_percentages_of_the_reference():
    s = _series([50.0, 100.0, 75.0])
    ctx = price_context(s, date(2024, 1, 3))
    assert ctx["pct_below_52wk_high"] == pytest.approx(25.0)
    assert ctx["pct_above_52wk_low"] == pytest.approx(50.0)


def test_context_reports_how_much_history_stands_behind_it():
    """A '52-week high' over 3 bars is not one, and the caller has to be able to tell."""
    ctx = price_context(_series([10.0, 11.0, 12.0]), date(2024, 1, 3))
    assert ctx["n_bars_before"] == 3


def test_momentum_is_none_without_enough_bars():
    ctx = price_context(_series([10.0] * 30), date(2024, 1, 30))
    assert ctx["ret_21d"] is not None
    assert ctx["ret_63d"] is None
    assert ctx["ret_252d"] is None


def test_momentum_measures_from_the_bar_that_many_days_back():
    prices = [100.0] * 10 + [110.0]
    ctx = price_context(_series(prices), date(2024, 1, 11))
    assert ctx["ret_21d"] is None          # only 11 bars exist
    s = _series([100.0] * 22 + [110.0])
    ctx = price_context(s, date(2024, 1, 23))
    assert ctx["ret_21d"] == pytest.approx(10.0)


def test_context_before_the_series_starts_is_empty_not_wrong():
    ctx = price_context(_series([10.0, 11.0], start=date(2025, 1, 1)), date(2024, 1, 1))
    assert ctx["px_close"] is None
    assert ctx["n_bars_before"] == 0


def test_flat_prices_have_zero_volatility():
    ctx = price_context(_series([10.0] * 40), date(2024, 2, 9))
    assert ctx["vol_21d"] == pytest.approx(0.0)


def test_dollar_volume_uses_raw_close_not_adjusted():
    """Dollar volume is what actually changed hands, in the dollars of the day."""
    s = _series([10.0] * 25, adj=[5.0] * 25, volume=[1000.0] * 25)
    ctx = price_context(s, date(2024, 1, 25))
    assert ctx["dollar_vol_21d"] == pytest.approx(10_000.0)


# ── price_on ─────────────────────────────────────────────────────────────────

def test_price_on_returns_the_raw_quote_as_of_that_day():
    s = _series([10.0, 11.0, 12.0], adj=[1.0, 2.0, 3.0])
    assert price_on(s, date(2024, 1, 2)) == 11.0


def test_price_on_before_the_series_has_no_answer():
    assert price_on(_series([10.0], start=date(2025, 1, 1)), date(2024, 1, 1)) is None
