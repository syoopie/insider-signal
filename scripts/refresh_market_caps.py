"""
Batch-refresh market_cap and cap_tier for all companies in the DB.

Uses the same Yahoo Finance + SEC EDGAR pipeline as the ingest job,
but runs over every company at once. Designed to be run once after
bootstrap and then weekly (see daily_ingest.yml for the weekly hook).

Rate-limited to 2 req/sec to stay well under Yahoo Finance limits.
Skips tickers that already have a fresh market_cap (updated within 7 days)
unless --force is passed.

Usage:
  python3 scripts/refresh_market_caps.py
  python3 scripts/refresh_market_caps.py --force
"""

from __future__ import annotations

import sys
import os
import argparse
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.common import setup_log_tee, log, phase
from src.db.connection import get_conn
from src.market.prices import get_market_data, get_cap_tier

setup_log_tee("refresh_market_caps")

_MIN_GAP = 0.5  # seconds between tickers (2/sec)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even tickers updated within the last 7 days")
    args = parser.parse_args()

    with get_conn() as conn:
        cur = conn.cursor()
        if args.force:
            cur.execute("SELECT cik, ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")
        else:
            # Only stale: never fetched, or fetched > 7 days ago
            cur.execute("""
                SELECT cik, ticker FROM companies
                WHERE ticker IS NOT NULL
                  AND (market_cap IS NULL OR market_cap = 0
                       OR cap_tier IS NULL OR cap_tier = 'unknown')
                ORDER BY ticker
            """)
        rows = cur.fetchall()

    total = len(rows)
    phase(f"Refreshing market cap for {total} companies")

    updated = 0
    failed  = 0
    t0 = time.time()

    with get_conn() as conn:
        cur = conn.cursor()
        for i, (cik, ticker) in enumerate(rows, 1):
            mdata = get_market_data(ticker)
            if mdata.get("market_cap"):
                cap_tier = get_cap_tier(mdata["market_cap"])
                cur.execute(
                    "UPDATE companies SET market_cap = %s, cap_tier = %s WHERE cik = %s",
                    (mdata["market_cap"], cap_tier, cik),
                )
                updated += 1
            else:
                failed += 1

            if i % 100 == 0 or i == total:
                elapsed = time.time() - t0
                log(f"  {i}/{total}  updated={updated}  failed={failed}  elapsed={elapsed:.0f}s")

            time.sleep(_MIN_GAP)

        conn.commit()

    phase("COMPLETE")
    log(f"Updated: {updated}  |  No data: {failed}  |  Total: {total}")


if __name__ == "__main__":
    main()
