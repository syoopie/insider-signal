"""
The parquet archive, presented as the tables `purchase_rollup` already reads.

One row in `transactions` is one broker fill, not one decision, and a 4/A
restates a purchase the original already reported. `src/db/purchases.py` is the
single definition of how those collapse into "an insider's purchase on a day",
and the reason it is a single definition is that the old per-caller version
kept one arbitrary fill and hid $4.1B of purchase value.

Writing a pandas rollup for the archive would recreate exactly that problem, so
this does not. It builds DuckDB views named `transactions`, `form4_filings` and
`companies` whose columns match the Postgres schema, then runs
`PURCHASE_ROLLUP_SQL` unmodified. The query is imported, never retyped; if it
changes, both paths change together or neither does.

**Three columns the archive cannot supply, and they come back NULL.**

`is_routine` is computed by `write_filing` against whatever history the database
held at the time, so it is a fact about the ingest window rather than about the
filing. `pct_below_52wk_high`, `px_52wk_high` and `px_close_at_tx` are fetched
once at ingest by `src/market/context.py`. `cap_tier` lives on `companies` and
is refreshed weekly from EDGAR.

NULL is the honest answer for all of them and it is not a bug, but it does mean
**rows out of here cannot be scored yet**: `discount_score` returns None without
`pct_below_52wk_high`, so every purchase would score 0. Joining the price panel
onto these rows is the next step and a separate one.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from src.db.purchases import purchase_rollup

ARCHIVE = Path("data/form4")

# The archive keys a filing by accession number; Postgres keys it by a serial
# id. The rollup uses the id only to group fills within a filing and to break a
# tie between two filings on the same date, and an accession number orders and
# groups the same way.
_FILINGS_VIEW = """
CREATE VIEW form4_filings AS
SELECT accession_number AS id,
       accession_number,
       cik,
       filed_date,
       period_date
FROM read_parquet('{filings}')
"""

_TRANSACTIONS_VIEW = """
CREATE VIEW transactions AS
SELECT t.accession_number       AS filing_id,
       t.insider_name,
       t.insider_role,
       t.role_category,
       t.transaction_date,
       t.transaction_code,
       t.shares,
       t.price_per_share,
       t.total_value,
       t.shares_after,
       t.is_10b51,
       t.is_direct,
       CAST(NULL AS BOOLEAN) AS is_routine,
       CAST(NULL AS DOUBLE)  AS pct_below_52wk_high,
       CAST(NULL AS DOUBLE)  AS px_52wk_high,
       CAST(NULL AS DOUBLE)  AS px_close_at_tx
FROM read_parquet('{transactions}') t
"""

# One row per CIK, matching `companies`, which ingest overwrites so it holds the
# current ticker rather than the one in force when a filing was made. Taking the
# most recent filing's ticker reproduces that. The archive does carry the
# point-in-time ticker on every filing row, which is strictly better for
# research, but using it here would mean this view and the database disagreed by
# construction and the comparison could not run.
_COMPANIES_VIEW = """
CREATE VIEW companies AS
SELECT DISTINCT ON (cik)
       cik,
       ticker,
       CAST(NULL AS VARCHAR) AS cap_tier,
       company_name AS name
FROM read_parquet('{filings}')
ORDER BY cik, filed_date DESC
"""


def connect(archive: Path = ARCHIVE) -> duckdb.DuckDBPyConnection:
    """An in-memory database whose three views read the archive parquet."""
    filings = (archive / "filings" / "part-*.parquet").as_posix()
    transactions = (archive / "transactions" / "part-*.parquet").as_posix()
    conn = duckdb.connect()
    conn.execute(_FILINGS_VIEW.format(filings=filings))
    conn.execute(_TRANSACTIONS_VIEW.format(transactions=transactions))
    conn.execute(_COMPANIES_VIEW.format(filings=filings))
    return conn


def purchases(conn: duckdb.DuckDBPyConnection, extra_where: str = "",
              params: tuple = ()) -> pd.DataFrame:
    """
    Rolled-up purchases from the archive, one row per insider-day-direction.

    `extra_where` is spliced into the rollup's CTE exactly as the database
    callers splice it. DuckDB takes `?` placeholders where psycopg2 takes `%s`,
    so a caller passing parameters writes `?`.
    """
    return conn.execute(purchase_rollup(extra_where), params).fetch_df()
