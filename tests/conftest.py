"""Shared fixtures. Nothing here touches the database."""

from datetime import date, timedelta

import pytest


@pytest.fixture
def make_tx():
    """
    Build a transaction dict shaped like a `transactions` row, with sane
    defaults for a scoreable open-market purchase. Override any field per test.
    """

    def _make(**overrides):
        tx = {
            "transaction_code": "P",
            "transaction_date": date.today().isoformat(),
            "is_10b51": False,
            "is_direct": True,
            "is_routine": False,
            "shares": 1000.0,
            "shares_after": 1000.0,
            "price_per_share": 10.0,
            "total_value": 10_000.0,
        }
        tx.update(overrides)
        return tx

    return _make


@pytest.fixture
def prior_on():
    """A prior-purchase dict `n` days before `ref` (default: today)."""

    def _make(days_ago, ref=None):
        ref = ref or date.today()
        return {"transaction_date": (ref - timedelta(days=days_ago)).isoformat()}

    return _make
