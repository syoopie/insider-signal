"""
Daily ingest entrypoint. Called by GitHub Actions daily_ingest.yml.

Flow:
  1. Get last stored filing date from DB
  2. Fetch new Form 4s from EDGAR since that date
  3. Filter to ticker universe
  4. Parse, score, detect clusters
  5. Save signals + send Telegram alerts
  6. Prune old data on 1st of month

Entire script is wrapped in try/except — any failure sends a Telegram
error notification so pipeline issues are never silent.
"""

import sys
import os
import time
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.common import (
    setup_log_tee, log as _log, phase as _phase, fmt_elapsed,
    load_ticker_universe, load_cik_map, in_universe, fetch_and_parse,
    DERIV_ONLY, XML_MISSING, PARSE_ERROR, resolve_ticker,
    EdgarRateLimitError, EdgarBlockedError, EdgarServerError,
)
from src.db.connection import apply_schema, get_conn
from src.ingest.edgar import fetch_form4_index
from src.ingest.store import (
    write_filing,
    update_company_market_data, get_last_filed_date,
    save_signal, mark_signal_alerted, get_unalerted_signals, prune_old_data,
)
from src.market.prices import get_market_data
from src.signals.scorer import score_transaction, classify_signal
from src.signals.cluster import detect_clusters_for_ticker, get_tickers_with_recent_purchases
from src.signals.formatter import build_evidence
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
    last_date = get_last_filed_date()
    earliest_allowed = today - timedelta(days=7)
    start_date = max(last_date, earliest_allowed) if last_date else earliest_allowed
    _log(f"Last stored filing date: {last_date or 'none'}")
    _log(f"Fetch window: {start_date} → {today} ({(today - start_date).days} days)")

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

    # ── SIGNAL SCORING ────────────────────────────────────────────────────────
    _phase("SIGNAL SCORING")
    t0 = time.time()

    recent_date = today - timedelta(days=7)
    tickers_to_score = get_tickers_with_recent_purchases(recent_date)
    _log(f"Tickers with purchases in past 7 days: {len(tickers_to_score)}")

    n_buy = n_cluster = n_watch = 0
    n_low = n_no_eligible = 0

    for ticker in tickers_to_score:
        cluster_info = detect_clusters_for_ticker(ticker, today)
        mdata = get_market_data(ticker) if ticker else {}

        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM (
                        SELECT DISTINCT ON (t.insider_name, t.transaction_date, t.transaction_code)
                            t.*, f.filed_date, c.cik, c.cap_tier, c.name as company_name
                        FROM transactions t
                        JOIN form4_filings f ON f.id = t.filing_id
                        JOIN companies c ON c.cik = f.cik
                        WHERE c.ticker = %s
                          AND t.transaction_code = 'P'
                          AND t.transaction_date >= %s
                        ORDER BY t.insider_name, t.transaction_date, t.transaction_code,
                                 f.filed_date DESC
                    ) deduped
                    WHERE is_10b51 = FALSE
                    ORDER BY transaction_date DESC
                """, (ticker, recent_date))
                tx_rows = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT insider_name, transaction_date FROM (
                        SELECT DISTINCT ON (t.insider_name, t.transaction_date, t.transaction_code)
                            t.insider_name, t.transaction_date, t.is_10b51
                        FROM transactions t
                        JOIN form4_filings f ON f.id = t.filing_id
                        JOIN companies c ON c.cik = f.cik
                        WHERE c.ticker = %s
                          AND t.transaction_code = 'P'
                        ORDER BY t.insider_name, t.transaction_date, t.transaction_code,
                                 f.filed_date DESC
                    ) deduped
                    WHERE is_10b51 = FALSE
                    ORDER BY transaction_date DESC
                """, (ticker,))
                all_prior = [dict(r) for r in cur.fetchall()]

        if not tx_rows:
            continue

        if mdata:
            update_company_market_data(tx_rows[0].get("cik"), mdata.get("market_cap"), mdata.get("cap_tier"))
        else:
            _log(f"  {ticker:<6}  market data unavailable (cap=unknown)")

        scored_txs = []
        aggregate_score = 0
        breakdown_combined = {}

        for tx_row in tx_rows:
            owner = {
                "name": tx_row.get("insider_name"),
                "role_raw": tx_row.get("insider_role"),
                "role_category": tx_row.get("role_category"),
            }
            prior_for_insider = [p for p in all_prior if p.get("insider_name") == owner["name"]]
            company = {"cap_tier": tx_row.get("cap_tier") or (mdata.get("cap_tier") if mdata else None)}

            result = score_transaction(tx_row, owner, company, mdata, prior_for_insider)
            if result and result.get("eligible"):
                scored_txs.append({"owner": owner, "transaction": tx_row, "score_result": result})
                if result["score"] > aggregate_score:
                    aggregate_score = result["score"]
                    breakdown_combined = result["breakdown"]

        if not scored_txs:
            n_no_eligible += 1
            _log(f"  {ticker:<6}  score=n/a  (all transactions ineligible — 10b5-1 or non-P)")
            continue

        participant_scores = [stx["score_result"]["score"] for stx in scored_txs]
        is_cluster    = cluster_info.get("is_cluster", False)
        tight_cluster = cluster_info.get("tight_cluster", False)
        cluster_n     = cluster_info.get("insider_count", 0)
        signal_type   = classify_signal(aggregate_score, is_cluster, participant_scores, tight_cluster)

        effective_cap = (tx_rows[0].get("cap_tier") or mdata.get("cap_tier") or "unknown") if mdata else (tx_rows[0].get("cap_tier") or "unknown")
        # Large-cap clusters have near-zero alpha (0% hit at 90d, -16% avg excess).
        if signal_type == "CLUSTER_BUY" and effective_cap == "large":
            signal_type = "WATCH"
        cluster_tag = f"(n={cluster_n})" if is_cluster else ""
        _log(f"  {ticker:<6}  score={aggregate_score:>3}  {signal_type}{cluster_tag}  "
             f"cap={effective_cap}  buyers={len(scored_txs)}")

        if signal_type == "LOW":
            n_low += 1
            continue

        company_name = tx_rows[0].get("company_name", ticker)
        filed_date = tx_rows[0].get("filed_date", "")

        evidence = build_evidence(
            ticker=ticker,
            company_name=company_name,
            score=aggregate_score,
            signal_type=signal_type,
            score_breakdown=breakdown_combined,
            cluster_info=cluster_info,
            transactions=scored_txs,
            market_data=mdata,
            filed_date=str(filed_date) if filed_date else "",
            signal_date=today,
        )

        signal_id = save_signal(
            ticker=ticker,
            signal_date=today,
            score=aggregate_score,
            signal_type=signal_type,
            cluster_flag=is_cluster,
            score_breakdown=breakdown_combined,
            evidence=evidence,
        )
        _log(f"  {ticker:<6}  signal saved (id={signal_id})")

        if signal_type in ("BUY", "CLUSTER_BUY"):
            sent = send_signal(evidence)
            _log(f"  {ticker:<6}  Telegram alert {'SENT' if sent else 'FAILED'}")
            if sent:
                mark_signal_alerted(signal_id)
            if signal_type == "CLUSTER_BUY":
                n_cluster += 1
            else:
                n_buy += 1
        else:
            n_watch += 1

    elapsed_score = time.time() - t0
    _log(f"Scoring complete in {elapsed_score:.1f}s")
    _log(f"  CLUSTER_BUY: {n_cluster}  BUY: {n_buy}  WATCH: {n_watch}  "
         f"LOW: {n_low}  ineligible: {n_no_eligible}")

    # ── MONTHLY PRUNING ───────────────────────────────────────────────────────
    if today.day == 1:
        _phase("MONTHLY PRUNE")
        tx_del, filing_del = prune_old_data(months=24)
        _log(f"Pruned {tx_del} transactions and {filing_del} filings older than 2 years")

    # ── DAILY SUMMARY ─────────────────────────────────────────────────────────
    _phase("WRAP UP")
    total = n_cluster + n_buy + n_watch
    sent = send_daily_summary(total, n_buy, n_cluster, n_watch)
    _log(f"Daily summary Telegram {'SENT' if sent else 'FAILED (not configured or error)'}")

    ts_path = os.path.join(os.path.dirname(__file__), "..", "last_run.txt")
    with open(ts_path, "w") as f:
        f.write(f"{today.isoformat()}\n")
    _log(f"last_run.txt updated")

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
