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

from typing import Set
from src.db.connection import apply_schema
from src.ingest.edgar import fetch_form4_index, fetch_filing_xml, fetch_cik_ticker_map
from src.ingest.parser import parse_form4
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


INGEST_RATE = 8.0  # req/sec — normal daily mode


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _phase(title: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"\n[{ts}] {'─' * 10} {title} {'─' * 10}", flush=True)


def load_ticker_universe() -> Set[str]:
    tickers_file = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")
    if not os.path.exists(tickers_file):
        return set()
    with open(tickers_file) as f:
        return {line.strip().upper() for line in f if line.strip()}


def main():
    t_start = time.time()
    today = date.today()
    print(f"=== Daily Ingest — {today} (UTC {datetime.utcnow().strftime('%H:%M:%S')}) ===")

    # Ensure schema exists
    _phase("DB SETUP")
    apply_schema()
    _log("Schema verified")

    ticker_universe = load_ticker_universe()
    _log(f"Ticker universe: {len(ticker_universe)} tickers loaded")

    # CIK → ticker mapping
    _phase("CIK MAP")
    t0 = time.time()
    try:
        ticker_to_cik = fetch_cik_ticker_map(req_per_sec=INGEST_RATE)
        cik_to_ticker = {v: k for k, v in ticker_to_cik.items()}
        _log(f"CIK map loaded: {len(cik_to_ticker):,} entries ({time.time()-t0:.1f}s)")
    except Exception as e:
        _log(f"CIK map failed: {e} — continuing without ticker resolution")
        cik_to_ticker = {}

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
    n_skipped_no_tx = 0
    n_duplicate = 0

    for filing_meta in fetch_form4_index(start_date, today, req_per_sec=INGEST_RATE):
        filings_seen += 1
        raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
        ticker = cik_to_ticker.get(raw_cik.zfill(10), "").upper()

        if ticker_universe and ticker and ticker not in ticker_universe:
            n_skipped_universe += 1
            continue

        filer_cik = filing_meta.get("filer_cik", raw_cik)
        xml = fetch_filing_xml(filing_meta["accession_number"], filer_cik, req_per_sec=INGEST_RATE)
        if not xml:
            n_skipped_no_xml += 1
            _log(f"  SKIP no-xml  {filing_meta['accession_number']} ({ticker or raw_cik})")
            continue

        parsed = parse_form4(xml, filing_meta)
        if not parsed or not parsed.get("transactions"):
            n_skipped_no_tx += 1
            continue

        issuer = parsed.get("issuer", {})
        owner = parsed.get("owner", {})
        cik = issuer.get("cik") or raw_cik
        ticker = _clean_ticker(issuer.get("ticker") or ticker) or ""

        upsert_company(cik, ticker, issuer.get("name", ""))

        mdata = get_market_data(ticker) if ticker else {}
        if mdata:
            update_company_market_data(cik, mdata.get("market_cap"), mdata.get("cap_tier"))

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

        n_tx = len(parsed["transactions"])
        codes = set(t.get("transaction_code", "?") for t in parsed["transactions"])
        cap = mdata.get("cap_tier", "?") if mdata else "?"
        _log(f"  STORED  {ticker or raw_cik:<6}  {filing_meta['accession_number']}  "
             f"{n_tx} tx {sorted(codes)}  cap={cap}")

        # Progress heartbeat every 25 stored filings
        if filings_stored % 25 == 0:
            elapsed = time.time() - t0
            rate = filings_stored / elapsed if elapsed > 0 else 0
            _log(f"  ... {filings_stored} stored so far ({rate:.1f} f/s, {elapsed:.0f}s elapsed)")

    elapsed_ingest = time.time() - t0
    _log(f"Ingest complete in {elapsed_ingest:.1f}s")
    _log(f"  Seen:    {filings_seen}")
    _log(f"  Stored:  {filings_stored} filings, {tx_stored} transactions")
    _log(f"  Skipped: {n_skipped_universe} not-in-universe, {n_skipped_no_xml} no-xml, "
         f"{n_skipped_no_tx} no-tx, {n_duplicate} duplicate")

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
                    SELECT t.*, f.filed_date, c.cap_tier, c.name as company_name
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
