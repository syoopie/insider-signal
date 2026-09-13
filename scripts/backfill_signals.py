"""
Rebuild stored signals from the transactions already in the database.

Run it after any change under src/signals/, and after bootstrap. No EDGAR
fetching and no Telegram. Every signal keyed to a filing disclosed in the range
is deleted and rebuilt in one transaction, through the same
`src/signals/rebuild.py` the daily ingest uses, and a signal already sent to
Telegram stays marked as sent. With no range it rebuilds everything the
database holds.

The oldest REFERENCE_DAYS of the database rebuild at score 0: `prune_old_data`
has deleted the filings those purchases would be ranked against.

  uv run python scripts/backfill_signals.py
  uv run python scripts/backfill_signals.py --start 2026-01-01 --end 2026-03-31
  uv run python scripts/backfill_signals.py --days 90 --dry-run
"""

import argparse
import sys
import time
from datetime import date, timedelta

from src.db.store import get_history_start
from src.log import fmt_elapsed, log, phase, setup_log_tee
from src.signals.rebuild import rebuild_signals

setup_log_tee("backfill")


def main():
    parser = argparse.ArgumentParser(description="Rebuild stored signals from stored transactions.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, help="Rebuild filings disclosed in the last N days")
    group.add_argument("--start", type=date.fromisoformat, help="First filing date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="Last filing date, YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score and count without writing to the database")
    args = parser.parse_args()

    end = args.end or date.today()
    if args.start:
        start = args.start
    elif args.days:
        start = end - timedelta(days=args.days)
    else:
        start = get_history_start() or end

    t0 = time.time()
    phase("REBUILD")
    log(f"Filings disclosed {start} → {end}   dry run: {args.dry_run}")
    result = rebuild_signals(start, end, dry_run=args.dry_run)

    counts = result.counts()
    log(f"  CLUSTER_BUY: {counts['CLUSTER_BUY']}  BUY: {counts['BUY']}  "
        f"WATCH: {counts['WATCH']}  LOW: {counts['LOW']}  ineligible: {result.ineligible}")
    replaced = result.replaced
    if replaced:
        log(f"  deleted {replaced.deleted}, stored {replaced.written}, "
            f"{replaced.suppressed} suppressed by the cooldown, {replaced.deduped} deduplicated, "
            f"{replaced.alerts_kept} alert flag(s) carried across")
    log(f"Completed in {fmt_elapsed(time.time() - t0)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print(f"FATAL ERROR:\n{traceback.format_exc()}")
        sys.exit(1)
