"""
One-time historical backfill: loads 2 years of Form 4 filings into Neon.

Usage:
  python scripts/bootstrap.py             # full backfill
  python scripts/bootstrap.py --dry-run   # parse only, no DB writes
  python scripts/bootstrap.py --days 90   # backfill last N days

Rate limit is intentionally slow (3 req/sec) to avoid EDGAR IP block
during the large burst of requests this script generates.
"""

import sys
import os
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.edgar import fetch_form4_index, fetch_filing_xml, fetch_cik_ticker_map
from src.ingest.parser import parse_form4
from src.ingest.store import (
    upsert_company, insert_filing, insert_transactions,
    update_company_market_data, get_last_filed_date,
)
from src.market.prices import get_market_data
from src.db.connection import apply_schema


BACKFILL_RATE = 3.0  # req/sec — conservative for burst backfill


def load_ticker_universe() -> set[str]:
    tickers_file = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")
    if not os.path.exists(tickers_file):
        print("WARNING: data/tickers.txt not found. Run scripts/update_tickers.py first.")
        print("Proceeding without universe filter (all issuers).")
        return set()
    with open(tickers_file) as f:
        return {line.strip().upper() for line in f if line.strip()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--days", type=int, default=730, help="Number of days to backfill (default 730 = 2 years)")
    args = parser.parse_args()

    if not args.dry_run:
        print("Applying schema...")
        apply_schema()

    ticker_universe = load_ticker_universe()
    print(f"Universe: {len(ticker_universe)} tickers (0 = no filter)")

    print("Fetching CIK → ticker map from SEC...")
    cik_to_ticker = {}
    try:
        ticker_to_cik = fetch_cik_ticker_map(req_per_sec=BACKFILL_RATE)
        cik_to_ticker = {v: k for k, v in ticker_to_cik.items()}
        print(f"  Loaded {len(cik_to_ticker)} CIK mappings")
    except Exception as e:
        print(f"  CIK map fetch failed: {e}")

    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)

    # Resume from last stored date if not dry-run
    if not args.dry_run:
        last = get_last_filed_date()
        if last and last > start_date:
            print(f"Resuming from {last} (last stored filing date)")
            start_date = last

    print(f"Backfilling {start_date} → {end_date}")
    print(f"Dry run: {args.dry_run}")
    print()

    filings_seen = 0
    filings_stored = 0
    tx_stored = 0
    skipped_universe = 0
    parse_errors = 0

    for filing_meta in fetch_form4_index(start_date, end_date, req_per_sec=BACKFILL_RATE):
        filings_seen += 1

        raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
        ticker = cik_to_ticker.get(raw_cik.zfill(10), "").upper()

        # Universe filter
        if ticker_universe and ticker and ticker not in ticker_universe:
            skipped_universe += 1
            continue

        # Fetch XML
        xml = fetch_filing_xml(
            filing_meta["accession_number"],
            raw_cik,
            req_per_sec=BACKFILL_RATE,
        )
        if not xml:
            parse_errors += 1
            continue

        parsed = parse_form4(xml, filing_meta)
        if not parsed or not parsed.get("transactions"):
            continue

        issuer = parsed.get("issuer", {})
        owner = parsed.get("owner", {})
        cik = issuer.get("cik") or raw_cik
        ticker = issuer.get("ticker") or ticker

        if args.dry_run:
            print(f"  [DRY RUN] {ticker} | {owner.get('name')} ({owner.get('role_category')}) "
                  f"| {len(parsed['transactions'])} tx | filed {filing_meta.get('filed_date')}")
            filings_stored += 1
            continue

        # Upsert company
        upsert_company(cik, ticker, issuer.get("name", ""))

        # Fetch market data for new companies (throttled)
        mdata = get_market_data(ticker) if ticker else {}
        if mdata:
            update_company_market_data(cik, mdata.get("market_cap"), mdata.get("cap_tier"))

        # Insert filing
        filing_id = insert_filing(
            filing_meta["accession_number"],
            cik,
            filing_meta.get("filed_date"),
            filing_meta.get("period_date"),
        )
        if filing_id is None:
            continue  # Already stored

        # Insert transactions
        n = insert_transactions(filing_id, owner, parsed["transactions"])
        tx_stored += n
        filings_stored += 1

        if filings_stored % 100 == 0:
            print(f"  Progress: {filings_stored} filings stored, {tx_stored} transactions, "
                  f"{skipped_universe} skipped (universe filter)")

    print()
    print("Bootstrap complete.")
    print(f"  Filings seen:    {filings_seen}")
    print(f"  Filings stored:  {filings_stored}")
    print(f"  Transactions:    {tx_stored}")
    print(f"  Skipped:         {skipped_universe} (not in universe)")
    print(f"  Parse errors:    {parse_errors}")


if __name__ == "__main__":
    main()
