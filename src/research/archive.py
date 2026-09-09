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

NULL is the honest answer for all of them out of `purchases()`, and it is not a
bug, but it does mean those rows cannot be scored: `discount_score` returns None
without `pct_below_52wk_high`, so every purchase would score 0.
`priced_purchases()` fills two of the three, each by calling the function that
already owns it — `market.context.context_from_series` for the price columns and
`research.routine.routine_flags` for the routine flag. `cap_tier` stays NULL.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from src.db.purchases import purchase_rollup

ARCHIVE = Path("data/form4")

# Mirrors the floor in `store._load_discount_series`, which is the scorer's own
# $2,000 disqualifier: a reference built from DRIP and payroll noise ranks real
# purchases against trades nobody decided to make.
MIN_REFERENCE_VALUE = 2_000

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


def with_price_context(frame: pd.DataFrame, panel: Optional[dict] = None) -> pd.DataFrame:
    """
    Fill the price columns the rollup leaves NULL, from the local price panel.

    This calls `context_from_series`, which is the function `src/market/context.py`
    uses at ingest. That is the whole point. A 52-week high computed a second way
    here would be a second definition of the only factor that scores, and the
    deleted 52-week-*low* factors are what happens then: the same purchase scored
    up to 12 points apart depending on which path saw it.

    A ticker the panel does not cover, or a date without 200 bars behind it, gets
    None rather than a partial answer, so it scores 0 and is never alerted.
    """
    from src.market.context import CONTEXT_FIELDS, context_from_series
    from src.market.panel import load_panel

    if panel is None:
        panel = load_panel()

    out = frame.copy()
    dates = pd.to_datetime(out["transaction_date"]).dt.date
    context = [
        context_from_series(panel.get(ticker), as_of)
        if ticker and as_of == as_of else dict.fromkeys(CONTEXT_FIELDS)
        for ticker, as_of in zip(out["ticker"], dates)
    ]
    for field in CONTEXT_FIELDS:
        out[field] = [row.get(field) for row in context]
    return out


def purchases(conn: duckdb.DuckDBPyConnection, extra_where: str = "",
              params: tuple = ()) -> pd.DataFrame:
    """
    Rolled-up purchases from the archive, one row per insider-day-direction.

    `extra_where` is spliced into the rollup's CTE exactly as the database
    callers splice it. DuckDB takes `?` placeholders where psycopg2 takes `%s`,
    so a caller passing parameters writes `?`.
    """
    return conn.execute(purchase_rollup(extra_where), params).fetch_df()


def priced_purchases(conn: duckdb.DuckDBPyConnection,
                     panel: Optional[dict] = None) -> pd.DataFrame:
    """
    Every archived purchase, rolled up, priced, and with `is_routine` decided.

    The three columns the rollup leaves NULL are filled by the two functions
    that own them, so nothing here recomputes a definition the pipeline already
    has. `cap_tier` stays NULL: it is a fact about today's market cap, the
    archive cannot supply it as of the trade, and the factor scores zero points.

    `routine_flags` is given the whole frame on purpose. Judging a subset
    shortens the history each row can see and turns decided rows into pd.NA.
    """
    from src.research.routine import routine_flags

    # One archived Form 4 in 71,930 reports a purchase with no transaction date.
    # Everything downstream is keyed on it — the price context as of the trade,
    # the filing lag, every exit date — so there is nothing to salvage.
    rolled = purchases(conn)
    rolled = rolled[rolled["transaction_date"].notna()]

    # DuckDB aggregates in parallel and returns groups in whatever order the
    # threads finished, so two runs over identical parquet disagree on row order
    # and, in 619 rows of 71,930, on the last bit of a summed double. Neither
    # changes a score, but the dataset is meant to be an experiment you can
    # repeat, and repeating it must give the same file. These five columns are
    # the rollup's own grain, so they are a unique key.
    rolled = rolled.sort_values(
        ["cik", "insider_name", "transaction_date", "transaction_code", "is_direct"],
        kind="stable",
    ).reset_index(drop=True)

    frame = with_price_context(rolled, panel)
    frame["is_routine"] = routine_flags(frame)
    return frame


def discount_series(priced: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    (filed dates ascending, discounts) for `signals.discount.reference_window`.

    Mirrors `store._load_discount_series`'s filters over the archive's own rows,
    and is built from the same priced frame the scored purchases come from, so
    a purchase and the reference it is ranked against agree by construction.
    """
    # DERA only carries the 10b5-1 checkbox from 2023q1, so `is_10b51` is NULL
    # for every earlier row. `== False` would discard seven years of reference.
    not_10b51 = ~priced["is_10b51"].fillna(False).astype(bool)

    keep = (
        priced["pct_below_52wk_high"].notna()
        & (priced["total_value"].fillna(0) >= MIN_REFERENCE_VALUE)
        & not_10b51
    )
    kept = priced[keep].sort_values("filed_date")
    return (pd.to_datetime(kept["filed_date"]).to_numpy(dtype="datetime64[D]"),
            kept["pct_below_52wk_high"].to_numpy(dtype="float64"))


_SALES_SQL = """
SELECT f.cik, f.filed_date, t.insider_name,
       COALESCE(t.total_value, 0) AS total_value
FROM transactions t
JOIN form4_filings f ON f.id = t.filing_id
WHERE t.transaction_code = 'S'
  AND t.is_10b51 IS NOT TRUE
"""


def sales(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Open-market sales, for the net-demand feature. No rollup, as in the database
    version: only the issuer, the disclosure date and the dollar amount matter.

    The dates come back as `datetime.date`, which is what psycopg2 hands the
    database caller. DuckDB gives pandas Timestamps, and `tier1._as_date` leaves
    those alone because Timestamp subclasses date — so they reach a comparison
    against a real date, which raises rather than compares.
    """
    frame = conn.execute(_SALES_SQL).fetch_df()
    frame["filed_date"] = pd.to_datetime(frame["filed_date"]).dt.date
    return frame


def history_start(conn: duckdb.DuckDBPyConnection) -> Optional[date]:
    """Earliest filing date the archive holds, keyed off filed_date as the database is."""
    return conn.execute("SELECT min(filed_date) FROM form4_filings").fetchone()[0]
