"""
Batch-refresh market_cap and cap_tier for all companies in the DB.

Strategy (fast, fully free):
  1. One bulk call to SEC EDGAR frames API → shares outstanding for ~4,000 companies
  2. Per-ticker YF chart call → current price (already used in ingest)
  3. market_cap = shares × price

Companies not found in EDGAR frames are marked cap_tier='unknown' (scored as
small-cap, which is the safe default given the S&P500+Russell2000 universe).

Rate: 2 req/sec to YF (well under limit).

Usage:
  python3 scripts/refresh_market_caps.py
  python3 scripts/refresh_market_caps.py --force
"""

from __future__ import annotations

import sys
import os
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from src.ingest.common import setup_log_tee, log, phase
from src.db.connection import get_conn
from src.market.prices import get_cap_tier

setup_log_tee("refresh_market_caps")

_YF_URL     = "https://query1.finance.yahoo.com/v8/finance/chart"
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible)"}
_EDGAR_HEADERS = {
    "User-Agent": "InsiderSignal sunyupei19992@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_SLEEP = 0.5  # seconds between YF calls


def _fetch_shares_outstanding() -> dict[str, int]:
    """
    One EDGAR frames call → {CIK_str: shares_outstanding} for all XBRL filers.
    Uses Q4 of the most recent completed year as the most stable annual figure.
    Falls back to Q4 of the prior year if current year isn't available yet.
    """
    for period in ("CY2025Q4I", "CY2024Q4I"):
        try:
            resp = requests.get(
                f"https://data.sec.gov/api/xbrl/frames/us-gaap/CommonStockSharesOutstanding/shares/{period}.json",
                headers=_EDGAR_HEADERS,
                timeout=30,
            )
            if resp.status_code == 200:
                rows = resp.json().get("data", [])
                log(f"  EDGAR frames {period}: {len(rows)} companies")
                return {str(r["cik"]): int(r["val"]) for r in rows if r.get("val")}
        except Exception as e:
            log(f"  EDGAR frames {period} failed: {e}")
    return {}


def _fetch_price(ticker: str) -> float | None:
    try:
        resp = requests.get(
            f"{_YF_URL}/{ticker}",
            params={"interval": "1d", "range": "5d"},
            headers=_YF_HEADERS,
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        meta = resp.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        return meta.get("regularMarketPrice")
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even tickers that already have market_cap set")
    args = parser.parse_args()

    phase("Loading shares outstanding from EDGAR frames")
    shares_by_cik = _fetch_shares_outstanding()
    log(f"  Loaded {len(shares_by_cik)} CIK → shares mappings")

    with get_conn() as conn:
        cur = conn.cursor()
        if args.force:
            cur.execute(
                "SELECT cik, ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker"
            )
        else:
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
    no_shares = 0
    no_price  = 0
    t0 = time.time()

    with get_conn() as conn:
        cur = conn.cursor()
        for i, (cik, ticker) in enumerate(rows, 1):
            shares = shares_by_cik.get(str(cik))
            if not shares:
                no_shares += 1
                # Still try to get price for later; skip cap update
                time.sleep(_SLEEP)
                if i % 100 == 0 or i == total:
                    conn.commit()
                    elapsed = time.time() - t0
                    log(f"  {i}/{total}  updated={updated}  no_shares={no_shares}  "
                        f"no_price={no_price}  elapsed={elapsed:.0f}s")
                continue

            price = _fetch_price(ticker)
            if not price:
                no_price += 1
                time.sleep(_SLEEP)
                if i % 100 == 0 or i == total:
                    conn.commit()
                    elapsed = time.time() - t0
                    log(f"  {i}/{total}  updated={updated}  no_shares={no_shares}  "
                        f"no_price={no_price}  elapsed={elapsed:.0f}s")
                continue

            mc = int(shares * price)
            cap_tier = get_cap_tier(mc)
            cur.execute(
                "UPDATE companies SET market_cap = %s, cap_tier = %s WHERE cik = %s",
                (mc, cap_tier, cik),
            )
            updated += 1

            if i % 100 == 0 or i == total:
                conn.commit()
                elapsed = time.time() - t0
                rate = updated / elapsed if elapsed > 0 else 0
                log(f"  {i}/{total}  updated={updated}  no_shares={no_shares}  "
                    f"no_price={no_price}  elapsed={elapsed:.0f}s  rate={rate:.1f} updates/s")

            time.sleep(_SLEEP)

    phase("COMPLETE")
    log(f"Updated: {updated}  |  No EDGAR shares: {no_shares}  |  No price: {no_price}  |  Total: {total}")


if __name__ == "__main__":
    main()
