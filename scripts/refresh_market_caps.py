"""
Batch-refresh market_cap and cap_tier for all companies in the DB.

Strategy (three-pass, fully free):
  Pass 1: EDGAR bulk frames — us-gaap/CommonStockSharesOutstanding (~4,200 companies)
  Pass 2: EDGAR bulk frames — dei/EntityCommonStockSharesOutstanding (~2,600 companies)
          Large-caps like LLY, WMT, IT, LUV use DEI instead of us-gaap for this tag.
  Pass 3: EDGAR per-company concept API for remaining unknowns (rate-limited, ~0.3 req/s)
          Handles community banks and newer filers not yet in bulk frames.

  For each company with shares resolved: YF chart → price → market_cap = shares × price.

Rate: 2 req/sec to YF (well under limit), 0.3 req/sec for EDGAR per-company fallback.

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
    Multi-pass bulk fetch → {CIK_str: shares_outstanding}.

    Pass 1: us-gaap/CommonStockSharesOutstanding (~4,200 companies)
    Pass 2: dei/EntityCommonStockSharesOutstanding (~2,600 companies)
            Large-caps (LLY, WMT, IT, LUV, FMC) only file this DEI tag, not us-gaap.
            DEI values fill gaps; us-gaap wins on overlap.
    """
    cik_shares: dict[str, int] = {}

    for taxonomy, concept in [
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("dei",     "EntityCommonStockSharesOutstanding"),
    ]:
        for period in ("CY2025Q4I", "CY2024Q4I"):
            try:
                resp = requests.get(
                    f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/shares/{period}.json",
                    headers=_EDGAR_HEADERS,
                    timeout=30,
                )
                if resp.status_code == 200:
                    rows = resp.json().get("data", [])
                    new_count = 0
                    for r in rows:
                        if r.get("val"):
                            cik_str = str(r["cik"])
                            if cik_str not in cik_shares:   # us-gaap wins on overlap
                                cik_shares[cik_str] = int(r["val"])
                                new_count += 1
                    log(f"  EDGAR frames {taxonomy}/{concept} {period}: {len(rows)} companies ({new_count} new)")
                    break
            except Exception as e:
                log(f"  EDGAR frames {taxonomy}/{concept} {period} failed: {e}")

    return cik_shares


def _fetch_shares_per_company(cik: str) -> int | None:
    """
    Per-company fallback for companies not in bulk frames.
    Queries the EDGAR company concept API for the most recent shares outstanding.
    """
    cik_padded = f"CIK{str(int(cik)).zfill(10)}"
    for taxonomy, concept in [
        ("dei",     "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
    ]:
        try:
            resp = requests.get(
                f"https://data.sec.gov/api/xbrl/companyconcept/{cik_padded}/{taxonomy}/{concept}.json",
                headers=_EDGAR_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                units = resp.json().get("units", {}).get("shares", [])
                # Sort by end date descending, prefer 10-K/10-Q
                units_sorted = sorted(units, key=lambda x: x.get("end", ""), reverse=True)
                for r in units_sorted:
                    if r.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A") and r.get("val"):
                        return int(r["val"])
        except Exception:
            pass
    return None


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


_EDGAR_FALLBACK_SLEEP = 0.35   # ~3 req/sec for per-company EDGAR calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even tickers that already have market_cap set")
    args = parser.parse_args()

    phase("Loading shares outstanding from EDGAR frames")
    shares_by_cik = _fetch_shares_outstanding()
    log(f"  Loaded {len(shares_by_cik)} CIK → shares mappings (from bulk frames)")

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
    fallback_used = 0
    t0 = time.time()

    with get_conn() as conn:
        cur = conn.cursor()
        for i, (cik, ticker) in enumerate(rows, 1):
            shares = shares_by_cik.get(str(cik))

            if not shares:
                # Pass 3: per-company EDGAR concept API fallback
                shares = _fetch_shares_per_company(cik)
                if shares:
                    shares_by_cik[str(cik)] = shares   # cache for any re-run
                    fallback_used += 1
                    time.sleep(_EDGAR_FALLBACK_SLEEP)
                else:
                    no_shares += 1
                    if i % 100 == 0 or i == total:
                        conn.commit()
                        elapsed = time.time() - t0
                        log(f"  {i}/{total}  updated={updated}  fallback={fallback_used}  "
                            f"no_shares={no_shares}  no_price={no_price}  elapsed={elapsed:.0f}s")
                    continue

            price = _fetch_price(ticker)
            if not price:
                no_price += 1
                time.sleep(_SLEEP)
                if i % 100 == 0 or i == total:
                    conn.commit()
                    elapsed = time.time() - t0
                    log(f"  {i}/{total}  updated={updated}  fallback={fallback_used}  "
                        f"no_shares={no_shares}  no_price={no_price}  elapsed={elapsed:.0f}s")
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
                log(f"  {i}/{total}  updated={updated}  fallback={fallback_used}  "
                    f"no_shares={no_shares}  no_price={no_price}  elapsed={elapsed:.0f}s  rate={rate:.1f} u/s")

            time.sleep(_SLEEP)

    phase("COMPLETE")
    log(f"Updated: {updated}  |  Fallback used: {fallback_used}  |  No shares: {no_shares}  "
        f"|  No price: {no_price}  |  Total: {total}")


if __name__ == "__main__":
    main()
