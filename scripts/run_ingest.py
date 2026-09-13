"""
Daily ingest entrypoint. Called by GitHub Actions daily_ingest.yml.

Flow:
  1. Get last stored filing date from DB
  2. Fetch new Form 4s from EDGAR since that date
  3. Filter to ticker universe
  4. Rebuild the signals for every filing in the window (src/signals/rebuild.py)
  5. Send Telegram alerts for BUY / CLUSTER_BUY carrying a filing from this run
  6. Prune old data on 1st of month

Entire script is wrapped in try/except — any failure sends a Telegram
error notification so pipeline issues are never silent.
"""

import sys
import os
import time
from collections import Counter
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.log import setup_log_tee, log as _log, phase as _phase, fmt_elapsed
from src.tickers import load_ticker_universe, in_universe, resolve_ticker
from src.ingest.fetch import load_cik_map, fetch_and_parse, DERIV_ONLY, XML_MISSING, PARSE_ERROR
from src.ingest.edgar import EdgarBlockedError, EdgarRateLimitError, EdgarServerError, fetch_form4_index
from src.db.connection import apply_schema, get_conn
from src.db.signals import mark_signal_alerted, unsent_alerts_filed_since
from src.db.store import (
    write_filing, fill_missing_price_context, get_last_filed_date, prune_old_data,
    RETENTION_MONTHS,
)
from src.signals.batch import SCORING_WINDOW_DAYS
from src.signals.rebuild import rebuild_signals
from src.alerts.telegram import send_signal, send_error, send_daily_summary

setup_log_tee("ingest")

INGEST_RATE    = 9.0   # req/sec — shared across all threads; EDGAR limit is 10
INGEST_WORKERS = 32    # concurrent XML fetch threads; saturates 9 req/s at ~3-4s latency


def main():
    t_start = time.time()
    today = date.today()
    print(f"=== Daily Ingest — {today} (UTC {datetime.utcnow().strftime('%H:%M:%S')}) ===")

    # Ensure schema exists
    _phase("DB SETUP")
    apply_schema()
    _log("Schema verified")

    _phase("UNIVERSE + CIK MAP")
    ticker_universe = load_ticker_universe()
    _log(f"Ticker universe: {len(ticker_universe)} tickers loaded")
    cik_to_ticker = load_cik_map(req_per_sec=INGEST_RATE)

    # Date range: from last stored to today, capped at 7 days back.
    #
    # The cap exists because EDGAR's search API reports at most 10,000 hits for a
    # query and returns them newest-first, so a window wide enough to exceed that
    # silently loses its oldest days. A 7-day window peaks near 5,600.
    #
    # The cap also means an outage longer than the window is permanent data loss:
    # nothing else ever revisits those days. Say so loudly rather than skipping
    # them in silence, because the fix is a manual bootstrap over the gap.
    last_date = get_last_filed_date()
    earliest_allowed = today - timedelta(days=7)
    start_date = max(last_date, earliest_allowed) if last_date else earliest_allowed
    _log(f"Last stored filing date: {last_date or 'none'}")
    _log(f"Fetch window: {start_date} → {today} ({(today - start_date).days} days)")

    if last_date and last_date < earliest_allowed:
        gap_days = (earliest_allowed - last_date).days
        msg = (f"Ingest gap: last stored filing is {last_date}, but the fetch window "
               f"only reaches back to {earliest_allowed}. {gap_days} day(s) will never "
               f"be ingested by the daily job.\n\n"
               f"Backfill them with:\n"
               f"  python scripts/bootstrap.py --start {last_date} --end {earliest_allowed}")
        _log(f"WARNING: {msg}")
        send_error(msg, context="daily ingest — coverage gap")

    # ── FILING INGEST ─────────────────────────────────────────────────────────
    _phase("FILING INGEST")
    t0 = time.time()

    filings_seen     = 0
    filings_stored   = 0
    tx_stored        = 0
    n_skipped_universe = 0
    n_xml_missing    = 0
    n_parse_error    = 0
    n_skipped_deriv  = 0
    n_duplicate      = 0

    # Pre-filter: load already-stored accessions for the window before touching EDGAR.
    window_stored: set = set()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT accession_number FROM form4_filings WHERE filed_date BETWEEN %s AND %s",
                    (start_date, today),
                )
                window_stored = {r[0] for r in cur.fetchall()}
        _log(f"  Pre-filter: {len(window_stored):,} accessions already stored")
    except Exception as e:
        _log(f"  Pre-filter failed ({e}) — relying on ON CONFLICT")

    # Stream index → submit XML fetches immediately (pipeline: overlap index + XML).
    parsed_results = []
    pending: dict = {}
    with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
        for filing_meta in fetch_form4_index(start_date, today, req_per_sec=INGEST_RATE):
            filings_seen += 1
            ticker = resolve_ticker(filing_meta, cik_to_ticker)
            if not in_universe(ticker, ticker_universe):
                n_skipped_universe += 1
                continue
            if filing_meta["accession_number"] in window_stored:
                n_duplicate += 1
                continue
            pending[pool.submit(fetch_and_parse, filing_meta, INGEST_RATE)] = (filing_meta, ticker)

        _log(f"  {filings_seen} in index, {len(pending)} submitted for XML fetch "
             f"({n_skipped_universe} not-in-universe, {n_duplicate} pre-filtered)")

        # EDGAR's search API caps a query at 10,000 hits, newest first, so at the
        # ceiling the oldest days of the window are silently absent. bootstrap.py
        # already warns on this; the daily job never did.
        if filings_seen >= 10_000:
            send_error(
                f"Index returned {filings_seen} hits for {start_date} → {today}, at or "
                f"above EDGAR's 10,000 result cap. The oldest days in that window were "
                f"truncated. Re-ingest them with a narrower bootstrap range.",
                context="daily ingest — EDGAR result cap",
            )
            _log("  WARNING: hit EDGAR's 10,000 result cap — oldest filings truncated")

        for future in as_completed(pending):
            fm, tk = pending[future]
            try:
                result = future.result()
            except (EdgarRateLimitError, EdgarBlockedError, EdgarServerError):
                raise
            except Exception:
                n_parse_error += 1
                continue
            if result is XML_MISSING:
                n_xml_missing += 1
            elif result is PARSE_ERROR:
                n_parse_error += 1
            elif result is DERIV_ONLY:
                n_skipped_deriv += 1
            else:
                parsed_results.append((result[0], result[1], tk))

    _log(f"  Fetch complete: {len(parsed_results)} parsed, {n_skipped_deriv} deriv-only, "
         f"{n_xml_missing} no-xml, {n_parse_error} parse-errors")

    if not parsed_results:
        _log("  Nothing new to write — all filings filtered or failed")
    else:
        _log(f"  Writing {len(parsed_results)} filings to DB...")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '8s'")
                cur.execute("SET idle_in_transaction_session_timeout = '120s'")
                for filing_meta, parsed, tk in parsed_results:
                    filing_id, n = write_filing(cur, filing_meta, parsed, tk)
                    if filing_id == 0:
                        n_duplicate += 1
                    else:
                        tx_stored += n
                        filings_stored += 1


    elapsed_ingest = time.time() - t0
    _log(f"Ingest complete in {fmt_elapsed(elapsed_ingest)}")
    _log(f"  Seen:    {filings_seen}")
    _log(f"  Stored:  {filings_stored} filings, {tx_stored} transactions")
    _log(f"  Skipped: {n_skipped_universe} not-in-universe, {n_skipped_deriv} deriv-only, "
         f"{n_xml_missing} no-xml, {n_parse_error} parse-errors, {n_duplicate} duplicate")

    # ── SIGNALS ───────────────────────────────────────────────────────────────
    _phase("SIGNALS")
    t0 = time.time()

    # Every filing from this run's fetch window, and at least one scoring window,
    # rebuilt by the same function the backfill uses.
    rebuild_start = min(start_date, today - timedelta(days=SCORING_WINDOW_DAYS - 1))

    # The score is built on price context stored on the transaction row. Fill it
    # before scoring, or every filing written today scores zero.
    attempted, ranked = fill_missing_price_context(rebuild_start)
    if attempted:
        _log(f"Price context: {ranked}/{attempted} purchases rankable")
        if ranked < attempted:
            _log(f"  {attempted - ranked} have under 200 bars of history and "
                 f"will score 0 rather than be ranked on a partial year")

    rebuild = rebuild_signals(rebuild_start, today)
    counts = rebuild.counts()
    replaced = rebuild.replaced
    _log(f"Rebuilt filings {rebuild_start} → {today} in {time.time() - t0:.1f}s")
    _log(f"  CLUSTER_BUY: {counts['CLUSTER_BUY']}  BUY: {counts['BUY']}  "
         f"WATCH: {counts['WATCH']}  LOW: {counts['LOW']}  ineligible: {rebuild.ineligible}")
    _log(f"  stored {replaced.written}, {replaced.suppressed} suppressed by the cooldown, "
         f"{replaced.deduped} deduplicated, {replaced.alerts_kept} already sent")

    # A signal whose filings all predate this run's fetch window was evaluated,
    # and alerted if it qualified, by an earlier run. Only the rest can be news.
    for alert in unsent_alerts_filed_since(start_date):
        sent = send_signal(alert["evidence"])
        _log(f"  {alert['ticker']:<6}  {alert['signal_type']:<11}  "
             f"Telegram alert {'SENT' if sent else 'FAILED'}")
        if sent:
            mark_signal_alerted(alert["id"])

    new = Counter(s.signal_type for s in rebuild.signals if s.filed_date >= start_date)

    # ── MONTHLY PRUNING ───────────────────────────────────────────────────────
    if today.day == 1:
        _phase("MONTHLY PRUNE")
        tx_del, filing_del, sig_del = prune_old_data()
        _log(f"Pruned {tx_del} transactions, {filing_del} filings and {sig_del} signals "
             f"older than {RETENTION_MONTHS} months")

    # ── DAILY SUMMARY ─────────────────────────────────────────────────────────
    _phase("WRAP UP")
    total = new["CLUSTER_BUY"] + new["BUY"] + new["WATCH"]
    sent = send_daily_summary(total, new["BUY"], new["CLUSTER_BUY"], new["WATCH"])
    _log(f"Daily summary Telegram {'SENT' if sent else 'FAILED (not configured or error)'}")

    ts_path = os.path.join(os.path.dirname(__file__), "..", "last_run.txt")
    with open(ts_path, "w") as f:
        f.write(f"{today.isoformat()}\n")
    _log("last_run.txt updated")

    total_elapsed = time.time() - t_start
    _log(f"=== Done in {total_elapsed:.1f}s ===")


if __name__ == "__main__":
    try:
        main()
    except EdgarRateLimitError as e:
        msg = f"EDGAR rate limit (429): {e}\nEDGAR is throttling this IP — retry in 15+ minutes."
        print(f"\nFATAL: {msg}")
        send_error(msg, context="daily ingest — rate limited")
        sys.exit(1)
    except EdgarBlockedError as e:
        msg = f"EDGAR access blocked (403): {e}\nCheck USER_AGENT in edgar.py."
        print(f"\nFATAL: {msg}")
        send_error(msg, context="daily ingest — blocked")
        sys.exit(1)
    except EdgarServerError as e:
        msg = f"EDGAR server error after retries: {e}"
        print(f"\nFATAL: {msg}")
        send_error(msg, context="daily ingest — server error")
        sys.exit(1)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"FATAL ERROR:\n{tb}")
        send_error(f"{str(e)}\n\n{tb[:500]}", context="daily ingest")
        sys.exit(1)
