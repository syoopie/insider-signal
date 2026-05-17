"""
Market data helpers using yfinance (free, no API key needed).
Used for: market cap classification, 52-week low detection, price lookups.
"""

import yfinance as yf
from datetime import date, timedelta
from typing import Optional


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
    Returns {market_cap, cap_tier, price_52wk_low, current_price} or empty dict on failure.
    """
    try:
        info = yf.Ticker(ticker).info
        market_cap = info.get("marketCap")
        low_52wk = info.get("fiftyTwoWeekLow")
        current = info.get("currentPrice") or info.get("regularMarketPrice")
        return {
            "market_cap": market_cap,
            "cap_tier": get_cap_tier(market_cap),
            "price_52wk_low": low_52wk,
            "current_price": current,
        }
    except Exception:
        return {}


def get_price_on_date(ticker: str, target_date: date) -> Optional[float]:
    """
    Returns the adjusted closing price on or after target_date (up to 5 trading days later).
    Returns None if no data found.
    """
    try:
        end = target_date + timedelta(days=7)
        hist = yf.Ticker(ticker).history(start=target_date.isoformat(), end=end.isoformat())
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception:
        return None


def get_price_change_pct(ticker: str, start_date: date, end_date: date) -> Optional[float]:
    """
    Returns percentage price change between start_date and end_date.
    Returns None if data unavailable (e.g., delisted stock).
    """
    try:
        end_fetch = end_date + timedelta(days=7)
        hist = yf.Ticker(ticker).history(
            start=start_date.isoformat(), end=end_fetch.isoformat()
        )
        if len(hist) < 2:
            return None
        price_start = float(hist["Close"].iloc[0])
        # Find the closest available price to end_date
        hist_to_end = hist[hist.index.date <= end_date]
        if hist_to_end.empty:
            return None
        price_end = float(hist_to_end["Close"].iloc[-1])
        if price_start == 0:
            return None
        return (price_end - price_start) / price_start * 100
    except Exception:
        return None


def is_near_52wk_low(current_price: Optional[float], low_52wk: Optional[float], threshold_pct: float = 10.0) -> bool:
    """Returns True if current_price is within threshold_pct% above the 52-week low."""
    if current_price is None or low_52wk is None or low_52wk == 0:
        return False
    pct_above_low = (current_price - low_52wk) / low_52wk * 100
    return pct_above_low <= threshold_pct
