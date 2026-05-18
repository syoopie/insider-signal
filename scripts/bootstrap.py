"""
One-time historical backfill: loads 2 years of Form 4 filings into Neon.

Flags:
  --days N    Number of days to backfill from today (default: 730 = 2 years).
  --dry-run   Fetch and parse filings without writing anything to the database.
              Use this to verify EDGAR connectivity and parsing before a real run.
  --force     Skip the per-window duplicate check and re-fetch XML for every
              filing in the date range regardless of what is already stored.
              Use this to repair corrupt or incomplete data. Without --force
              (the default), a single DB query per window identifies already-
              stored accessions and skips their XML fetches — which is far
              cheaper than redundant API calls.

Usage:
  python scripts/bootstrap.py             # full 2-year backfill (skips already-stored)
  python scripts/bootstrap.py --dry-run   # parse only, no DB writes
  python scripts/bootstrap.py --days 90   # backfill last N days
  python scripts/bootstrap.py --force     # re-fetch everything, ignore existing data

Processes date range in 7-day oldest-first chunks.

Performance: index pagination and XML fetching are pipelined — as each index
page arrives, candidates are submitted to a persistent thread pool immediately
rather than waiting for the full window index to download first. WORKERS=32
keeps ~30 requests in-flight at once, which is what's needed to saturate the
9 req/sec EDGAR budget given ~3-4s network latency per request.
DB writes remain in the main thread (psycopg2 is not thread-safe).
"""

import sys
import os
import argparse
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.common import (
    setup_log_tee, log, phase, fmt_elapsed,
    load_ticker_universe, load_cik_map, in_universe, fetch_and_parse,
    DERIV_ONLY,
)
from src.ingest.edgar import fetch_form4_index
from src.ingest.store import get_last_filed_date, _clean_ticker
from src.db.connection import apply_schema, get_conn

log_path = setup_log_tee("bootstrap")
log(f"Logging to {log_path}")

BACKFILL_RATE  = 9.0   # req/sec — shared across all threads; EDGAR limit is 10
WORKERS        = 32    # concurrent XML-fetch threads; saturates 9 req/s at ~3-4s latency
INDEX_WORKERS  = 16    # concurrent index page fetches within one window; shares BACKFILL_RATE
LOG_INTERVAL   = 30    # seconds between status lines
DB_WRITE_BATCH = 50    # flush results to DB after accumulating this many


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--days", type=int, default=730,
                        help="Number of days to backfill (default 730 = 2 years)")
    parser.add_argument("--force", action="store_true",
                        help="Skip per-window duplicate check; re-fetch all XML regardless of what is stored")
    args = parser.parse_args()

    if not args.dry_run:
        phase("DB SETUP")
        apply_schema()
        log("Schema verified")

    phase("UNIVERSE + CIK MAP")
    ticker_universe = load_ticker_universe()
    log(f"Ticker universe: {len(ticker_universe)} tickers loaded")
    cik_to_ticker = load_cik_map(req_per_sec=BACKFILL_RATE)

    end_date   = date.today()
    start_date = end_date - timedelta(days=args.days)

    # 7-day windows, oldest first.
    # EDGAR caps search results at 10,000 per query; a 30-day window has ~24,000
    # Form 4s so we'd silently miss ~60% of them. 7 days ≈ 5,600 filings — safely
    # under the cap with room to spare.
    windows = []
    chunk_start = start_date
    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=7), end_date)
        windows.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    total_windows = len(windows)
    log(f"Backfilling {start_date} → {end_date}  "
        f"({total_windows} windows, {WORKERS} workers, dry_run={args.dry_run})")

    run_start    = time.time()
    last_log_t   = run_start

    filings_seen     = 0
    filings_stored   = 0
    tx_stored        = 0
    skipped_universe = 0
    skipped_duplicate = 0
    skipped_deriv    = 0   # derivative-only filings (Table II only) — not errors
    parse_errors     = 0   # genuine failures: fetch failed or XML malformed
    candidates_total = 0
    candidates_done  = 0
    window_idx       = 0       # updated by the for-loop, read by maybe_log closure

    pending     : dict = {}    # future → (filing_meta, ticker)
    results_buf : list = []    # (filing_meta, parsed, ticker) ready to write

    # ── helpers (closures over the counters above) ────────────────────────────

    def maybe_log(force: bool = False, paginating: bool = False):
        """
        paginating=True  → index still being downloaded; candidates_total is growing,
                           so done/total is misleading and ETA is unknown.
        paginating=False → index fully consumed; candidates_total is the true window
                           total and ETA is meaningful.
        """
        nonlocal last_log_t
        now = time.time()
        if not force and (now - last_log_t) < LOG_INTERVAL:
            return
        elapsed  = now - run_start
        rate     = candidates_done / elapsed if elapsed > 0 else 0
        inflight = candidates_total - candidates_done
        if paginating:
            # Total is still growing — show submitted/done/inflight, omit ETA.
            progress = (
                f"submitted={candidates_total:,}  done={candidates_done:,}  "
                f"inflight={inflight:,}  ETA=? [indexing]"
            )
        else:
            remaining = inflight
            eta = fmt_elapsed(remaining / rate) if rate > 0 and remaining > 0 else "done"
            progress = f"done={candidates_done:,}/{candidates_total:,}  inflight={inflight:,}  ETA={eta}"
        log(
            f"[{fmt_elapsed(elapsed)}]  {progress}  "
            f"stored={filings_stored:,}  tx={tx_stored:,}  "
            f"skip_uni={skipped_universe:,}  skip_dup={skipped_duplicate:,}  "
            f"deriv_only={skipped_deriv:,}  errors={parse_errors}  "
            f"rate={rate:.1f}/s  window={window_idx+1}/{total_windows}"
        )
        last_log_t = now

    def drain_done():
        """Non-blocking: collect any futures that have already finished."""
        nonlocal parse_errors, skipped_deriv, candidates_done
        done = [f for f in list(pending) if f.done()]
        for fut in done:
            fm, tk = pending.pop(fut)
            candidates_done += 1
            try:
                result = fut.result()
                if result is None:
                    parse_errors += 1
                elif result is DERIV_ONLY:
                    skipped_deriv += 1
                else:
                    results_buf.append((result[0], result[1], tk))
            except Exception:
                parse_errors += 1

    def drain_all():
        """Blocking: wait for every currently-pending future to finish."""
        nonlocal parse_errors, skipped_deriv, candidates_done
        futs = list(pending.keys())
        for fut in as_completed(futs):
            fm, tk = pending.pop(fut, (None, None))
            if fm is None:
                continue
            candidates_done += 1
            try:
                result = fut.result()
                if result is None:
                    parse_errors += 1
                elif result is DERIV_ONLY:
                    skipped_deriv += 1
                else:
                    results_buf.append((result[0], result[1], tk))
            except Exception:
                parse_errors += 1
            maybe_log(paginating=False)

    def flush():
        """Write results_buf to DB in one connection, then clear it. Main thread only."""
        nonlocal filings_stored, tx_stored, skipped_duplicate
        if not results_buf:
            return

        if args.dry_run:
            for filing_meta, parsed, tk in results_buf:
                issuer = parsed.get("issuer", {})
                owner  = parsed.get("owner", {})
                ticker = _clean_ticker(issuer.get("ticker") or tk) or ""
                log(f"  [DRY] {ticker} | {owner.get('name')} ({owner.get('role_category')}) "
                    f"| {len(parsed['transactions'])} tx | {filing_meta.get('filed_date')}")
                filings_stored += 1
            results_buf.clear()
            return

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for filing_meta, parsed, tk in results_buf:
                        issuer  = parsed.get("issuer", {})
                        owner   = parsed.get("owner", {})
                        raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
                        cik     = issuer.get("cik") or raw_cik
                        ticker  = _clean_ticker(issuer.get("ticker") or tk) or ""

                        cur.execute(
                            "INSERT INTO companies (cik, ticker, name) VALUES (%s,%s,%s) "
                            "ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker, name=EXCLUDED.name",
                            (cik, ticker.upper() if ticker else None, issuer.get("name", "")),
                        )
                        cur.execute(
                            "INSERT INTO form4_filings "
                            "  (accession_number, cik, filed_date, period_date) "
                            "VALUES (%s,%s,%s,%s) "
                            "ON CONFLICT (accession_number) DO NOTHING RETURNING id",
                            (filing_meta["accession_number"], cik,
                             filing_meta.get("filed_date") or None,
                             filing_meta.get("period_date") or None),
                        )
                        row = cur.fetchone()
                        if not row:
                            skipped_duplicate += 1
                            continue
                        filing_id = row[0]

                        tx_rows = [
                            (
                                filing_id,
                                owner.get("name"), owner.get("role_raw"), owner.get("role_category"),
                                tx.get("transaction_date"), tx.get("transaction_code"),
                                tx.get("shares"), tx.get("price_per_share"),
                                tx.get("total_value"), tx.get("shares_after"),
                                bool(tx.get("is_10b51", False)), bool(tx.get("is_direct", True)),
                            )
                            for tx in parsed.get("transactions", [])
                        ]
                        if tx_rows:
                            cur.executemany(
                                "INSERT INTO transactions "
                                "  (filing_id, insider_name, insider_role, role_category, "
                                "   transaction_date, transaction_code, shares, price_per_share, "
                                "   total_value, shares_after, is_10b51, is_direct) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                tx_rows,
                            )
                        tx_stored      += len(tx_rows)
                        filings_stored += 1
        except Exception as e:
            parse_errors += len(results_buf)
            log(f"  ERROR writing batch to DB: {e}")
        results_buf.clear()

    # ── Main processing loop ──────────────────────────────────────────────────

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for window_idx, (ws, we) in enumerate(windows):
            phase(f"Window {window_idx+1}/{total_windows}: {ws} → {we}")
            window_total = 0
            window_cands = 0

            # Pre-filter: one cheap DB query to find already-stored accessions for
            # this window's date range, so we don't waste XML fetches on them.
            # Bypassed by --force (re-ingest everything).
            window_stored: set = set()
            if not args.force and not args.dry_run:
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT accession_number FROM form4_filings "
                                "WHERE filed_date BETWEEN %s AND %s",
                                (ws, we),
                            )
                            window_stored = {r[0] for r in cur.fetchall()}
                    log(f"  Pre-filter: {len(window_stored):,} accessions already stored")
                except Exception as e:
                    log(f"  Pre-filter query failed ({e}) — relying on ON CONFLICT")

            # Stream the index: submit an XML-fetch future for each candidate as
            # soon as its index record arrives — no need to wait for the full page
            # set to download before fetching XMLs.
            for fm in fetch_form4_index(ws, we, req_per_sec=BACKFILL_RATE, index_workers=INDEX_WORKERS):
                window_total += 1
                filings_seen += 1
                raw_cik = fm.get("cik_raw", "").lstrip("0")
                ticker  = cik_to_ticker.get(raw_cik.zfill(10), "").upper()
                if not in_universe(ticker, ticker_universe):
                    skipped_universe += 1
                    continue

                if fm["accession_number"] in window_stored:
                    skipped_duplicate += 1
                    continue

                window_cands     += 1
                candidates_total += 1
                pending[pool.submit(fetch_and_parse, fm, BACKFILL_RATE)] = (fm, ticker)

                # Non-blocking drain: collect any futures that finished while the
                # index paginator was waiting for its next page.
                if len(pending) > WORKERS:
                    drain_done()

                # Flush to DB whenever the buffer has enough results.
                if len(results_buf) >= DB_WRITE_BATCH:
                    flush()

                maybe_log(paginating=True)

            if window_total >= 10000:
                log("  WARNING: window hit EDGAR 10K cap — some filings may be missing")
            log(f"  {window_total} filings in index, {window_cands} passed filter")
            # candidates_total is now final for this window — switch to ETA-aware logging.
            maybe_log(force=True, paginating=False)

            # Wait for all XML fetches from this window, then commit to DB.
            drain_all()
            flush()
            log(f"  Window {window_idx+1} done.  stored={filings_stored:,}  "
                f"tx={tx_stored:,}  elapsed={fmt_elapsed(time.time()-run_start)}")

    elapsed = time.time() - run_start
    phase("COMPLETE")
    log(f"Elapsed:        {fmt_elapsed(elapsed)}")
    log(f"Filings seen:   {filings_seen:,}")
    log(f"Filings stored: {filings_stored:,}")
    log(f"Transactions:   {tx_stored:,}")
    log(f"Skipped:        {skipped_universe:,} (not in universe)")
    log(f"Duplicates:     {skipped_duplicate:,} (already stored)")
    log(f"Deriv-only:     {skipped_deriv:,} (Table II only — options/warrants, expected)")
    log(f"Fetch/parse errors: {parse_errors:,}")
    if elapsed > 0:
        log(f"Avg rate:       {filings_stored / elapsed:.2f} filings/sec")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"FATAL ERROR:\n{traceback.format_exc()}")
        sys.exit(1)
