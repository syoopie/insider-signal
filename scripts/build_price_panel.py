"""
Fetch the daily price panel that scoring research runs on.

One Yahoo request per symbol for the whole window, for every ticker that has
ever had an open-market purchase, plus the benchmark legs. About 1,400 requests
at the shared 0.5s throttle, so roughly 12 minutes, once.

Resumable and idempotent: a symbol already in the panel is skipped unless
--force, and a refetched symbol replaces its rows rather than duplicating them.
Partial progress is written every --checkpoint symbols, so an interrupted run
loses at most that many.

Usage:
  python3 scripts/build_price_panel.py                # build or resume
  python3 scripts/build_price_panel.py --force        # refetch everything
  python3 scripts/build_price_panel.py --days 1100    # widen the window
  python3 scripts/build_price_panel.py --coverage     # report, fetch nothing
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import pandas as pd

from src.db.connection import get_conn
from src.ingest.common import setup_log_tee, log, phase, fmt_elapsed
from src.market.panel import (
    BENCHMARK_SYMBOLS,
    PANEL_PATH,
    PanelFetchError,
    fetch_symbol_history,
    merge_panels,
    panel_coverage,
    read_panel_frame,
    write_panel,
)

setup_log_tee("build_price_panel")

# Filings start 2024-04-03 and the backtest looks back 730 days. Fetching a
# little beyond both leaves room for trailing windows: a 252-day momentum
# feature on the earliest purchase needs a year of bars before it.
DEFAULT_DAYS = 1100


def _tickers_with_purchases() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT c.ticker
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                JOIN companies c ON c.cik = f.cik
                WHERE t.transaction_code = 'P'
                  AND c.ticker IS NOT NULL AND c.ticker <> ''
                ORDER BY 1
            """)
            return [r[0] for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Calendar days of history to fetch (default {DEFAULT_DAYS})")
    parser.add_argument("--force", action="store_true",
                        help="Refetch symbols already present in the panel")
    parser.add_argument("--checkpoint", type=int, default=100,
                        help="Write partial progress every N symbols (default 100)")
    parser.add_argument("--coverage", action="store_true",
                        help="Print coverage for the existing panel and exit")
    args = parser.parse_args()

    if args.coverage:
        frame = read_panel_frame()
        cov = panel_coverage(frame)
        log(f"Panel: {len(frame):,} rows  {len(cov):,} symbols  file={PANEL_PATH}")
        log(f"  bars per symbol: min={cov['bars'].min()}  "
            f"median={int(cov['bars'].median())}  max={cov['bars'].max()}")
        log(f"  date range: {cov['first'].min().date()} → {cov['last'].max().date()}")
        thin = cov[cov["bars"] < 60].sort_values("bars")
        log(f"  symbols with <60 bars: {len(thin)}")
        for row in thin.head(20).itertuples():
            log(f"    {row.symbol:<8} {row.bars:>4} bars  {row.first.date()} → {row.last.date()}")
        return

    end = date.today()
    start = end - timedelta(days=args.days)

    phase("SYMBOL LIST")
    tickers = _tickers_with_purchases()
    symbols = sorted(set(tickers) | set(BENCHMARK_SYMBOLS))
    log(f"{len(tickers):,} tickers with a P transaction + {len(BENCHMARK_SYMBOLS)} benchmarks "
        f"= {len(symbols):,} symbols")
    log(f"Window: {start} → {end} ({args.days} days)")

    try:
        existing = read_panel_frame()
        have = set(existing["symbol"].unique())
        log(f"Existing panel: {len(existing):,} rows across {len(have):,} symbols")
    except FileNotFoundError:
        existing = None
        have = set()
        log("No existing panel — building from scratch")

    todo = symbols if args.force else [s for s in symbols if s not in have]
    log(f"To fetch: {len(todo):,}  (skipping {len(symbols) - len(todo):,} already present)")
    if not todo:
        log("Nothing to do.")
        return

    phase("FETCH")
    fetched: list[pd.DataFrame] = []
    n_empty = 0
    failures: list[str] = []
    t0 = time.time()

    for i, symbol in enumerate(todo, 1):
        try:
            frame = fetch_symbol_history(symbol, start, end)
        except PanelFetchError as e:
            failures.append(symbol)
            log(f"  ! {symbol:<8} fetch failed: {e}")
            frame = None
        if frame is None:
            n_empty += 1
        else:
            fetched.append(frame)

        if i % args.checkpoint == 0 or i == len(todo):
            existing = merge_panels(existing, fetched)
            write_panel(existing)
            fetched = []
            elapsed = time.time() - t0
            log(f"  {i}/{len(todo)}  rows={len(existing):,}  no_data={n_empty}  "
                f"failed={len(failures)}  elapsed={fmt_elapsed(elapsed)}")

    phase("COVERAGE")
    frame = read_panel_frame()
    cov = panel_coverage(frame)
    log(f"Panel: {len(frame):,} rows across {len(cov):,} symbols → {PANEL_PATH}")
    log(f"  size on disk: {PANEL_PATH.stat().st_size / 1e6:.1f} MB")
    log(f"  bars per symbol: min={cov['bars'].min()}  "
        f"median={int(cov['bars'].median())}  max={cov['bars'].max()}")
    log(f"  date range: {cov['first'].min().date()} → {cov['last'].max().date()}")

    missing = sorted(set(symbols) - set(cov["symbol"]))
    log(f"  symbols with no bars at all: {len(missing)}")
    if missing:
        log(f"    {', '.join(missing[:40])}{' …' if len(missing) > 40 else ''}")
    if failures:
        log(f"  FETCH FAILURES ({len(failures)}) — re-run to retry: {', '.join(failures[:40])}")

    for bench in BENCHMARK_SYMBOLS:
        if bench not in set(cov["symbol"]):
            log(f"  MISSING BENCHMARK: {bench} — the panel cannot compute excess returns")


if __name__ == "__main__":
    main()
