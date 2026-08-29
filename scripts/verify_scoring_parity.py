"""
Check that the research dataset scores purchases the way the pipeline does.

The dataset exists to hold the negative class the signals table throws away, so
it necessarily scores rows the backfill never writes. That only works if the two
agree wherever they overlap. Both call src/signals/batch.py, so they should, but
"should" is what the old duplicated cluster logic also had.

For every stored signal this reconstructs the scoring window from
evidence.filed_date, takes the highest-scoring eligible purchase the dataset has
for that ticker in that window, and compares it against the stored score. That
is precisely what backfill_signals.py does to produce the number.

Agreement will not reach 100%, and the residual is this check's approximation
rather than a scoring disagreement. A ticker with purchases disclosed on several
nearby days produces overlapping work items, `batch_save_signals` keeps one row
per (ticker, signal_date), and the cooldown drops follow-ups. So a stored score
can come from a neighbouring window rather than the one reconstructed from its
own filed_date. Roughly 1% of signals land that way.

Exits non-zero if agreement is below --min-agreement.

Usage:
  python3 scripts/verify_scoring_parity.py
  python3 scripts/verify_scoring_parity.py --min-agreement 99.0
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd

from src.db.connection import get_conn
from src.ingest.common import setup_log_tee, log, phase
from src.market.panel import PANEL_PATH
from src.signals.batch import window_start_for

setup_log_tee("verify_scoring_parity")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"


def _stored_signals() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, signal_date, score, signal_type,
                       (evidence->>'filed_date')::date AS filed_date
                FROM signals
                WHERE evidence->>'filed_date' IS NOT NULL
                ORDER BY signal_date
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--min-agreement", type=float, default=99.0,
                        help="Minimum percentage of comparable signals that must match")
    args = parser.parse_args()

    phase("LOAD")
    frame = pd.read_parquet(args.dataset)
    scorable = frame[frame["eligible"] & ~frame["scorer_disqualified"].fillna(True)]
    log(f"Dataset: {len(frame):,} rows, {len(scorable):,} scorable, from {args.dataset}")

    by_ticker: dict[str, list[tuple]] = defaultdict(list)
    for ticker, filed, score in zip(scorable["ticker"], scorable["filed_date"], scorable["score"]):
        by_ticker[ticker].append((filed, score))

    covered_from = frame["filed_date"].min()
    covered_to = frame["filed_date"].max()
    signals = _stored_signals()
    log(f"Stored signals with a filed_date: {len(signals):,}")
    log(f"Dataset covers filings {covered_from} → {covered_to}")

    phase("RECONCILE")
    matched = mismatched = 0
    outside = no_rows = 0
    examples = []

    for sig in signals:
        filed = sig["filed_date"]
        if filed < covered_from or filed > covered_to:
            outside += 1
            continue

        start = window_start_for(filed)
        scores = [s for f, s in by_ticker.get(sig["ticker"], []) if start <= f <= filed]
        if not scores:
            no_rows += 1
            continue

        expected = max(scores)
        if int(expected) == int(sig["score"]):
            matched += 1
        else:
            mismatched += 1
            if len(examples) < 10:
                examples.append((sig["ticker"], sig["signal_date"], filed,
                                 sig["score"], int(expected), sig["signal_type"]))

    comparable = matched + mismatched
    log(f"  comparable: {comparable:,}   matched: {matched:,}   mismatched: {mismatched:,}")
    log(f"  outside the dataset's filing range: {outside:,}   no scorable rows in window: {no_rows:,}")

    if examples:
        log("\n  mismatches (ticker, signal_date, filed_date, stored, dataset, type):")
        for row in examples:
            log(f"    {row[0]:<6} {row[1]}  filed={row[2]}  stored={row[3]:>3}  "
                f"dataset={row[4]:>3}  {row[5]}")

    phase("VERDICT")
    if comparable == 0:
        log("FAIL — nothing was comparable; the dataset and the signals table do not overlap")
        raise SystemExit(1)

    agreement = matched / comparable * 100
    if agreement >= args.min_agreement:
        log(f"PASS — {agreement:.2f}% agreement on {comparable:,} signals "
            f"(threshold {args.min_agreement}%)")
    else:
        log(f"FAIL — {agreement:.2f}% agreement on {comparable:,} signals "
            f"(threshold {args.min_agreement}%)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
