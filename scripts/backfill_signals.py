"""
Backfill signals from transactions already in the database.

Run this after bootstrap to populate the signals table with historical signals
so the dashboard and backtest have data immediately.

No EDGAR fetching — operates entirely on transactions already stored.
No Telegram alerts — this is a batch backfill, not real-time detection.

Signal date is the date of the latest purchase in the window, matching live
ingest. It is NOT filed_date + 1; the backtest avoids look-ahead by deriving
exec_date from evidence.filed_date instead. See "Signal dating" in CLAUDE.md.

Market cap tier is read from the companies table; live Yahoo Finance is not
called because current prices do not represent historical cap tiers.

Performance: loads all relevant transactions in two bulk queries, then
processes entirely in memory to avoid per-item round trips to Neon.

Flags:
  --days N         Backfill last N days from today (default: 365).
  --start / --end  Explicit date range instead of --days.
  --dry-run        Score and log without writing anything to the database.
                   Use this to preview what would be written before committing.
  --force          Overwrite signals that already exist in the signals table
                   (same ticker + signal_date). Without --force, existing rows
                   are skipped so the script is safe to re-run incrementally.
                   Use --force after re-scoring rule changes or to repair data.

Usage:
  python3 scripts/backfill_signals.py --days 90
  python3 scripts/backfill_signals.py --start 2024-01-01 --end 2024-12-31
  python3 scripts/backfill_signals.py --days 365 --dry-run
  python3 scripts/backfill_signals.py --days 365 --force
"""

from __future__ import annotations

import sys
import argparse
import time
from collections import defaultdict
from datetime import date, timedelta

from psycopg2.extras import RealDictCursor

from src.ingest.common import setup_log_tee, log, phase, fmt_elapsed
from src.db.connection import get_conn
from src.db.purchases import purchase_rollup
from src.db.store import batch_save_signals, get_history_start
from src.signals.cluster import cluster_from_transactions
from src.signals.scorer import score_transaction, classify_signal, cluster_size_bonus, filing_lag_bonus
from src.signals.formatter import build_evidence

setup_log_tee("backfill")

SCORING_WINDOW_DAYS = 7   # mirror run_ingest: score P transactions from last N days


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
    sql = f"""
        SELECT * FROM ({purchase_rollup('AND c.ticker = ANY(%s)')}) rolled
        WHERE is_10b51 IS NOT TRUE
        ORDER BY ticker, transaction_date DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (tickers,))
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
      tx_rows   — purchases DISCLOSED in [filed_date - 6, filed_date] (scoring window)
      all_prior — purchases disclosed or transacted before it         (history)

    Windowing on filed_date mirrors live ingest. Windowing on transaction_date,
    as this did, silently dropped every trade reported more than a week late.
    """
    window_start = filed_date - timedelta(days=SCORING_WINDOW_DAYS - 1)
    tx_rows, all_prior = [], []
    for tx in all_ticker_txs:
        fd = tx.get("filed_date")
        td = tx.get("transaction_date")
        if fd is None or td is None:
            continue
        if isinstance(fd, str):
            try:
                fd = date.fromisoformat(fd[:10])
            except ValueError:
                continue
        if isinstance(td, str):
            try:
                td = date.fromisoformat(td[:10])
            except ValueError:
                continue
        if window_start <= fd <= filed_date:
            tx_rows.append(tx)
        elif fd < window_start:
            all_prior.append(tx)
    return tx_rows, all_prior


# Cluster detection lives in src/signals/cluster.py. Backfill pre-loads all of a
# ticker's P transactions in one bulk query and hands them to the same function
# the live path uses, so "what counts as a cluster" is defined in exactly one
# place. `_bulk_load_transactions` already excludes 10b5-1 trades and orders
# rows newest-first, which is what cluster_from_transactions expects.


def _disclosed_by(all_ticker_txs: list[dict], as_of: date) -> list[dict]:
    """
    Rows whose filing had already landed by as_of. Cluster detection previously
    saw every row for the ticker and filtered only on transaction_date, so a
    purchase inside the 14-day window but filed afterwards could form a cluster
    before it was public.
    """
    out = []
    for tx in all_ticker_txs:
        fd = tx.get("filed_date")
        if isinstance(fd, str):
            try:
                fd = date.fromisoformat(fd[:10])
            except ValueError:
                continue
        if fd is not None and fd <= as_of:
            out.append(tx)
    return out


def _score_ticker_txs(
    ticker: str,
    tx_rows: list[dict],
    all_prior: list[dict],
    history_start: date | None = None,
) -> tuple[int, dict, list, list]:
    """
    Score all eligible transactions.
    Returns (aggregate_score, breakdown, scored_txs, participant_scores).
    aggregate_score: max individual score (used for BUY threshold).
    participant_scores: all individual eligible scores (used for cluster avg).
    """
    scored_txs = []
    aggregate_score = 0
    breakdown_combined = {}
    participant_scores = []

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

        result = score_transaction(tx_row, owner, company, mdata, prior_for_insider,
                                   history_start=history_start)
        if result and result.get("eligible"):
            scored_txs.append({"owner": owner, "transaction": tx_row, "score_result": result})
            participant_scores.append(result["score"])
            if result["score"] > aggregate_score:
                aggregate_score = result["score"]
                breakdown_combined = result["breakdown"]

    return aggregate_score, breakdown_combined, scored_txs, participant_scores


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

    history_start = get_history_start()
    log(f"History floor for first-purchase checks: {history_start or 'unknown'}")

    if not work_items:
        log("Nothing to process. Run bootstrap.py first to load transaction data.")
        return

    # Skipping already-scored work needs the ticker's signal dates, not
    # filed_date + 1. That key dates from when signal_date really was
    # filed_date + 1, so the lookup always missed and every run rescored
    # everything. Compare against the set of dates present for the ticker.
    if not args.force:
        existing = _get_existing_signal_keys(start - timedelta(days=400), end)
        if existing:
            dates_by_ticker: dict[str, set] = defaultdict(set)
            for tk, sd in existing:
                dates_by_ticker[tk].add(sd)
            before = len(work_items)
            work_items = [
                (fd, tk) for fd, tk in work_items
                if not any(abs((sd - fd).days) <= SCORING_WINDOW_DAYS
                           for sd in dates_by_ticker.get(tk, ()))
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

    # ── SCORING + incremental writes ──────────────────────────────────────────
    phase("SCORING")

    _FLUSH_EVERY = 200          # write to DB every N queued signals
    n_buy = n_cluster = n_watch = n_low = n_ineligible = 0
    n_saved = 0
    n_dry_run_total = 0         # for --dry-run reporting
    pending = []                # signals queued for next DB flush

    def _flush(label: str = "") -> None:
        nonlocal n_saved, pending
        if not pending or args.dry_run:
            return
        flushed = batch_save_signals(pending)
        n_saved += flushed
        tag = f"  ({label})" if label else ""
        log(f"  [write] {flushed} → DB{tag}  total={n_saved}")
        pending = []

    for filed_date, ticker in work_items:
        all_ticker_txs   = tx_by_ticker.get(ticker, [])
        tx_rows, all_prior = _get_window_txs(all_ticker_txs, filed_date)

        if not tx_rows:
            continue

        # Date of the latest purchase in the window (tx_rows sorted DESC by transaction_date)
        signal_date = tx_rows[0].get("transaction_date") or (filed_date + timedelta(days=1))

        aggregate_score, breakdown_combined, scored_txs, participant_scores = _score_ticker_txs(
            ticker, tx_rows, all_prior, history_start
        )

        if not scored_txs:
            n_ineligible += 1
            continue

        cluster_info  = cluster_from_transactions(_disclosed_by(all_ticker_txs, filed_date), filed_date)
        is_cluster    = cluster_info.get("is_cluster", False)
        tight_cluster = cluster_info.get("tight_cluster", False)
        cluster_n     = cluster_info.get("insider_count", 0)

        # --- Signal-level bonuses (cluster size + filing urgency) ---
        if is_cluster and cluster_n >= 4:
            cs_pts, cs_factor = cluster_size_bonus(cluster_n)
            if cs_pts > 0:
                aggregate_score = min(aggregate_score + cs_pts, 100)
                breakdown_combined = dict(breakdown_combined)
                breakdown_combined[cs_factor] = cs_pts

        lags = []
        for tx in tx_rows:
            fd = tx.get("filed_date")
            td = tx.get("transaction_date")
            if fd and td:
                try:
                    if not hasattr(fd, "year"): fd = date.fromisoformat(str(fd)[:10])
                    if not hasattr(td, "year"): td = date.fromisoformat(str(td)[:10])
                    lag = (fd - td).days
                    if lag >= 0:
                        lags.append(lag)
                except (ValueError, TypeError):
                    pass
        if lags:
            fl_pts, fl_factor = filing_lag_bonus(min(lags))
            if fl_pts > 0:
                aggregate_score = min(aggregate_score + fl_pts, 100)
                breakdown_combined = dict(breakdown_combined)
                breakdown_combined[fl_factor] = fl_pts

        signal_type   = classify_signal(aggregate_score, is_cluster, participant_scores, tight_cluster)

        cap_tier    = tx_rows[0].get("cap_tier") or "unknown"
        # Large-cap clusters have near-zero alpha (0% hit at 90d, -16% avg excess).
        # Downgrade to WATCH so they surface on dashboard but don't trigger alerts.
        if signal_type == "CLUSTER_BUY" and cap_tier == "large":
            signal_type = "WATCH"
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

        pending.append(dict(
            ticker=ticker,
            signal_date=signal_date,
            score=aggregate_score,
            signal_type=signal_type,
            cluster_flag=is_cluster,
            score_breakdown=breakdown_combined,
            evidence=evidence,
        ))
        n_dry_run_total += 1

        if signal_type == "CLUSTER_BUY":
            n_cluster += 1
        elif signal_type == "BUY":
            n_buy += 1
        else:
            n_watch += 1

        if len(pending) >= _FLUSH_EVERY:
            _flush()

    _flush("final")

    # ── DEDUP ─────────────────────────────────────────────────────────────────
    if not args.dry_run:
        from src.db.store import dedup_suppressed_signals
        n_removed = dedup_suppressed_signals(since=start, until=end)
        if n_removed:
            log(f"  Dedup: removed {n_removed} signals suppressed by cooldown logic")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    phase("SUMMARY")
    elapsed = time.time() - t_start
    log(f"Completed in {fmt_elapsed(elapsed)}")
    log(f"  CLUSTER_BUY: {n_cluster}  BUY: {n_buy}  WATCH: {n_watch}  "
        f"LOW: {n_low}  ineligible: {n_ineligible}")
    if args.dry_run:
        log(f"  DRY RUN — {n_dry_run_total} signals would be written (use without --dry-run to commit)")
    else:
        log(f"  Signals written: {n_saved}")
        if n_saved:
            log("  Dashboard and backtest now reflect this history.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print(f"FATAL ERROR:\n{traceback.format_exc()}")
        sys.exit(1)
