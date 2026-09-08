"""
A local parquet archive of Form 4 history, for research only.

`prune_old_data` deletes anything older than the retention window on a schedule,
and the daily ingest calls it. The research sample therefore has a hard ceiling:
every month ingest adds at the back, pruning removes one at the front. Measured
2026-09-08, that leaves about 16 predictable months at the 90-day horizon, and
it will leave 16 forever.

That ceiling is the binding constraint on the whole scoring effort. The ruler
resolves about 3pp of selection alpha and its standard error falls as
1/sqrt(months), so no amount of feature work can find an effect of the size
every insider feature has measured. Only more months can, and more months is a
thing EDGAR gives away.

**This used to fetch filings one at a time and it does not any more.** For
anything older than about a year that costs three requests each: the submissions
API has aged the filing out, so `fetch_filing_xml` falls back to scraping the
index page for the XML link. A thousand filings a week at three requests each
put a 9 req/sec budget over EDGAR's limit and earned a 429 twenty-two minutes
into the first real run, which abandoned the window it was on.

DERA publishes the same filings already parsed, one zip per quarter, about 10MB
each. Ten years is roughly forty requests instead of several hundred thousand,
and it finishes in minutes. `src/ingest/dera.py` has the equivalence check
against EDGAR's daily index.

The archive is deliberately not in Neon. The database is on a 0.5GB free tier
and ten years of transactions would be several hundred megabytes. The price
panel set this precedent and `verify_price_panel.py` proved it equivalent to the
path it replaced; `verify_form4_archive.py` does the same here.

What this stores that the database does not: `is_director`, `is_officer` and
`is_ten_percent`. `write_filing` stores none of them, so a CFO who sits on the
board and a CFO who does not are the same stored row today.

What it does not store: `is_routine`, which `write_filing` computes against
whatever history the database holds at the time. Deriving it from the archive is
a different computation on a different window and it belongs with the rollup
step, not here.

Resumable by construction. One parquet part per quarter and a manifest recording
the quarters that finished, plus the downloaded zips kept on disk, so a re-run
costs nothing for the quarters already done.

Usage:
  uv run python scripts/build_form4_archive.py --start 2016-01-01 --end 2024-09-02
  uv run python scripts/build_form4_archive.py --start 2022-01-01   # to today
  uv run python scripts/build_form4_archive.py --start ... --force  # ignore the manifest
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.ingest.common import fmt_elapsed, load_ticker_universe, log, phase, setup_log_tee
from src.ingest.dera import (
    download_quarter,
    quarters_between,
    read_quarter,
    to_archive_rows,
)

setup_log_tee("build_form4_archive")

ARCHIVE = Path("data/form4")
MANIFEST = ARCHIVE / "manifest.json"
DOWNLOADS = ARCHIVE / "dera"

FILING_COLUMNS = [
    "accession_number", "cik", "ticker", "company_name",
    "filed_date", "period_date",
]

TRANSACTION_COLUMNS = [
    "accession_number", "insider_name", "insider_cik", "insider_role",
    "role_category", "is_director", "is_officer", "is_ten_percent",
    "transaction_date", "transaction_code", "shares", "price_per_share",
    "total_value", "shares_after", "is_10b51", "is_direct",
]


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        return {"quarters": {}}
    stored = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # The manifest used to be keyed by fetch window. Those keys describe parts
    # written by the per-filing path and say nothing about which quarters are
    # covered, so a run after the rewrite has to ignore them.
    return {"quarters": stored.get("quarters", {})}


def _save_manifest(manifest: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True),
                        encoding="utf-8")


def quarter_key(year: int, quarter: int) -> str:
    return f"{year}q{quarter}"


def _write_part(kind: str, key: str, frame: pd.DataFrame,
                columns: list) -> None:
    """
    One part file per quarter, written whole.

    Partial parts are what make a resumed run wrong rather than merely slow, so
    a quarter is written once, after its whole frame is built, and only then
    recorded in the manifest.
    """
    path = ARCHIVE / kind / f"part-{key}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reindex(columns=columns).to_parquet(path, index=False,
                                              compression="zstd")


def _clear_legacy_parts() -> int:
    """
    Drop parts written by the per-filing path.

    Those are named by fetch window and the new ones by quarter, so both would
    survive side by side and every filing inside the overlap would be counted
    twice. The zips make rebuilding them free.
    """
    removed = 0
    for kind in ("filings", "transactions"):
        for path in (ARCHIVE / kind).glob("part-*.parquet"):
            key = path.stem.removeprefix("part-")
            if "q" not in key:
                path.unlink()
                removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-read quarters the manifest already records.")
    parser.add_argument("--rate", type=float, default=4.0,
                        help="Requests per second. One request per quarter, so "
                             "this barely matters; it exists to stay polite.")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start \
        else date(2016, 1, 1)
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else date.today()

    began = time.time()
    phase("SETUP")
    universe = load_ticker_universe()
    log(f"Universe: {len(universe):,} tickers")

    manifest = _load_manifest()
    quarters = quarters_between(start, end)
    pending = [q for q in quarters
               if args.force or quarter_key(*q) not in manifest["quarters"]]
    log(f"{len(quarters)} quarters from {start} to {end}, {len(pending)} to build")

    dropped = _clear_legacy_parts()
    if dropped:
        log(f"Removed {dropped} parts left by the per-filing path; they are "
            "named by window and would double-count inside the overlap.")

    totals = {"filings": 0, "transactions": 0, "unpublished": 0}
    for index, (year, quarter) in enumerate(pending, start=1):
        key = quarter_key(year, quarter)
        phase(f"{key}  ({index}/{len(pending)})")
        zip_path = download_quarter(year, quarter, DOWNLOADS, req_per_sec=args.rate)
        if zip_path is None:
            log("  DERA has not published this quarter yet, skipping.")
            totals["unpublished"] += 1
            continue

        tables = read_quarter(zip_path)
        filings, transactions = to_archive_rows(tables, universe)
        _write_part("filings", key, filings, FILING_COLUMNS)
        _write_part("transactions", key, transactions, TRANSACTION_COLUMNS)

        purchases = int((transactions["transaction_code"] == "P").sum())
        log(f"  {len(filings):,} filings, {len(transactions):,} transactions, "
            f"{purchases:,} purchases, {zip_path.stat().st_size / 1e6:.1f}MB")
        totals["filings"] += len(filings)
        totals["transactions"] += len(transactions)
        manifest["quarters"][key] = {
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "filings": len(filings),
            "transactions": len(transactions),
        }
        _save_manifest(manifest)

    phase("SUMMARY")
    log(f"filings {totals['filings']:,}   transactions {totals['transactions']:,}"
        f"   quarters not yet published {totals['unpublished']}")
    log(f"manifest covers {len(manifest['quarters'])} quarters at {MANIFEST}")
    log(f"Completed in {fmt_elapsed(time.time() - began)}")


if __name__ == "__main__":
    main()
