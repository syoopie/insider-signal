"""
Backfill signals from transactions already in the database.

Run this after bootstrap to populate the signals table with historical signals
so the dashboard and backtest have data immediately.

No EDGAR fetching — operates entirely on transactions already stored.
No Telegram alerts — this is a batch backfill, not real-time detection.

Signal date is set to filed_date + 1 (same point-in-time rule as live ingest).
Market cap tier is read from the companies table; live Yahoo Finance is not
called because current prices do not represent historical cap tiers.

Performance: loads all relevant transactions in two bulk queries, then
processes entirely in memory to avoid per-item round trips to Neon.

Usage:
  python3 scripts/backfill_signals.py --days 90
  python3 scripts/backfill_signals.py --start 2024-01-01 --end 2024-12-31
  python3 scripts/backfill_signals.py --days 365 --dry-run
  python3 scripts/backfill_signals.py --days 365 --force   # overwrite existing signals
"""

from __future__ import annotations

import sys
import os
import argparse
import time
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from psycopg2.extras import RealDictCursor

from src.ingest.common import setup_log_tee, log, phase, fmt_elapsed
from src.db.connection import get_conn
from src.ingest.store import save_signal
from src.signals.scorer import score_transaction, classify_signal
from src.signals.formatter import build_evidence

setup_log_tee("backfill")

SCORING_WINDOW_DAYS  = 7   # mirror run_ingest: score P transactions from last N days
CLUSTER_WINDOW_DAYS  = 14  # mirror cluster.py
CLUSTER_MIN_INSIDERS = 3


# ── Bulk data loaders ─────────────────────────────────────────────────────────

def _get_work_items(start: date, end: date) -> list[tuple]:
    """Return distinct (filed_date, ticker) pairs with eligible P transactions in range."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT f.filed_date, c.ticker
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                JOIN companies c ON c.cik = f.cik
                WHERE t.transaction_code = 'P'
                  AND t.is_10b51 = FALSE
                  AND f.filed_date BETWEEN %s AND %s
                  AND c.ticker IS NOT NULL
                  AND c.ticker NOT IN ('', 'NONE', 'NA', 'N/A', 'NULL')
                ORDER BY f.filed_date, c.ticker
                """,
                (start, end),
            )
            return cur.fetchall()


def _bulk_load_transactions(tickers: list[str]) -> dict[str, list[dict]]:
    """
    Load ALL P transactions for the given tickers in one query.
    Returns {ticker: [tx_row, ...]} sorted by transaction_date DESC.
    """
    if not tickers:
        return {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT t.*, f.filed_date, c.ticker, c.cik, c.cap_tier,
                       c.name AS company_name
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                JOIN companies c ON c.cik = f.cik
                WHERE t.transaction_code = 'P'
                  AND t.is_10b51 = FALSE
                  AND c.ticker = ANY(%s)
                ORDER BY c.ticker, t.transaction_date DESC
                """,
                (tickers,),
            )
            rows = [dict(r) for r in cur.fetchall()]

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_ticker[row["ticker"]].append(row)
    return by_ticker


def _get_existing_signal_keys(start: date, end: date) -> set[tuple]:
    """Return (ticker, signal_date) pairs already in the signals table."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, signal_date FROM signals WHERE signal_date BETWEEN %s AND %s",
                (start, end),
            )
            return {(r[0], r[1]) for r in cur.fetchall()}


# ── In-memory helpers ─────────────────────────────────────────────────────────

def _get_window_txs(all_ticker_txs: list[dict], filed_date: date) -> tuple[list, list]:
    """
    Split pre-loaded transactions into:
      tx_rows   — transactions in [filed_date - 6, filed_date]  (scoring window)
      all_prior — transactions before the scoring window         (history for routine/first-buy)
    """
    window_start = filed_date - timedelta(days=SCORING_WINDOW_DAYS - 1)
    tx_rows, all_prior = [], []
    for tx in all_ticker_txs:
        td = tx.get("transaction_date")
        if td is None:
            continue
        if isinstance(td, str):
            try:
                td = date.fromisoformat(td[:10])
            except ValueError:
                continue
        if window_start <= td <= filed_date:
            tx_rows.append(tx)
        elif td < window_start:
            all_prior.append(tx)
    return tx_rows, all_prior


def _detect_cluster(all_ticker_txs: list[dict], as_of_date: date) -> dict:
    """Cluster detection from pre-loaded transaction data (mirrors cluster.py logic)."""
    window_start = as_of_date - timedelta(days=CLUSTER_WINDOW_DAYS)
    seen_names: dict[str, dict] = {}
    for tx in all_ticker_txs:
        td = tx.get("transaction_date")
        if td is None:
            continue
        if isinstance(td, str):
            try:
                td = date.fromisoformat(td[:10])
            except ValueError:
                continue
        if window_start <= td <= as_of_date:
            name = tx.get("insider_name") or "Unknown"
            if name not in seen_names:
                seen_names[name] = tx

    insiders = list(seen_names.values())
    return {
        "is_cluster":    len(insiders) >= CLUSTER_MIN_INSIDERS,
        "insider_count": len(insiders),
        "insiders":      insiders,
        "window_start":  window_start,
        "window_end":    as_of_date,
    }


def _score_ticker_txs(
    ticker: str,
    tx_rows: list[dict],
    all_prior: list[dict],
) -> tuple[int, dict, list]:
    """Score all eligible transactions, return (aggregate_score, breakdown, scored_txs)."""
    scored_txs = []
    aggregate_score = 0
    breakdown_combined = {}

    for tx_row in tx_rows:
        cap_tier = tx_row.get("cap_tier") or "unknown"
        owner = {
            "name":          tx_row.get("insider_name"),
            "role_raw":      tx_row.get("insider_role"),
            "role_category": tx_row.get("role_category"),
        }
        company = {"cap_tier": cap_tier}
        mdata   = {"cap_tier": cap_tier}   # no live 52wk low for historical backfill

        prior_for_insider = [
            p for p in all_prior if p.get("insider_name") == owner["name"]
        ]

        result = score_transaction(tx_row, owner, company, mdata, prior_for_insider)
        if result and result.get("eligible"):
            scored_txs.append({"owner": owner, "transaction": tx_row, "score_result": result})
            aggregate_score = max(aggregate_score, result["score"])
            breakdown_combined.update(result["breakdown"])

    return aggregate_score, breakdown_combined, scored_txs


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill signals from stored transactions.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=365,
                       help="Days to backfill from today (default: 365)")
    group.add_argument("--start", type=str,
                       help="Start date YYYY-MM-DD (use with --end)")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score and log without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite signals that already exist in the DB")
    args = parser.parse_args()

    today = date.today()
    if args.start:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end) if args.end else today
    else:
        end   = today
        start = end - timedelta(days=args.days)

    print(f"=== Signal Backfill — {today} ===")
    log(f"Range: {start} → {end}  ({(end - start).days} days)")
    log(f"Dry run: {args.dry_run}   Force overwrite: {args.force}")

    t_start = time.time()

    # ── PREP ──────────────────────────────────────────────────────────────────
    phase("PREP")

    work_items = _get_work_items(start, end)
    log(f"{len(work_items)} (ticker, filed_date) pairs with eligible P transactions")

    if not work_items:
        log("Nothing to process. Run bootstrap.py first to load transaction data.")
        return

    if not args.force:
        existing = _get_existing_signal_keys(
            start + timedelta(days=1), end + timedelta(days=1)
        )
        if existing:
            before = len(work_items)
            work_items = [
                (fd, tk) for fd, tk in work_items
                if (tk, fd + timedelta(days=1)) not in existing
            ]
            log(f"Skipping {before - len(work_items)} already-scored → {len(work_items)} remaining")

    if not work_items:
        log("All pairs already scored. Use --force to overwrite.")
        return

    tickers = list({tk for _, tk in work_items})
    log(f"Bulk-loading transactions for {len(tickers)} tickers...")
    tx_by_ticker = _bulk_load_transactions(tickers)
    total_loaded = sum(len(v) for v in tx_by_ticker.values())
    log(f"Loaded {total_loaded} transactions into memory ({fmt_elapsed(time.time() - t_start)})")

    # ── SCORING ───────────────────────────────────────────────────────────────
    phase("SCORING")

    n_buy = n_cluster = n_watch = n_low = n_ineligible = n_saved = 0
    signals_to_write = []

    for filed_date, ticker in work_items:
        signal_date      = filed_date + timedelta(days=1)
        all_ticker_txs   = tx_by_ticker.get(ticker, [])
        tx_rows, all_prior = _get_window_txs(all_ticker_txs, filed_date)

        if not tx_rows:
            continue

        aggregate_score, breakdown_combined, scored_txs = _score_ticker_txs(
            ticker, tx_rows, all_prior
        )

        if not scored_txs:
            n_ineligible += 1
            continue

        cluster_info = _detect_cluster(all_ticker_txs, filed_date)
        is_cluster   = cluster_info.get("is_cluster", False)
        signal_type  = classify_signal(aggregate_score, is_cluster)

        cap_tier    = tx_rows[0].get("cap_tier") or "unknown"
        cluster_tag = f" CLUSTER({cluster_info['insider_count']})" if is_cluster else ""
        icon        = "✓" if signal_type in ("BUY", "CLUSTER_BUY") else " "
        log(f"  {icon} {ticker:<6}  {signal_date}  score={aggregate_score:>3}  "
            f"{signal_type}{cluster_tag}  cap={cap_tier}  buyers={len(scored_txs)}")

        if signal_type == "LOW":
            n_low += 1
            continue

        company_name = tx_rows[0].get("company_name", ticker)
        mdata        = {"cap_tier": cap_tier}

        evidence = build_evidence(
            ticker=ticker,
            company_name=company_name,
            score=aggregate_score,
            signal_type=signal_type,
            score_breakdown=breakdown_combined,
            cluster_info=cluster_info,
            transactions=scored_txs,
            market_data=mdata,
            filed_date=str(filed_date),
            signal_date=signal_date,
        )

        signals_to_write.append(dict(
            ticker=ticker,
            signal_date=signal_date,
            score=aggregate_score,
            signal_type=signal_type,
            cluster_flag=is_cluster,
            score_breakdown=breakdown_combined,
            evidence=evidence,
        ))

        if signal_type == "CLUSTER_BUY":
            n_cluster += 1
        elif signal_type == "BUY":
            n_buy += 1
        else:
            n_watch += 1

    # ── WRITE ─────────────────────────────────────────────────────────────────
    if signals_to_write and not args.dry_run:
        phase("WRITE")
        log(f"Writing {len(signals_to_write)} signals to DB...")
        for sig in signals_to_write:
            save_signal(**sig)
            n_saved += 1
        log(f"Done.")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    phase("SUMMARY")
    elapsed = time.time() - t_start
    log(f"Completed in {fmt_elapsed(elapsed)}")
    log(f"  CLUSTER_BUY: {n_cluster}  BUY: {n_buy}  WATCH: {n_watch}  "
        f"LOW: {n_low}  ineligible: {n_ineligible}")
    if args.dry_run:
        log(f"  DRY RUN — {len(signals_to_write)} signals would be written (use without --dry-run to commit)")
    else:
        log(f"  Signals written: {n_saved}")
        if n_saved:
            log(f"  Dashboard and backtest now reflect this history.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"FATAL ERROR:\n{traceback.format_exc()}")
        sys.exit(1)
