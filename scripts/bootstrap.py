"""
One-time historical backfill: loads 2 years of Form 4 filings into Neon.

Usage:
  python scripts/bootstrap.py             # full backfill
  python scripts/bootstrap.py --dry-run   # parse only, no DB writes
  python scripts/bootstrap.py --days 90   # backfill last N days

Processes date range in 30-day oldest-first chunks so that resume-on-interrupt
is safe (MAX(filed_date) always reflects the oldest fully-covered window).

XML fetches run in a thread pool to fill the 8 req/sec rate budget
(network latency alone would cap a single thread at ~3 req/sec).
DB writes remain in the main thread for psycopg2 safety.
"""

import sys
import os
import argparse
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.edgar import fetch_form4_index, fetch_filing_xml, fetch_cik_ticker_map
from src.ingest.parser import parse_form4
from src.ingest.store import (
    upsert_company, insert_filing, insert_transactions,
    get_last_filed_date,
)
from typing import Set, Optional, Tuple
from src.db.connection import apply_schema

BACKFILL_RATE = 8.0   # req/sec — shared across all threads; EDGAR limit is 10
WORKERS = 4           # concurrent XML fetch threads
LOG_INTERVAL = 30     # print a status line every N seconds


def load_ticker_universe() -> Set[str]:
    tickers_file = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")
    if not os.path.exists(tickers_file):
        print("WARNING: data/tickers.txt not found. Run scripts/update_tickers.py first.")
        print("Proceeding without universe filter (all issuers).")
        return set()
    with open(tickers_file) as f:
        return {line.strip().upper() for line in f if line.strip()}


def fmt_elapsed(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def fetch_and_parse(filing_meta: dict, rate: float) -> Optional[Tuple[dict, dict]]:
    """Fetch XML and parse it. Returns (filing_meta, parsed) or None. Runs in worker thread."""
    filer_cik = filing_meta.get("filer_cik", filing_meta.get("cik_raw", ""))
    xml = fetch_filing_xml(filing_meta["accession_number"], filer_cik, req_per_sec=rate)
    if not xml:
        return None
    parsed = parse_form4(xml, filing_meta)
    if not parsed or not parsed.get("transactions"):
        return None
    return filing_meta, parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--days", type=int, default=730, help="Number of days to backfill (default 730 = 2 years)")
    parser.add_argument("--force", action="store_true", help="Ignore last stored date and backfill from scratch")
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

    if not args.dry_run and not args.force:
        last = get_last_filed_date()
        if last and last > start_date:
            print(f"Resuming from {last} (last stored filing date)")
            start_date = last

    # 30-day windows, oldest first, so resume is safe
    windows = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=30), end_date)
        windows.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    total_windows = len(windows)
    print(f"Backfilling {start_date} → {end_date}  ({total_windows} windows, {WORKERS} workers)")
    print(f"Dry run: {args.dry_run}")
    print()

    run_start = time.time()
    last_log_time = run_start

    filings_seen = 0
    filings_stored = 0
    tx_stored = 0
    skipped_universe = 0
    parse_errors = 0
    stored_since_last_log = 0

    BATCH = 20
    pending = []

    def maybe_log(force: bool = False):
        nonlocal last_log_time, stored_since_last_log
        now = time.time()
        if not force and (now - last_log_time) < LOG_INTERVAL:
            return
        elapsed = now - run_start
        rate = filings_stored / elapsed if elapsed > 0 else 0
        eta_sec = (total_windows - window_idx - 1) / max(rate / max(filings_seen, 1), 1e-9) if rate > 0 else 0
        print(
            f"  [{fmt_elapsed(elapsed)}]  stored={filings_stored:,}  tx={tx_stored:,}  "
            f"seen={filings_seen:,}  skipped={skipped_universe:,}  errors={parse_errors}  "
            f"rate={rate:.2f} f/s  window={window_idx+1}/{total_windows}"
        )
        last_log_time = now
        stored_since_last_log = 0

    def flush_batch(batch):
        nonlocal filings_stored, tx_stored, parse_errors, stored_since_last_log
        if not batch:
            return
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(fetch_and_parse, fm, BACKFILL_RATE): (fm, tk)
                for fm, tk in batch
            }
            for future in as_completed(futures):
                fm, tk = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    parse_errors += 1
                    continue
                if result is None:
                    parse_errors += 1
                    continue

                filing_meta, parsed = result
                issuer = parsed.get("issuer", {})
                owner = parsed.get("owner", {})
                raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
                cik = issuer.get("cik") or raw_cik
                ticker = issuer.get("ticker") or tk

                if args.dry_run:
                    print(f"  [DRY] {ticker} | {owner.get('name')} ({owner.get('role_category')}) "
                          f"| {len(parsed['transactions'])} tx | {filing_meta.get('filed_date')}")
                    filings_stored += 1
                    continue

                upsert_company(cik, ticker, issuer.get("name", ""))
                filing_id = insert_filing(
                    filing_meta["accession_number"], cik,
                    filing_meta.get("filed_date"), filing_meta.get("period_date"),
                )
                if filing_id is None:
                    continue
                n = insert_transactions(filing_id, owner, parsed["transactions"])
                tx_stored += n
                filings_stored += 1
                stored_since_last_log += 1

        maybe_log()

    for window_idx, (ws, we) in enumerate(windows):
        print(f"── Window {window_idx+1}/{total_windows}: {ws} → {we}  (fetching index...)")

        window_filings = list(fetch_form4_index(ws, we, req_per_sec=BACKFILL_RATE))
        window_filings.reverse()  # EDGAR returns newest-first; flip to oldest-first
        window_total = len(window_filings)

        # Count how many pass the universe filter before touching the network
        window_candidates = []
        for fm in window_filings:
            filings_seen += 1
            raw_cik = fm.get("cik_raw", "").lstrip("0")
            ticker = cik_to_ticker.get(raw_cik.zfill(10), "").upper()
            if ticker_universe and ticker and ticker not in ticker_universe:
                skipped_universe += 1
                continue
            window_candidates.append((fm, ticker))

        print(f"   {window_total} filings in index, {len(window_candidates)} pass universe filter — processing...")

        for fm, tk in window_candidates:
            pending.append((fm, tk))
            if len(pending) >= BATCH:
                flush_batch(pending)
                pending = []

        flush_batch(pending)
        pending = []
        print(f"   Window {window_idx+1} done.  Total stored so far: {filings_stored:,}  tx: {tx_stored:,}  elapsed: {fmt_elapsed(time.time()-run_start)}")

    print()
    elapsed = time.time() - run_start
    print("Bootstrap complete.")
    print(f"  Elapsed:         {fmt_elapsed(elapsed)}")
    print(f"  Filings seen:    {filings_seen:,}")
    print(f"  Filings stored:  {filings_stored:,}")
    print(f"  Transactions:    {tx_stored:,}")
    print(f"  Skipped:         {skipped_universe:,} (not in universe)")
    print(f"  Parse errors:    {parse_errors:,}")
    print(f"  Avg rate:        {filings_stored/elapsed:.2f} filings/sec")


if __name__ == "__main__":
    main()
