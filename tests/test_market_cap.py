"""
An implausible market cap must never read as a small cap.

EDGAR's CommonStockSharesOutstanding returned share counts orders of magnitude
too low for 13 companies, so Planet Fitness ($5.3B) was stored at $5,036 and
scored 'small' — earning the +15 small-cap bonus it should never have had.
"""
import pytest

from src.market.prices import MIN_PLAUSIBLE_MARKET_CAP, get_cap_tier, sanitize_market_cap


@pytest.mark.parametrize("cap", [0, 710, 5_036, 792_234, MIN_PLAUSIBLE_MARKET_CAP - 1])
def test_implausible_caps_are_unknown(cap):
    assert sanitize_market_cap(cap) is None
    assert get_cap_tier(cap) == "unknown"


def test_missing_cap_is_unknown():
    assert sanitize_market_cap(None) is None
    assert get_cap_tier(None) == "unknown"


@pytest.mark.parametrize(
    "cap,tier",
    [
        (MIN_PLAUSIBLE_MARKET_CAP, "small"),
        (1_999_999_999, "small"),
        (2_000_000_000, "mid"),
        (9_999_999_999, "mid"),
        (10_000_000_000, "large"),
        (5_361_705_440_000, "large"),
    ],
)
def test_real_caps_keep_their_tier(cap, tier):
    assert sanitize_market_cap(cap) == cap
    assert get_cap_tier(cap) == tier
