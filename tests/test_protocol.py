"""Which rows may carry a verdict, and which label they are charged against."""
from datetime import date

import pandas as pd

from src.research.protocol import evaluable, label_column


def _frame(exec_dates, excess=None, horizon=90):
    n = len(exec_dates)
    return pd.DataFrame({
        "exec_date": [pd.Timestamp(d) for d in exec_dates],
        "ticker": [f"T{i%7}" for i in range(n)],
        "eligible": [True] * n,
        "scorer_disqualified": [False] * n,
        f"exit_in_future_{horizon}d": [False] * n,
        f"excess_spy_{horizon}d": excess if excess is not None else list(range(n)),
    })


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


def test_label_families_name_their_horizon():
    assert label_column(90) == "excess_spy_90d"
    assert label_column(30, "sector") == "excess_sector_30d"
