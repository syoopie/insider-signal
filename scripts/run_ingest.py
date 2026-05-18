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
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.common import setup_log_tee, log as _log, phase as _phase, fmt_elapsed, load_ticker_universe, load_cik_map, in_universe, log_stored, fetch_and_parse, DERIV_ONLY
from src.db.connection import apply_schema
from src.ingest.edgar import fetch_form4_index
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.ingest.store import (
    upsert_company, insert_filing, insert_transactions,
    update_company_market_data, get_last_filed_date,
    save_signal, mark_signal_alerted, get_unalerted_signals, prune_old_data,
    _clean_ticker,
)
from src.market.prices import get_market_data
from src.signals.scorer import score_transaction, classify_signal
from src.signals.cluster import detect_clusters_for_ticker, get_tickers_with_recent_purchases
from src.signals.formatter import build_evidence
from src.alerts.telegram import send_signal, send_error, send_daily_summary

setup_log_tee("ingest")

INGEST_RATE = 8.0   # req/sec — shared across all threads; EDGAR limit is 10
INGEST_WORKERS = 8  # concurrent XML fetch threads


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

    filings_seen = 0
    filings_stored = 0
    tx_stored = 0
    n_skipped_universe = 0
    n_skipped_no_xml = 0
    n_skipped_deriv  = 0
    n_duplicate = 0

    # Phase 1: fetch index and filter to universe (fast — no XML downloads yet)
    candidates = []
    for filing_meta in fetch_form4_index(start_date, today, req_per_sec=INGEST_RATE):
        filings_seen += 1
        raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
        ticker = cik_to_ticker.get(raw_cik.zfill(10), "").upper()
        if not in_universe(ticker, ticker_universe):
            n_skipped_universe += 1
            continue
        candidates.append((filing_meta, ticker))

    _log(f"  {filings_seen} filings in index, {len(candidates)} pass universe filter")

    # Pre-filter: drop accessions already in DB before spending EDGAR quota on them
    if candidates:
        from src.db.connection import get_conn
        accessions = [fm["accession_number"] for fm, _ in candidates]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT accession_number FROM form4_filings WHERE accession_number = ANY(%s)",
                        (accessions,)
                    )
                    already_stored = {r[0] for r in cur.fetchall()}
            n_pre = sum(1 for fm, _ in candidates if fm["accession_number"] in already_stored)
            if n_pre:
                candidates = [(fm, tk) for fm, tk in candidates if fm["accession_number"] not in already_stored]
                n_duplicate += n_pre
                _log(f"  Pre-filter: {n_pre} already stored, {len(candidates)} remaining")
        except Exception:
            pass  # ON CONFLICT handles it safely if pre-filter fails

    # Phase 2: fetch + parse XMLs in parallel
    parsed_results = []
    with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
        futures = {
            pool.submit(fetch_and_parse, fm, INGEST_RATE): (fm, tk)
            for fm, tk in candidates
        }
        for i, future in enumerate(as_completed(futures), 1):
            fm, tk = futures[future]
            try:
                result = future.result()
            except Exception:
                n_skipped_no_xml += 1
                continue
            if result is None:
                n_skipped_no_xml += 1
                continue
            if result is DERIV_ONLY:
                n_skipped_deriv += 1
                continue
            parsed_results.append((result[0], result[1], tk))

            if i % 50 == 0 or i == len(candidates):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                remaining = len(candidates) - i
                eta = fmt_elapsed(remaining / rate) if rate > 0 else "?"
                _log(f"  Fetched {i}/{len(candidates)}  rate={rate:.1f}/s  ETA={eta}")

    _log(f"  Fetch complete: {len(parsed_results)} parsed, {n_skipped_deriv} deriv-only, {n_skipped_no_xml} fetch/parse errors")

    if not parsed_results:
        _log("  Nothing new to write — all filings filtered or failed")
    else:
        _log(f"  Writing {len(parsed_results)} filings to DB...")

    # Phase 3: write to DB sequentially (psycopg2 not thread-safe)
    for filing_meta, parsed, tk in parsed_results:
        raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
        issuer = parsed.get("issuer", {})
        owner = parsed.get("owner", {})
        cik = issuer.get("cik") or raw_cik
        ticker = _clean_ticker(issuer.get("ticker") or tk) or ""

        upsert_company(cik, ticker, issuer.get("name", ""))

        filing_id = insert_filing(
            filing_meta["accession_number"], cik,
            filing_meta.get("filed_date"), filing_meta.get("period_date"),
        )
        if filing_id is None:
            n_duplicate += 1
            continue

        n = insert_transactions(filing_id, owner, parsed["transactions"])
        tx_stored += n
        filings_stored += 1

        codes = sorted({t.get("transaction_code", "?") for t in parsed["transactions"]})
        log_stored(ticker or raw_cik, filing_meta["accession_number"],
                   len(parsed["transactions"]), codes, filing_meta.get("filed_date", ""))

    elapsed_ingest = time.time() - t0
    _log(f"Ingest complete in {fmt_elapsed(elapsed_ingest)}")
    _log(f"  Seen:    {filings_seen}")
    _log(f"  Stored:  {filings_stored} filings, {tx_stored} transactions")
    _log(f"  Skipped: {n_skipped_universe} not-in-universe, {n_skipped_deriv} deriv-only, "
         f"{n_skipped_no_xml} fetch/parse errors, {n_duplicate} duplicate")

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

        from src.db.connection import get_conn
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.*, f.filed_date, c.cik, c.cap_tier, c.name as company_name
                    FROM transactions t
                    JOIN form4_filings f ON f.id = t.filing_id
                    JOIN companies c ON c.cik = f.cik
                    WHERE c.ticker = %s
                      AND t.transaction_code = 'P'
                      AND t.is_10b51 = FALSE
                      AND t.transaction_date >= %s
                    ORDER BY t.transaction_date DESC
                """, (ticker, recent_date))
                tx_rows = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT t.insider_name, t.transaction_date
                    FROM transactions t
                    JOIN form4_filings f ON f.id = t.filing_id
                    JOIN companies c ON c.cik = f.cik
                    WHERE c.ticker = %s
                      AND t.transaction_code = 'P'
                      AND t.is_10b51 = FALSE
                    ORDER BY t.transaction_date DESC
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
                aggregate_score = max(aggregate_score, result["score"])
                breakdown_combined.update(result["breakdown"])

        if not scored_txs:
            n_no_eligible += 1
            _log(f"  {ticker:<6}  score=n/a  (all transactions ineligible — 10b5-1 or non-P)")
            continue

        is_cluster = cluster_info.get("is_cluster", False)
        cluster_n = cluster_info.get("insider_count", 0)
        signal_type = classify_signal(aggregate_score, is_cluster)

        cluster_tag = f" CLUSTER({cluster_n})" if is_cluster else ""
        effective_cap = (tx_rows[0].get("cap_tier") or mdata.get("cap_tier") or "unknown") if mdata else (tx_rows[0].get("cap_tier") or "unknown")
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
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"FATAL ERROR:\n{tb}")
        send_error(f"{str(e)}\n\n{tb[:500]}", context="daily ingest")
        sys.exit(1)
