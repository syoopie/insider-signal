"""
Point-in-time price features and forward returns, computed from the local panel.

Two rules govern everything here.

**Nothing looks forward.** A feature "as of the transaction date" reads bars up
to and including that date and never past it. Every lookup goes through
PanelSeries.index_as_of, which is strict about that.

**Returns use adj_close, levels use close.** A return has to include dividends
or it understates in proportion to yield. A level compared against something
quoted in raw dollars — a Form 4's price_per_share against a 52-week high — has
to be the raw quote, or the comparison is between two different price scales.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np

from src.market.panel import PanelSeries

TRADING_DAYS_YEAR = 252

# Windows in trading days.
MOMENTUM_WINDOWS = {"ret_21d": 21, "ret_63d": 63, "ret_252d": 252}
VOL_WINDOW = 21
DOLLAR_VOL_WINDOW = 21


@dataclass(frozen=True)
class WindowReturn:
    """
    pct is the return; status says why it is missing when it is.

    Statuses are kept apart because they mean different things about the trade.
    "no_symbol" is a coverage gap in the panel. "no_entry" means the series
    stops before the position would have opened. "no_exit" means it stops during
    the hold, which is what a delisting looks like. Collapsing them forces every
    analysis to guess, and guessing is how a data gap becomes a -50% loss.
    """
    pct: Optional[float]
    status: str  # "ok" | "no_symbol" | "no_entry" | "no_exit"

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# How far a bar may sit from the date it stands in for. A long weekend plus a
# holiday is four days; a trading halt is longer. Beyond this the bar is not
# standing in for that date, it is the last bar the symbol ever had.
MAX_BAR_GAP_DAYS = 10


def window_return(series: Optional[PanelSeries], start: date, end: date) -> WindowReturn:
    """
    Total return from the first bar on or after `start` to the last on or before `end`.

    Both ends must land near the date they stand for. Taking whatever bar exists
    is how a symbol that stopped trading three days into a 180-day hold gets
    recorded as a real three-day return rather than a delisting — the same
    survivorship mistake the backtest already had, in a different place.
    """
    if series is None or len(series) == 0:
        return WindowReturn(None, "no_symbol")

    i = series.index_on_or_after(start)
    if i == -1 or _gap(series.dates[i], start) > MAX_BAR_GAP_DAYS:
        return WindowReturn(None, "no_entry")

    j = series.index_as_of(end)
    if j <= i or _gap(series.dates[j], end) > MAX_BAR_GAP_DAYS:
        return WindowReturn(None, "no_exit")

    p0, p1 = series.adj_close[i], series.adj_close[j]
    if not p0 or not np.isfinite(p0) or not np.isfinite(p1):
        return WindowReturn(None, "no_entry")
    return WindowReturn(float((p1 - p0) / p0 * 100.0), "ok")


def _gap(bar: np.datetime64, target: date) -> int:
    """Absolute calendar days between a bar's date and the date it stands for."""
    return abs(int((bar - np.datetime64(target, "D")) / np.timedelta64(1, "D")))


def price_context(series: Optional[PanelSeries], as_of: date) -> dict:
    """
    Everything the panel knows about a symbol as of a date, from that date backwards.

    Returns a dict of floats and Nones. `n_bars_before` is included so a caller
    can refuse a feature that does not have enough history behind it — a
    "52-week high" over 40 bars is not a 52-week high, and treating it as one is
    the mistake that made first_purchase_12mo fire on the ingest start date.
    """
    empty = {
        "px_close": None, "px_52wk_high": None, "px_52wk_low": None,
        "pct_below_52wk_high": None, "pct_above_52wk_low": None,
        "ret_21d": None, "ret_63d": None, "ret_252d": None,
        "vol_21d": None, "dollar_vol_21d": None, "n_bars_before": 0,
    }
    if series is None or len(series) == 0:
        return empty

    idx = series.index_as_of(as_of)
    if idx < 0:
        return empty

    close = series.close[: idx + 1]
    adj = series.adj_close[: idx + 1]
    volume = series.volume[: idx + 1]
    px = float(close[-1])

    year = close[-TRADING_DAYS_YEAR:]
    high = float(np.nanmax(year))
    low = float(np.nanmin(year))

    out = {
        "px_close": px,
        "px_52wk_high": high,
        "px_52wk_low": low,
        "pct_below_52wk_high": (high - px) / high * 100.0 if high else None,
        "pct_above_52wk_low": (px - low) / low * 100.0 if low else None,
        "n_bars_before": int(idx + 1),
    }

    for name, window in MOMENTUM_WINDOWS.items():
        out[name] = _trailing_return(adj, window)

    out["vol_21d"] = _annualised_vol(adj, VOL_WINDOW)

    dollar = close[-DOLLAR_VOL_WINDOW:] * volume[-DOLLAR_VOL_WINDOW:]
    dollar = dollar[np.isfinite(dollar)]
    out["dollar_vol_21d"] = float(np.mean(dollar)) if len(dollar) else None

    return out


def _trailing_return(adj: np.ndarray, window: int) -> Optional[float]:
    if len(adj) <= window:
        return None
    start, end = adj[-(window + 1)], adj[-1]
    if not start or not np.isfinite(start) or not np.isfinite(end):
        return None
    return float((end - start) / start * 100.0)


def _annualised_vol(adj: np.ndarray, window: int) -> Optional[float]:
    if len(adj) < window + 1:
        return None
    tail = adj[-(window + 1):]
    if np.any(~np.isfinite(tail)) or np.any(tail <= 0):
        return None
    daily = np.diff(np.log(tail))
    if len(daily) < 2:
        return None
    return float(np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS_YEAR) * 100.0)


def price_on(series: Optional[PanelSeries], as_of: date) -> Optional[float]:
    """Raw close as of a date, for comparing against a price quoted in dollars."""
    if series is None or len(series) == 0:
        return None
    idx = series.index_as_of(as_of)
    if idx < 0:
        return None
    px = float(series.close[idx])
    return px if np.isfinite(px) else None
