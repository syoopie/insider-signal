"""
A local parquet archive of Form 4 history, for research only.

`prune_old_data` deletes anything older than 24 months on a schedule, and the
daily ingest calls it. The database's earliest filing is 2024-09-03, exactly two
years before today, and it will still be exactly two years before whatever today
is when you read this. The research sample therefore has a hard ceiling: every
month ingest adds at the back, pruning removes one at the front. Measured
2026-09-08, that leaves 16 predictable months at the 90-day horizon, and it will
leave 16 forever.

That ceiling is the binding constraint on the whole scoring effort. The ruler
resolves about 3pp of selection alpha and its standard error falls as
1/sqrt(months), so no amount of feature work can find an effect of the size
every insider feature has measured. Only more months can, and more months is a
thing EDGAR gives away.

The archive is deliberately not in Neon. The database is on a 0.5GB free tier at
about 105MB, and ten years of transactions and filings would be several hundred
megabytes. The price panel set this precedent and `verify_price_panel.py` proved
it equivalent to the path it replaced; do the same here before trusting it.

What this stores that the database does not: `is_director`, `is_officer` and
`is_ten_percent`. `parse_form4` has always read all three off
`reportingOwnerRelationship` and `write_filing` stores none of them, so a CFO
who sits on the board and a CFO who does not are the same stored row today. The
archive keeps them.

What it does not store: `is_routine`, which `write_filing` computes against
whatever history the database holds at the time. Deriving it from the archive is
a different computation on a different window and it belongs with the rollup
step, not here.

Resumable by construction. One parquet part per window, and a manifest recording
the windows that finished. Re-running skips them, so an interrupted 50-hour fetch
costs one window and not the run.

Usage:
  uv run python scripts/build_form4_archive.py --start 2016-01-01 --end 2024-09-02
  uv run python scripts/build_form4_archive.py --days 60          # a pilot
  uv run python scripts/build_form4_archive.py --start ... --force # ignore the manifest
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.ingest.common import (
    DERIV_ONLY,
    PARSE_ERROR,
    XML_MISSING,
    EdgarBlockedError,
    EdgarRateLimitError,
    EdgarServerError,
    fetch_and_parse,
    fmt_elapsed,
    in_universe,
    load_cik_map,
    load_ticker_universe,
    log,
    phase,
    resolve_ticker,
    setup_log_tee,
)
from src.ingest.edgar import fetch_form4_index

setup_log_tee("build_form4_archive")

ARCHIVE = Path("data/form4")
MANIFEST = ARCHIVE / "manifest.json"

# Matches bootstrap.py. EDGAR allows 10 req/sec; 9 leaves headroom and 32
# workers is what saturates it at 3 to 4 seconds of latency per request.
RATE = 9.0
WORKERS = 32
INDEX_WORKERS = 16
WINDOW_DAYS = 7

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
        return {"windows": {}}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _save_manifest(manifest: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True),
                        encoding="utf-8")


def _rows(filing_meta: dict, parsed: dict, ticker: str) -> tuple[dict, list[dict]]:
    owner = parsed.get("owner") or {}
    accession = filing_meta["accession_number"]
    filing = {
        "accession_number": accession,
        "cik": (parsed.get("issuer") or {}).get("cik") or filing_meta.get("cik_raw"),
        "ticker": ticker,
        "company_name": (parsed.get("issuer") or {}).get("name"),
        # Both from `parsed`, which has run them through `_clean_date`. The raw
        # index metadata has not.
        "filed_date": parsed.get("filed_date"),
        "period_date": parsed.get("period_date"),
    }
    transactions = [{
        "accession_number": accession,
        "insider_name": owner.get("name"),
        "insider_cik": owner.get("cik"),
        "insider_role": owner.get("role_raw"),
        "role_category": owner.get("role_category"),
        "is_director": owner.get("is_director"),
        "is_officer": owner.get("is_officer"),
        "is_ten_percent": owner.get("is_ten_percent"),
        "transaction_date": tx.get("transaction_date"),
        "transaction_code": tx.get("transaction_code"),
        "shares": tx.get("shares"),
        "price_per_share": tx.get("price_per_share"),
        "total_value": tx.get("total_value"),
        "shares_after": tx.get("shares_after"),
        "is_10b51": tx.get("is_10b51"),
        "is_direct": tx.get("is_direct"),
    } for tx in parsed.get("transactions", [])]
    return filing, transactions


def _write_part(kind: str, window_start: date, rows: list[dict],
                columns: list[str]) -> None:
    """
    One part file per window, written whole.

    Partial parts are what make a resumed run wrong rather than merely slow, so
    a window is written once, after every future for it has been drained, and
    only then recorded in the manifest.
    """
    path = ARCHIVE / kind / f"part-{window_start.isoformat()}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=columns) if rows \
        else pd.DataFrame(columns=columns)
    frame.to_parquet(path, index=False, compression="zstd")


def to_fetch(metas, cik_to_ticker: dict, universe) -> list[tuple[dict, str]]:
    """
    The index records worth a document fetch, each with its resolved ticker.

    A joint Form 4 is indexed once per reporting owner under one accession
    number, so the index yields it several times. The database has never had to
    care because `accession_number` is unique there and the second write is a
    no-op. Parquet has no such constraint, and without this the archive stored
    six transactions where the database stored three, on 22 of 2,488 filings in
    the first pilot.
    """
    claimed: set[str] = set()
    out = []
    for meta in metas:
        accession = meta.get("accession_number")
        if not accession or accession in claimed:
            continue
        ticker = resolve_ticker(meta, cik_to_ticker)
        if not in_universe(ticker, universe):
            continue
        claimed.add(accession)
        out.append((meta, ticker))
    return out


def _windows(start: date, end: date, size: int) -> list[tuple[date, date]]:
    out = []
    cursor = start
    while cursor <= end:
        out.append((cursor, min(cursor + timedelta(days=size - 1), end)))
        cursor += timedelta(days=size)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--days", type=int, default=None,
                        help="Window ending today, when --start is not given.")
    parser.add_argument("--chunk", type=int, default=WINDOW_DAYS)
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch windows the manifest already records.")
    args = parser.parse_args()

    today = date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    elif args.days:
        start = today - timedelta(days=args.days)
    else:
        raise SystemExit("Give --start or --days.")
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today

    t0 = time.time()
    phase("SETUP")
    manifest = _load_manifest()
    universe = load_ticker_universe()
    cik_to_ticker = load_cik_map(req_per_sec=RATE)
    log(f"Universe: {len(universe):,} tickers   CIK map: {len(cik_to_ticker):,}")

    windows = _windows(start, end, args.chunk)
    todo = [w for w in windows
            if args.force or w[0].isoformat() not in manifest["windows"]]
    log(f"{len(windows)} windows from {start} to {end}, {len(todo)} to fetch")
    if not todo:
        log("Nothing to do. The manifest already covers this range.")
        return

    totals = {"filings": 0, "transactions": 0, "skipped": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for index, (window_start, window_end) in enumerate(todo, 1):
            phase(f"Window {index}/{len(todo)}: {window_start} to {window_end}")
            pending = {}
            filings, transactions = [], []

            try:
                index = list(fetch_form4_index(window_start, window_end,
                                               req_per_sec=RATE,
                                               index_workers=INDEX_WORKERS))
            except (EdgarRateLimitError, EdgarBlockedError, EdgarServerError) as exc:
                log(f"  EDGAR refused the index: {exc}. Stopping before writing.")
                break

            seen = len(index)
            wanted = to_fetch(index, cik_to_ticker, universe)
            totals["skipped"] += seen - len(wanted)
            log(f"  {seen:,} in index, {len(wanted):,} to fetch")
            for meta, ticker in wanted:
                pending[pool.submit(fetch_and_parse, meta, RATE)] = ticker

            failed = False
            for future, ticker in pending.items():
                try:
                    result = future.result()
                except (EdgarRateLimitError, EdgarBlockedError, EdgarServerError) as exc:
                    log(f"  EDGAR refused a document: {exc}. Abandoning this window.")
                    failed = True
                    break
                except Exception as exc:
                    log(f"  parse failed: {exc}")
                    totals["failed"] += 1
                    continue
                if result in (DERIV_ONLY, XML_MISSING, PARSE_ERROR):
                    totals["failed"] += 1
                    continue
                meta, parsed = result
                filing, rows = _rows(meta, parsed, ticker)
                filings.append(filing)
                transactions.extend(rows)

            if failed:
                break

            _write_part("filings", window_start, filings, FILING_COLUMNS)
            _write_part("transactions", window_start, transactions,
                        TRANSACTION_COLUMNS)
            manifest["windows"][window_start.isoformat()] = {
                "end": window_end.isoformat(),
                "filings": len(filings),
                "transactions": len(transactions),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            _save_manifest(manifest)

            totals["filings"] += len(filings)
            totals["transactions"] += len(transactions)
            log(f"  wrote {len(filings):,} filings, "
                f"{len(transactions):,} transactions, "
                f"elapsed {fmt_elapsed(time.time() - t0)}")

    phase("SUMMARY")
    log(f"filings {totals['filings']:,}   transactions {totals['transactions']:,}   "
        f"outside universe {totals['skipped']:,}   unusable {totals['failed']:,}")
    log(f"manifest covers {len(manifest['windows'])} windows at {MANIFEST}")
    log(f"Completed in {fmt_elapsed(time.time() - t0)}")


if __name__ == "__main__":
    main()
