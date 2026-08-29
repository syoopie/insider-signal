"""
Remove Table I rows that report debt, not stock.

A filer disclosing notes puts the principal amount in both transactionShares
and transactionPricePerShare, so shares x price is meaningless: MetLife's $10M
of KYN senior notes was stored as a $100 trillion purchase, and Prudential's
TYG notes as $554 trillion. src/ingest/parser.py now skips these at ingest, so
this only has to clear rows written before that fix.

Idempotent. Safe to re-run; a second run deletes nothing.

Usage:
  python scripts/purge_debt_transactions.py --dry-run
  python scripts/purge_debt_transactions.py
"""

from __future__ import annotations

import argparse

from src.db.connection import get_conn
from src.ingest.common import log, phase

# Equity priced at its own share count is arithmetically impossible above a few
# dollars; below that it is a coincidence worth leaving alone.
SELECT_SQL = """
    SELECT f.accession_number, c.ticker, t.insider_name,
           t.transaction_date, t.shares, t.total_value
    FROM transactions t
    JOIN form4_filings f ON f.id = t.filing_id
    LEFT JOIN companies c ON c.cik = f.cik
    WHERE t.shares = t.price_per_share AND t.shares > 1000
    ORDER BY t.total_value DESC
"""

DELETE_SQL = "DELETE FROM transactions WHERE shares = price_per_share AND shares > 1000"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="List rows without deleting")
    args = parser.parse_args()

    phase("DEBT ROWS MIS-PARSED AS PURCHASES")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_SQL)
            rows = cur.fetchall()
            for acc, ticker, name, td, shares, value in rows:
                log(f"  {ticker or '?':<6} {td}  {name[:38]:<38} "
                    f"shares={float(shares):,.0f}  stored_value=${float(value or 0):,.0f}")
            log(f"{len(rows)} row(s) match")

            if args.dry_run:
                log("Dry run — nothing deleted")
                return
            if not rows:
                log("Nothing to do")
                return

            cur.execute(DELETE_SQL)
            log(f"Deleted {cur.rowcount} row(s)")

    log("Re-run scripts/backfill_signals.py --days 730 --force to rescore without them")


if __name__ == "__main__":
    main()
