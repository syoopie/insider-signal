"""
A local daily price panel: one row per (symbol, trading day).

Everything the scoring research needs is a lookup into a price series. Forward
returns at four horizons for ~9,500 purchases, 52-week high and low as of a
trade date, trailing momentum, realised volatility, dollar volume. Fetching
those per question over the network is what makes the backtest a 30-minute job
and makes any real experiment unaffordable. Fetched once into a local file, the
same questions are array indexing.

The panel is deliberately NOT in Neon. The database is on a 0.5GB free tier and
the pipeline does not need this; only research does.

Two price columns, and the difference matters:

  adj_close  dividend- and split-adjusted. Use for every return.
  close      split-adjusted only, which is the price actually quoted that day.
             Use when comparing against something else quoted in raw dollars,
             such as a Form 4's price_per_share against a 52-week high.

Mixing them silently produces a wrong answer rather than an error, so callers
should name which one they want.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import requests

from src.market.prices import throttle_yf

_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible)"}

PANEL_PATH = Path(__file__).resolve().parents[2] / "data" / "prices" / "panel.parquet"

# The benchmark legs. SPY and IWM are what the backtest already compares against;
# the SPDR sector funds are here so a signal can be measured against its own
# industry instead of the whole market.
BENCHMARK_SYMBOLS = [
    "SPY", "IWM",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
]

PANEL_COLUMNS = ["symbol", "date", "close", "adj_close", "volume"]


class PanelFetchError(RuntimeError):
    """The request failed. Distinct from a symbol that genuinely has no bars."""


@dataclass(frozen=True)
class PanelSeries:
    """One symbol's history as parallel arrays, sorted ascending by date."""
    symbol: str
    dates: np.ndarray      # datetime64[D]
    close: np.ndarray      # float64
    adj_close: np.ndarray  # float64
    volume: np.ndarray     # float64

    def __len__(self) -> int:
        return len(self.dates)

    def index_as_of(self, d: date) -> int:
        """
        Position of the last bar on or before `d`, or -1 if the series starts later.

        Strictly as-of: a bar dated `d` counts, a bar dated `d + 1` never does.
        This is the only lookup that keeps a feature point-in-time, so callers
        computing anything "as of the transaction date" must go through it.
        """
        pos = int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="right"))
        return pos - 1

    def index_on_or_after(self, d: date) -> int:
        """Position of the first bar on or after `d`, or -1 if none exists."""
        pos = int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="left"))
        return pos if pos < len(self.dates) else -1


def _utc_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def fetch_symbol_history(symbol: str, start: date, end: date,
                         attempts: int = 3) -> Optional[pd.DataFrame]:
    """
    One request for a symbol's whole range.

    Returns a DataFrame with PANEL_COLUMNS, or None when the symbol genuinely has
    no bars (delisted, renamed, never listed). Raises PanelFetchError when the
    request itself failed, so a network problem is never recorded as an empty
    history — that conflation is what once scored a transient failure as a -50%
    delisting.
    """
    last_error = None
    for attempt in range(attempts):
        throttle_yf()
        try:
            resp = requests.get(
                f"{_YF_CHART_URL}/{symbol}",
                params={
                    "interval": "1d",
                    "period1": _utc_ts(start),
                    "period2": _utc_ts(end),
                    "events": "div,splits",
                },
                headers=_YF_HEADERS,
                timeout=20,
            )
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            time.sleep(2 ** attempt)
            continue

        try:
            chart = resp.json().get("chart") or {}
        except ValueError as e:
            last_error = e
            time.sleep(2 ** attempt)
            continue

        if chart.get("error"):
            return None
        results = chart.get("result") or []
        if not results:
            return None

        return _frame_from_result(symbol, results[0] or {})

    raise PanelFetchError(f"{symbol}: {last_error}")


def _frame_from_result(symbol: str, result: dict) -> Optional[pd.DataFrame]:
    timestamps = result.get("timestamp") or []
    if not timestamps:
        return None

    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0] or {}
    adj = (indicators.get("adjclose") or [{}])[0] or {}

    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    adj_closes = adj.get("adjclose") or closes

    # Daily bars are stamped at the exchange open, so the UTC date is the
    # trading date for every US session including DST changeovers.
    dates = [datetime.fromtimestamp(ts, tz=timezone.utc).date() for ts in timestamps]

    frame = pd.DataFrame({
        "symbol": symbol,
        "date": pd.to_datetime(dates),
        "close": pd.Series(closes, dtype="float64"),
        "adj_close": pd.Series(adj_closes, dtype="float64"),
        "volume": pd.Series(volumes, dtype="float64"),
    })
    frame = frame.dropna(subset=["close", "adj_close"])
    if frame.empty:
        return None
    return frame.sort_values("date").reset_index(drop=True)[PANEL_COLUMNS]


def write_panel(frame: pd.DataFrame, path: Path = PANEL_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.sort_values(["symbol", "date"]).reset_index(drop=True).to_parquet(
        path, index=False, compression="zstd"
    )
    return path


def read_panel_frame(path: Path = PANEL_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"No price panel at {path}. Build it with "
            "'uv run python scripts/build_price_panel.py'."
        )
    return pd.read_parquet(path)


def load_panel(path: Path = PANEL_PATH) -> dict[str, PanelSeries]:
    """{symbol: PanelSeries}, ready for as-of lookups."""
    frame = read_panel_frame(path)
    out: dict[str, PanelSeries] = {}
    for symbol, group in frame.groupby("symbol", sort=False):
        group = group.sort_values("date")
        out[symbol] = PanelSeries(
            symbol=symbol,
            dates=group["date"].to_numpy(dtype="datetime64[D]"),
            close=group["close"].to_numpy(dtype="float64"),
            adj_close=group["adj_close"].to_numpy(dtype="float64"),
            volume=group["volume"].to_numpy(dtype="float64"),
        )
    return out


def panel_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol row count and date range. What you check before trusting a build."""
    return (
        frame.groupby("symbol")
        .agg(bars=("date", "size"), first=("date", "min"), last=("date", "max"))
        .reset_index()
    )


def merge_panels(existing: Optional[pd.DataFrame],
                 fetched: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """
    Combine new symbol frames with whatever is already stored, new data winning.

    Rebuilding a symbol replaces its rows rather than duplicating them, so a
    resumed or repeated build converges to the same panel.
    """
    frames = [f for f in fetched if f is not None and not f.empty]
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PANEL_COLUMNS)
    if existing is None or existing.empty:
        combined = new
    else:
        keep = existing[~existing["symbol"].isin(set(new["symbol"]))]
        combined = pd.concat([keep, new], ignore_index=True)
    return combined.sort_values(["symbol", "date"]).reset_index(drop=True)
