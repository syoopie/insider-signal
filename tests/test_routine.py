"""
The routine rule, pinned against the database version it reproduces.

Two of these cases exist because the database version got them wrong first. A
month end hardcoded to 28 hid every purchase on the 29th-31st. And undetermined
is a third answer, never a False.
"""
from datetime import date

import pandas as pd

from src.research.routine import routine_flags

COLUMNS = ["cik", "insider_name", "transaction_date"]


def _buy(when, cik="1", name="A BUYER"):
    return (cik, name, when)


def _frame(*rows):
    return pd.DataFrame(list(rows), columns=COLUMNS)


def _flag(*rows):
    """The last row is the purchase under test in every case below."""
    got = routine_flags(_frame(*rows)).iloc[-1]
    return None if got is pd.NA else bool(got)


OLDEST = _buy(date(2021, 1, 5))


def test_the_same_month_in_two_of_three_prior_years_is_routine():
    assert _flag(OLDEST,
                 _buy(date(2022, 3, 20)),
                 _buy(date(2023, 3, 10)),
                 _buy(date(2024, 3, 5))) is True


def test_history_that_reaches_back_with_no_same_month_buying_is_not_routine():
    assert _flag(OLDEST,
                 _buy(date(2022, 7, 1)),
                 _buy(date(2023, 9, 1)),
                 _buy(date(2024, 3, 5))) is False


def test_an_insider_with_no_prior_history_is_undetermined():
    assert _flag(_buy(date(2024, 3, 5))) is None


def test_one_determined_year_that_is_routine_is_still_not_routine():
    """One of three is not two of three, and a determined year is not pd.NA."""
    assert _flag(_buy(date(2023, 3, 10)),
                 _buy(date(2024, 3, 5))) is False


def test_one_determined_year_that_is_not_routine_is_false_not_undetermined():
    assert _flag(_buy(date(2023, 8, 1)),
                 _buy(date(2024, 3, 5))) is False


# ── the month-end trap ──────────────────────────────────────────────────────

def test_a_prior_year_match_late_in_the_month_is_found():
    """A month end hardcoded to day 28 finds neither prior year and says False."""
    for day in (29, 30, 31):
        assert _flag(OLDEST,
                     _buy(date(2022, 3, day)),
                     _buy(date(2023, 3, day)),
                     _buy(date(2024, 3, 5))) is True, day


def test_a_prior_year_match_on_a_leap_day_is_found():
    """February's end moves. 2024-02-29 exists and 2023-02-29 does not."""
    assert _flag(OLDEST,
                 _buy(date(2023, 2, 25)),
                 _buy(date(2024, 2, 29)),
                 _buy(date(2025, 2, 10))) is True


# ── shape ───────────────────────────────────────────────────────────────────

def test_the_result_is_boolean_dtype_and_keyed_by_the_input_index():
    """
    Aligned by index, not by position, so a caller can assign it straight onto
    a frame it has sorted or filtered.
    """
    frame = _frame(_buy(date(2024, 3, 5)),
                   OLDEST,
                   _buy(date(2023, 3, 10)),
                   _buy(date(2022, 3, 20)))
    frame.index = [40, 10, 30, 20]

    got = routine_flags(frame)
    assert got.dtype == "boolean"
    expected = pd.Series(pd.array([True, pd.NA, False, False], dtype="boolean"),
                         index=[40, 10, 30, 20], name="is_routine")
    pd.testing.assert_series_equal(got, expected)


def test_a_missing_name_or_date_is_undetermined():
    frame = _frame(OLDEST,
                   _buy(date(2022, 3, 20)),
                   _buy(date(2023, 3, 10)),
                   _buy(None),
                   _buy(date(2024, 3, 5), name=None))
    got = routine_flags(frame)
    assert got.iloc[3] is pd.NA and got.iloc[4] is pd.NA


def test_the_same_name_at_two_issuers_does_not_pool_history():
    """Two directors sharing a common name at unrelated companies is real."""
    frame = _frame(_buy(date(2021, 1, 5), cik="1"),
                   _buy(date(2022, 3, 20), cik="1"),
                   _buy(date(2023, 3, 10), cik="1"),
                   _buy(date(2021, 1, 5), cik="2"),
                   _buy(date(2022, 7, 1), cik="2"),
                   _buy(date(2023, 8, 1), cik="2"),
                   _buy(date(2024, 3, 5), cik="1"),
                   _buy(date(2024, 3, 5), cik="2"))
    got = routine_flags(frame)
    assert bool(got.iloc[6]) is True
    assert bool(got.iloc[7]) is False
