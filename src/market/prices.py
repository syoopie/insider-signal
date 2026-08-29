"""
Market data helpers — Yahoo Finance chart API + SEC EDGAR XBRL.

Two endpoints, both free, no API key, no crumb:
  - YF chart  (/v8/finance/chart)  → current price, 52-week low/high
  - SEC EDGAR (/api/xbrl/companyconcept) → shares outstanding
  market_cap = shares × current_price

Results cached per ticker for the lifetime of the process (one ingest run).
"""

import calendar
import time
import logging
import requests
from datetime import date, timedelta
from typing import NamedTuple, Optional

logging.getLogger("urllib3").setLevel(logging.CRITICAL)

_YF_CHART_URL  = "https://query1.finance.yahoo.com/v8/finance/chart"
_EDGAR_CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept"

_YF_HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible)"}
_EDGAR_HEADERS = {"User-Agent": "InsiderSignal sunyupei19992@gmail.com", "Accept-Encoding": "gzip, deflate"}

_cache: dict = {}            # ticker → market data dict
_cik_cache: dict = {}        # ticker → CIK string

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
    """None for an implausible cap, so it stores as unknown and scores at +5."""
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


def _get_cik(ticker: str) -> Optional[str]:
    """Return the CIK for a ticker by querying the companies table. Cached."""
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        from src.db.connection import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT cik FROM companies WHERE ticker = %s LIMIT 1", (ticker.upper(),))
                row = cur.fetchone()
        cik = row[0] if row else None
        _cik_cache[ticker] = cik
        return cik
    except Exception:
        return None


def _get_shares_outstanding(ticker: str) -> Optional[int]:
    """
    Fetch shares outstanding from SEC EDGAR XBRL company facts.
    Returns the most recent 10-K or 10-Q value, or None if unavailable.
    """
    cik = _get_cik(ticker)
    if not cik:
        return None
    cik_padded = str(cik).zfill(10)
    try:
        _throttle()
        resp = requests.get(
            f"{_EDGAR_CONCEPT}/CIK{cik_padded}/us-gaap/CommonStockSharesOutstanding.json",
            headers=_EDGAR_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        shares_list = resp.json().get("units", {}).get("shares", [])
        # Keep only 10-K and 10-Q filings with a real value
        valid = [x for x in shares_list if x.get("form") in ("10-K", "10-Q") and x.get("val")]
        if not valid:
            return None
        latest = sorted(valid, key=lambda x: x.get("end", ""), reverse=True)
        return int(latest[0]["val"])
    except Exception:
        return None


def get_market_data(ticker: str) -> dict:
    """
    Returns {market_cap, cap_tier, price_52wk_low, current_price} or {} on failure.
    Cached per ticker for the lifetime of the process.
    """
    if ticker in _cache:
        return _cache[ticker]

    try:
        _throttle()
        resp = requests.get(
            f"{_YF_CHART_URL}/{ticker}",
            params={"interval": "1d", "range": "1y"},
            headers=_YF_HEADERS,
            timeout=4,
        )
        if resp.status_code != 200:
            _cache[ticker] = {}
            return {}

        meta = resp.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        current = meta.get("regularMarketPrice")
        low_52wk = meta.get("fiftyTwoWeekLow")

        if not current:
            _cache[ticker] = {}
            return {}

        shares = _get_shares_outstanding(ticker)
        market_cap = sanitize_market_cap(int(shares * current) if shares and current else None)

        mdata = {
            "market_cap": market_cap,
            "cap_tier": get_cap_tier(market_cap),
            "price_52wk_low": low_52wk,
            "current_price": current,
        }
        _cache[ticker] = mdata
        return mdata

    except Exception:
        _cache[ticker] = {}
        return {}


def get_price_on_date(ticker: str, target_date: date) -> Optional[float]:
    """Closing price on or just after target_date (up to 7 calendar days)."""
    try:
        start_ts = int(time.mktime(target_date.timetuple()))
        end_ts   = int(time.mktime((target_date + timedelta(days=7)).timetuple()))
        _throttle()
        resp = requests.get(
            f"{_YF_CHART_URL}/{ticker}",
            params={"interval": "1d", "period1": start_ts, "period2": end_ts},
            headers=_YF_HEADERS,
            timeout=8,
        )
        closes = (
            resp.json()
                .get("chart", {})
                .get("result", [{}])[0]
                .get("indicators", {})
                .get("quote", [{}])[0]
                .get("close", [])
        )
        closes = [c for c in closes if c is not None]
        return float(closes[0]) if closes else None
    except Exception:
        return None


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


def get_price_change_pct(ticker: str, start_date: date, end_date: date) -> Optional[float]:
    """Percentage price change between start_date and end_date, or None."""
    return get_price_change(ticker, start_date, end_date).pct


def is_near_52wk_low(current_price: Optional[float], low_52wk: Optional[float], threshold_pct: float = 10.0) -> bool:
    if current_price is None or low_52wk is None or low_52wk == 0:
        return False
    return (current_price - low_52wk) / low_52wk * 100 <= threshold_pct
