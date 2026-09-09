"""Shared fixtures. Nothing here touches the database."""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"


def load_script(path: Path):
    """
    Execute a file in scripts/ as a module and hand it back.

    Registering it in `sys.modules` first is not optional: a dataclass resolves
    its annotations through `sys.modules[cls.__module__]`, so a module executed
    without being registered raises AttributeError on the `@dataclass` line
    rather than on anything the script got wrong.
    """
    name = f"_script_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


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
            # Stored at ingest by src/market/context.py. Defaulted to the
            # research sample's median so a test that is not about the ranking
            # gets a scoreable purchase; pass None to exercise the unranked path.
            "pct_below_52wk_high": 24.87,
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
