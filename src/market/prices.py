"""
Market data helpers — direct Yahoo Finance API (no yfinance dependency).

Uses the /v7/finance/quote endpoint with a crumb-based session.
Crumb is fetched once per process and reused. Falls back to empty dict on any failure.
Results are cached per ticker for the lifetime of the process (one ingest run).
"""

import time
import logging
import requests
from datetime import date, timedelta
from typing import Optional

logging.getLogger("urllib3").setLevel(logging.CRITICAL)

_YF_QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
_YF_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_YF_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart"

_session: Optional[requests.Session] = None
_crumb: Optional[str] = None
_crumb_attempted: bool = False  # try once per process; stop on failure
_cache: dict = {}

_last_call = 0.0
_MIN_GAP = 0.5  # seconds between calls


def _throttle():
    global _last_call
    gap = time.time() - _last_call
    if gap < _MIN_GAP:
        time.sleep(_MIN_GAP - gap)
    _last_call = time.time()


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })
    return _session


def _get_crumb() -> Optional[str]:
    global _crumb, _crumb_attempted
    if _crumb:
        return _crumb
    if _crumb_attempted:
        return None  # already failed this run — don't retry
    _crumb_attempted = True
    try:
        s = _get_session()
        s.get("https://fc.yahoo.com", timeout=5)  # seeds session; 404 is fine
        resp = s.get(_YF_CRUMB_URL, timeout=8)
        if resp.status_code == 200 and resp.text.strip():
            _crumb = resp.text.strip()
        else:
            print(f"[market] crumb fetch failed: HTTP {resp.status_code} — market data unavailable this run")
    except Exception as e:
        print(f"[market] crumb fetch error: {e} — market data unavailable this run")
    return _crumb


def _reset_crumb():
    global _crumb, _crumb_attempted
    _crumb = None
    _crumb_attempted = False  # allow one more attempt after a 401


def get_cap_tier(market_cap: Optional[int]) -> str:
    if market_cap is None:
        return "unknown"
    if market_cap < 2_000_000_000:
        return "small"
    if market_cap < 10_000_000_000:
        return "mid"
    return "large"


def get_market_data(ticker: str) -> dict:
    """
    Returns {market_cap, cap_tier, price_52wk_low, current_price} or {} on failure.
    Cached per ticker for the lifetime of the process.
    """
    if ticker in _cache:
        return _cache[ticker]

    try:
        _throttle()
        crumb = _get_crumb()
        if not crumb:
            return {}

        resp = _get_session().get(
            _YF_QUOTE_URL,
            params={"symbols": ticker, "crumb": crumb},
            timeout=10,
        )

        if resp.status_code == 401:
            _reset_crumb()
            return {}

        data = resp.json()
        result = data.get("quoteResponse", {}).get("result", [])
        if not result:
            _cache[ticker] = {}
            return {}

        q = result[0]
        market_cap = q.get("marketCap")
        low_52wk = q.get("fiftyTwoWeekLow")
        current = q.get("regularMarketPrice")

        cap_int = int(market_cap) if market_cap else None
        mdata = {
            "market_cap": cap_int,
            "cap_tier": get_cap_tier(cap_int),
            "price_52wk_low": low_52wk,
            "current_price": current,
        }
        _cache[ticker] = mdata
        return mdata

    except Exception:
        _cache[ticker] = {}
        return {}


def get_price_on_date(ticker: str, target_date: date) -> Optional[float]:
    """
    Returns the closing price on or just after target_date (up to 7 calendar days).
    Uses the Yahoo Finance chart endpoint directly.
    """
    try:
        import math
        start_ts = int(time.mktime(target_date.timetuple()))
        end_ts = int(time.mktime((target_date + timedelta(days=7)).timetuple()))
        crumb = _get_crumb()
        params = {
            "interval": "1d",
            "period1": start_ts,
            "period2": end_ts,
        }
        if crumb:
            params["crumb"] = crumb
        resp = _get_session().get(f"{_YF_CHART_URL}/{ticker}", params=params, timeout=10)
        chart = resp.json().get("chart", {})
        result = chart.get("result", [])
        if not result:
            return None
        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        return float(closes[0]) if closes else None
    except Exception:
        return None


def get_price_change_pct(ticker: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Returns percentage price change between start_date and end_date, or None if unavailable.
    """
    try:
        import math
        start_ts = int(time.mktime(start_date.timetuple()))
        end_ts = int(time.mktime((end_date + timedelta(days=7)).timetuple()))
        crumb = _get_crumb()
        params = {
            "interval": "1d",
            "period1": start_ts,
            "period2": end_ts,
        }
        if crumb:
            params["crumb"] = crumb
        resp = _get_session().get(f"{_YF_CHART_URL}/{ticker}", params=params, timeout=10)
        chart = resp.json().get("chart", {})
        result = chart.get("result", [])
        if not result:
            return None
        timestamps = result[0].get("timestamp", [])
        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        pairs = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
        if len(pairs) < 2:
            return None
        price_start = pairs[0][1]
        end_ts_cutoff = time.mktime(end_date.timetuple())
        valid = [(ts, c) for ts, c in pairs if ts <= end_ts_cutoff]
        if not valid:
            return None
        price_end = valid[-1][1]
        if price_start == 0:
            return None
        return (price_end - price_start) / price_start * 100
    except Exception:
        return None


def is_near_52wk_low(current_price: Optional[float], low_52wk: Optional[float], threshold_pct: float = 10.0) -> bool:
    """Returns True if current_price is within threshold_pct% above the 52-week low."""
    if current_price is None or low_52wk is None or low_52wk == 0:
        return False
    return (current_price - low_52wk) / low_52wk * 100 <= threshold_pct
