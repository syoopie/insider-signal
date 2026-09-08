"""
Prove the parquet archive says what the database says, on the window they share.

The price panel earned its trust this way. `verify_price_panel.py` recomputed
every signal in the last backtest from the panel and matched the network path to
0.003pp, and only then did anything downstream rely on it. The archive is about
to become the substrate for every scoring number, over a window the database
cannot hold, so it needs the same treatment before it is used and not after.

The overlap is whatever both cover. The database keeps 24 months because
`prune_old_data` deletes the rest, so the overlap shrinks by a day every day and
the check has to be run against a window still inside it.

  uv run python scripts/verify_form4_archive.py
  uv run python scripts/verify_form4_archive.py --tolerance 0.01
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.db.connection import get_conn
from src.ingest.common import load_ticker_universe, log, phase, setup_log_tee

setup_log_tee("verify_form4_archive")

ARCHIVE = Path("data/form4")
TOLERANCE = 0.02


def _archive() -> tuple[pd.DataFrame, pd.DataFrame]:
    filings = sorted((ARCHIVE / "filings").glob("part-*.parquet"))
    transactions = sorted((ARCHIVE / "transactions").glob("part-*.parquet"))
    if not filings:
        raise SystemExit(f"No parts under {ARCHIVE}. Run build_form4_archive.py first.")
    return (pd.concat([pd.read_parquet(p) for p in filings], ignore_index=True),
            pd.concat([pd.read_parquet(p) for p in transactions], ignore_index=True))


def _database(start, end) -> tuple[pd.DataFrame, pd.DataFrame]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.accession_number, f.cik, f.filed_date, f.period_date,
                       c.ticker
                FROM form4_filings f
                LEFT JOIN companies c ON c.cik = f.cik
                WHERE f.filed_date BETWEEN %s AND %s
            """, (start, end))
            filings = pd.DataFrame(cur.fetchall(),
                                   columns=["accession_number", "cik",
                                            "filed_date", "period_date",
                                            "ticker"])
            cur.execute("""
                SELECT f.accession_number, t.insider_name, t.transaction_date,
                       t.transaction_code, t.shares, t.price_per_share,
                       t.total_value, t.is_10b51, t.is_direct
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                WHERE f.filed_date BETWEEN %s AND %s
            """, (start, end))
            transactions = pd.DataFrame(
                cur.fetchall(),
                columns=["accession_number", "insider_name", "transaction_date",
                         "transaction_code", "shares", "price_per_share",
                         "total_value", "is_10b51", "is_direct"])
    return filings, transactions


def _share(part: int, whole: int) -> float:
    return part / max(whole, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args()

    phase("LOAD")
    arc_filings, arc_tx = _archive()
    arc_filings["filed_date"] = pd.to_datetime(arc_filings["filed_date"]).dt.date
    start, end = arc_filings["filed_date"].min(), arc_filings["filed_date"].max()
    log(f"Archive: {len(arc_filings):,} filings, {len(arc_tx):,} transactions, "
        f"{start} to {end}")

    db_filings, db_tx = _database(start, end)
    log(f"Database over the same dates: {len(db_filings):,} filings, "
        f"{len(db_tx):,} transactions")
    if db_filings.empty:
        raise SystemExit("The database holds nothing in this window. Pruning has "
                         "already passed it, so there is no overlap left to check.")

    failures = []

    phase("ACCESSIONS")
    # Both sides filter to data/tickers.txt, but the database was filled by
    # ingest runs stretching back two years and each used the universe file as
    # it stood that day. Comparing raw sets therefore measures how much the
    # Russell reconstituted, not whether the archive is complete. Restricting
    # the database side to today's universe is the like-for-like comparison; the
    # drift is reported separately because it is worth knowing.
    universe = load_ticker_universe()
    in_universe_now = db_filings["ticker"].isin(universe) if universe \
        else pd.Series(True, index=db_filings.index)
    drifted = db_filings[~in_universe_now]
    comparable = db_filings[in_universe_now]

    arc_set = set(arc_filings["accession_number"])
    db_set = set(comparable["accession_number"])
    both = arc_set & db_set
    log(f"  in both {len(both):,}   archive only {len(arc_set - db_set):,}   "
        f"database only {len(db_set - arc_set):,}")
    log(f"  set aside: {len(drifted):,} stored filings whose ticker has since "
        f"left data/tickers.txt")
    missing = _share(len(db_set - arc_set), len(db_set))
    log(f"  the archive is missing {missing:.2%} of comparable stored filings")
    if missing > args.tolerance:
        failures.append(f"archive missing {missing:.2%} of the database's filings")

    # The other direction is not a failure and is the more interesting number.
    # A filing the archive has and the database does not is one whose issuer
    # was outside the universe on the day it was filed and inside it later, so
    # the database has a hole the research sample has always silently carried.
    log(f"  the archive adds {len(arc_set - db_set):,} filings the database "
        f"never ingested, {_share(len(arc_set - db_set), len(arc_set)):.2%} of "
        "its own rows")

    phase("TRANSACTION COUNTS PER FILING")
    arc_counts = arc_tx[arc_tx["accession_number"].isin(both)] \
        .groupby("accession_number").size()
    db_counts = db_tx[db_tx["accession_number"].isin(both)] \
        .groupby("accession_number").size()
    joined = pd.concat([arc_counts.rename("archive"), db_counts.rename("db")],
                       axis=1).fillna(0)
    disagree = joined[joined["archive"] != joined["db"]]
    log(f"  {len(joined):,} shared filings, {len(disagree):,} disagree on count")
    rate = _share(len(disagree), len(joined))
    if rate > args.tolerance:
        failures.append(f"{rate:.2%} of shared filings disagree on transaction count")
        log(f"  worst offenders:\n{disagree.head(10).to_string()}")

    phase("PURCHASE VALUE")
    def purchases(frame: pd.DataFrame) -> float:
        rows = frame[(frame["transaction_code"] == "P")
                     & frame["accession_number"].isin(both)]
        return float(pd.to_numeric(rows["total_value"], errors="coerce").sum())

    arc_value, db_value = purchases(arc_tx), purchases(db_tx)
    log(f"  archive ${arc_value:,.0f}   database ${db_value:,.0f}")
    gap = abs(arc_value - db_value) / max(db_value, 1.0)
    log(f"  gap {gap:.4%}")
    if gap > args.tolerance:
        failures.append(f"purchase value differs by {gap:.2%}")

    phase("WHAT THE ARCHIVE ADDS")
    for column in ("is_director", "is_officer", "is_ten_percent"):
        known = arc_tx[column].notna().sum()
        log(f"  {column:<16} present on {known:,} of {len(arc_tx):,} rows "
            f"({arc_tx[column].fillna(False).sum():,} true). "
            "The database stores no such column.")

    phase("VERDICT")
    if failures:
        for line in failures:
            log(f"  FAIL: {line}")
        raise SystemExit(1)
    log(f"  The archive reproduces the database within {args.tolerance:.0%} on "
        f"every check, over {start} to {end}.")


if __name__ == "__main__":
    main()
