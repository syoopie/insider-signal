"""
Point-in-time price context for a purchase, fetched once and stored.

The scorer needs to know how far below its 52-week high a stock sat *on the day
the insider bought*. The live ingest path has a network; `backfill_signals.py`
does not. That asymmetry is exactly why the old 52-week factors were deleted:
the same purchase scored up to 12 points apart depending on which entry point
saw it, and the live path compared against *today's* 52-week high rather than
the high as of the trade.

The fix is to compute it once, at ingest, and store it on the transaction row.
Both paths then read the same stored number, and the number is right.

Nothing here is called during scoring. `score_transaction` stays a pure function
of stored data.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from src.ingest.common import log
from src.market.features import price_context
from src.market.panel import PanelFetchError, PanelSeries, fetch_symbol_history

# A "52-week high" needs a year behind it. Over 40 bars it is the high of the
# last two months wearing the wrong name, and treating it as one is the mistake
# that made first_purchase_12mo fire on the ingest start date.
MIN_BARS = 200

# Fetch a little over two years so the 252-bar window is full even when the
# transaction date sits well before the filing.
LOOKBACK_DAYS = 800

CONTEXT_FIELDS = ("px_close_at_tx", "px_52wk_high", "pct_below_52wk_high",
                  "price_context_bars")

_series_cache: dict[str, Optional[PanelSeries]] = {}


def _series(ticker: str, as_of: date) -> Optional[PanelSeries]:
    """
    One fetch per ticker per process.

    A day's filings cluster heavily on a few hundred issuers, and the same
    issuer often files several Form 4s at once. Without the cache a cluster of
    six insiders at one company costs six identical round trips against an
    8 req/sec budget shared with the filing fetches themselves.
    """
    if ticker in _series_cache:
        return _series_cache[ticker]
    try:
        frame = fetch_symbol_history(ticker, as_of - timedelta(days=LOOKBACK_DAYS),
                                     as_of + timedelta(days=1))
    except PanelFetchError as error:
        log(f"  price context: {ticker} fetch failed ({error})")
        _series_cache[ticker] = None
        return None
    if frame is None or frame.empty:
        _series_cache[ticker] = None
        return None
    series = PanelSeries(
        symbol=ticker,
        dates=frame["date"].to_numpy(),
        close=frame["close"].to_numpy(dtype="float64"),
        adj_close=frame["adj_close"].to_numpy(dtype="float64"),
        volume=frame["volume"].to_numpy(dtype="float64"),
    )
    _series_cache[ticker] = series
    return series


def context_from_series(series: Optional[PanelSeries], as_of: date) -> dict:
    """
    The stored columns, or Nones when there is not enough history behind the date.

    Returning Nones rather than a partial answer is deliberate. A purchase whose
    context cannot be established scores as unrankable and is never alerted,
    which is the conservative failure and the one the audit script can see.
    """
    empty = dict.fromkeys(CONTEXT_FIELDS)
    if series is None:
        return empty
    values = price_context(series, as_of)
    bars = int(values.get("n_bars_before") or 0)
    if bars < MIN_BARS or values.get("pct_below_52wk_high") is None:
        return {**empty, "price_context_bars": bars}
    return {
        "px_close_at_tx": float(values["px_close"]),
        "px_52wk_high": float(values["px_52wk_high"]),
        "pct_below_52wk_high": float(values["pct_below_52wk_high"]),
        "price_context_bars": bars,
    }


def context_for(ticker: Optional[str], as_of: Optional[date]) -> dict:
    """Fetch and compute in one call. Used by the ingest path."""
    if not ticker or as_of is None:
        return dict.fromkeys(CONTEXT_FIELDS)
    return context_from_series(_series(ticker, as_of), as_of)


def reset_cache() -> None:
    _series_cache.clear()
