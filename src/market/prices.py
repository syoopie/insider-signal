"""
Yahoo Finance chart API: price changes for the backtest, and the cap-tier rules.

Free, no API key, no crumb. Market caps themselves are written weekly by
`scripts/refresh_market_caps.py`; nothing in the daily pipeline fetches one.
"""

import calendar
import time
import logging
import requests
from datetime import date, timedelta
from typing import NamedTuple, Optional

logging.getLogger("urllib3").setLevel(logging.CRITICAL)

_YF_CHART_URL  = "https://query1.finance.yahoo.com/v8/finance/chart"

_YF_HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible)"}

_last_call = 0.0
_MIN_GAP = 0.5  # seconds between API calls


def _throttle():
    global _last_call
    gap = time.time() - _last_call
    if gap < _MIN_GAP:
        time.sleep(_MIN_GAP - gap)
    _last_call = time.time()


def throttle_yf():
    """The Yahoo rate limiter, shared. One limiter per process, not one per module."""
    _throttle()


# EDGAR's CommonStockSharesOutstanding sometimes yields a share count that is
# orders of magnitude too low — a single share class, or a value the filer scaled
# in its own units. That produced caps like Planet Fitness at $5,036. Nothing in
# an S&P 500 + Russell 2000 universe is worth under $10M, so a cap below this is
# a failed lookup, not a micro-cap, and must not earn the small-cap bonus.
MIN_PLAUSIBLE_MARKET_CAP = 10_000_000


def sanitize_market_cap(market_cap: Optional[int]) -> Optional[int]:
    """None for an implausible cap, so it stores as unknown."""
    if market_cap is None or market_cap < MIN_PLAUSIBLE_MARKET_CAP:
        return None
    return market_cap


def get_cap_tier(market_cap: Optional[int]) -> str:
    market_cap = sanitize_market_cap(market_cap)
    if market_cap is None:
        return "unknown"
    if market_cap < 2_000_000_000:
        return "small"
    if market_cap < 10_000_000_000:
        return "mid"
    return "large"


class PriceChange(NamedTuple):
    """
    pct is the return; status says why it is missing when it is.

    "no_data" means the symbol genuinely has no prices for the window, which is
    what a delisting looks like. "error" means the request failed. The backtest
    must treat these differently: a delisting is a real -50% outcome, a failed
    request is a sample the run could not measure. Collapsing both to None let a
    transient network blip be recorded as a total loss.
    """
    pct: Optional[float]
    status: str  # "ok" | "no_data" | "error"


def _utc_ts(d: date) -> int:
    """Midnight UTC. time.mktime uses the local zone, which made backtest entry
    prices depend on the machine's timezone."""
    return calendar.timegm(d.timetuple())


def _total_return_closes(indicators: dict) -> list:
    """
    Dividend-and-split-adjusted closes, falling back to raw closes.

    `quote[].close` is split-adjusted (NVDA's 10:1 reads +43.05% raw against
    +43.06% adjusted) but not dividend-adjusted. Over 2024 that is +24.45% vs
    +26.05% for SPY and +31.07% vs +39.19% for T. The error does not cancel in
    `ticker - SPY`: it scales with (ticker yield - SPY yield) x horizon, so it
    understates precisely the small-cap value names insider buying favours.

    A few symbols return no adjclose array, so fall back rather than dropping
    the observation — a missing benchmark leg discards the whole signal.
    """
    adjclose = (indicators.get("adjclose") or [{}])[0] or {}
    series = adjclose.get("adjclose")
    if series:
        return series
    return ((indicators.get("quote") or [{}])[0] or {}).get("close") or []


def get_price_change(ticker: str, start_date: date, end_date: date) -> PriceChange:
    """Percentage change between the first close on/after start_date and the last on/before end_date."""
    try:
        _throttle()
        resp = requests.get(
            f"{_YF_CHART_URL}/{ticker}",
            params={
                "interval": "1d",
                "period1": _utc_ts(start_date),
                "period2": _utc_ts(end_date + timedelta(days=7)),
            },
            headers=_YF_HEADERS,
            timeout=8,
        )
    except requests.RequestException:
        return PriceChange(None, "error")

    if resp.status_code == 404:
        return PriceChange(None, "no_data")
    if resp.status_code != 200:
        return PriceChange(None, "error")
    try:
        chart = resp.json().get("chart") or {}
    except ValueError:
        return PriceChange(None, "error")
    if chart.get("error"):
        return PriceChange(None, "no_data")
    results = chart.get("result") or []
    if not results:
        return PriceChange(None, "no_data")

    result     = results[0] or {}
    timestamps = result.get("timestamp") or []
    closes     = _total_return_closes(result.get("indicators") or {})
    pairs = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
    if len(pairs) < 2:
        return PriceChange(None, "no_data")

    price_start = pairs[0][1]
    cutoff = _utc_ts(end_date) + 86_400
    valid = [c for ts, c in pairs if ts <= cutoff]
    if not valid or not price_start:
        return PriceChange(None, "no_data")
    return PriceChange((valid[-1] - price_start) / price_start * 100, "ok")
