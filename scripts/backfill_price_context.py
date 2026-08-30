"""
Fill `transactions.pct_below_52wk_high` and friends for rows written before ingest stored it.

The scorer ranks purchases by how far below its 52-week high a stock sat on the
day the insider bought. Live ingest writes that at filing time; every row
already in the database predates it. Until this has run, those purchases are
unranked and score zero.

Reads the local price panel first, because `data/prices/panel.parquet` already
holds daily bars for every ticker with a purchase and needs no network. Falls
back to fetching for tickers the panel misses, unless `--panel-only`.

  uv run python scripts/backfill_price_context.py --dry-run
  uv run python scripts/backfill_price_context.py
  uv run python scripts/backfill_price_context.py --force   # recompute filled rows

Idempotent. Without `--force` it only touches rows where price_context_bars is
NULL, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from psycopg2.extras import RealDictCursor, execute_batch

from src.db.connection import get_conn
from src.ingest.common import log, phase, setup_log_tee
from src.market.context import context_from_series, context_for
from src.market.panel import load_panel

setup_log_tee("backfill_price_context")

BATCH = 500

SELECT_SQL = """
SELECT t.id, t.transaction_date, c.ticker
FROM transactions t
JOIN form4_filings f ON f.id = t.filing_id
JOIN companies c ON c.cik = f.cik
WHERE t.transaction_code = 'P'
  AND c.ticker IS NOT NULL
  {only_missing}
ORDER BY c.ticker, t.transaction_date
"""

UPDATE_SQL = """
UPDATE transactions
SET px_close_at_tx = %(px_close_at_tx)s,
    px_52wk_high = %(px_52wk_high)s,
    pct_below_52wk_high = %(pct_below_52wk_high)s,
    price_context_bars = %(price_context_bars)s
WHERE id = %(id)s
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Recompute rows that already have context.")
    parser.add_argument("--panel-only", action="store_true",
                        help="Never hit the network; skip tickers the panel misses.")
    args = parser.parse_args()

    phase("LOAD")
    panel = load_panel()
    log(f"{len(panel):,} symbols in the local panel")

    only_missing = "" if args.force else "AND t.price_context_bars IS NULL"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(SELECT_SQL.format(only_missing=only_missing))
            rows = cur.fetchall()
    log(f"{len(rows):,} purchase rows to fill")
    if not rows:
        log("Nothing to do.")
        return

    phase("COMPUTE")
    updates, stats = [], defaultdict(int)
    for row in rows:
        ticker = row["ticker"]
        series = panel.get(ticker)
        if series is not None:
            context = context_from_series(series, row["transaction_date"])
            stats["from panel"] += 1
        elif args.panel_only:
            stats["skipped, not in panel"] += 1
            continue
        else:
            context = context_for(ticker, row["transaction_date"])
            stats["fetched"] += 1
        stats["ranked" if context["pct_below_52wk_high"] is not None
              else "too little history"] += 1
        updates.append({"id": row["id"], **context})

    for name, count in sorted(stats.items()):
        log(f"  {name:<24} {count:>7,}")

    if args.dry_run:
        log("\n--dry-run: nothing written")
        return

    phase("WRITE")
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_batch(cur, UPDATE_SQL, updates, page_size=BATCH)
    log(f"updated {len(updates):,} rows")
    log("Next: uv run python scripts/backfill_signals.py --days 730 --force")


if __name__ == "__main__":
    main()
