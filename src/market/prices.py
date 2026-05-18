"""
Market data helpers — Yahoo Finance chart API + SEC EDGAR XBRL.

Two endpoints, both free, no API key, no crumb:
  - YF chart  (/v8/finance/chart)  → current price, 52-week low/high
  - SEC EDGAR (/api/xbrl/companyconcept) → shares outstanding
  market_cap = shares × current_price

Results cached per ticker for the lifetime of the process (one ingest run).
"""

import time
import logging
import requests
from datetime import date, timedelta
from typing import Optional

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


def get_cap_tier(market_cap: Optional[int]) -> str:
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
        market_cap = int(shares * current) if shares and current else None

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


def get_price_change_pct(ticker: str, start_date: date, end_date: date) -> Optional[float]:
    """Percentage price change between start_date and end_date, or None."""
    try:
        start_ts = int(time.mktime(start_date.timetuple()))
        end_ts   = int(time.mktime((end_date + timedelta(days=7)).timetuple()))
        _throttle()
        resp = requests.get(
            f"{_YF_CHART_URL}/{ticker}",
            params={"interval": "1d", "period1": start_ts, "period2": end_ts},
            headers=_YF_HEADERS,
            timeout=8,
        )
        result = resp.json().get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        closes     = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        pairs = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
        if len(pairs) < 2:
            return None
        price_start = pairs[0][1]
        cutoff = time.mktime(end_date.timetuple())
        valid = [c for ts, c in pairs if ts <= cutoff]
        if not valid:
            return None
        return (valid[-1] - price_start) / price_start * 100 if price_start else None
    except Exception:
        return None


def is_near_52wk_low(current_price: Optional[float], low_52wk: Optional[float], threshold_pct: float = 10.0) -> bool:
    if current_price is None or low_52wk is None or low_52wk == 0:
        return False
    return (current_price - low_52wk) / low_52wk * 100 <= threshold_pct
