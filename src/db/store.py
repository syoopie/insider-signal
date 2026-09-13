"""
DB persistence layer for Form 4 filings and transactions.
All inserts are idempotent — safe to re-run on the same data.
"""

import calendar
from datetime import date
from typing import Optional, Tuple, List
from src.db.connection import get_conn
from src.signals.discount import REFERENCE_DAYS as DISCOUNT_REFERENCE_DAYS
from src.signals.discount import reference_window
from src.tickers import clean_ticker

_TX_INSERT_SQL = """
    INSERT INTO transactions (
        filing_id, insider_name, insider_role, role_category,
        transaction_date, transaction_code, shares, price_per_share,
        total_value, shares_after, is_10b51, is_direct, is_routine
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def _compute_is_routine(cur, insider_name: str, cik: str, tx_date) -> Optional[bool]:
    """
    Compute whether this insider's purchase is 'routine' per Cohen, Malloy &
    Pomorski (2012): bought in the same calendar month in ≥2 of 3 prior years.

    Returns True/False if enough historical data exists; None if the DB doesn't
    span 3 years back (can't make a determination without false positives).
    """
    if not insider_name or not tx_date:
        return None
    try:
        from datetime import date as _date
        if isinstance(tx_date, str):
            tx_date = _date.fromisoformat(tx_date[:10])
        tx_month = tx_date.month
        tx_year  = tx_date.year

        # Check the oldest P transaction for this insider to know our data span.
        cur.execute(
            """
            SELECT MIN(t.transaction_date)
            FROM transactions t
            JOIN form4_filings f ON f.id = t.filing_id
            WHERE f.cik = %s AND t.insider_name = %s AND t.transaction_code = 'P'
            """,
            (cik, insider_name),
        )
        row = cur.fetchone()
        oldest = row[0] if row and row[0] else None

        routine_years = 0
        determined_years = 0
        for yr_back in (1, 2, 3):
            yr = tx_year - yr_back
            if oldest is None or oldest > _date(yr, 12, 31):
                continue  # no data for this year — skip (no false positives)
            determined_years += 1
            year_start = _date(yr, tx_month, 1)
            # Last day of the month. Hardcoding 28 skipped purchases on the
            # 29th-31st, undercounting routine traders in 11 months of 12.
            year_end   = _date(yr, tx_month, calendar.monthrange(yr, tx_month)[1])
            cur.execute(
                """
                SELECT 1 FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                WHERE f.cik = %s AND t.insider_name = %s
                  AND t.transaction_code = 'P'
                  AND t.transaction_date BETWEEN %s AND %s
                LIMIT 1
                """,
                (cik, insider_name, year_start, year_end),
            )
            if cur.fetchone():
                routine_years += 1

        if determined_years == 0:
            return None  # not enough data
        return routine_years >= 2
    except Exception:
        return None


def write_filing(cur, filing_meta: dict, parsed: dict, ticker: str,
                 known_ciks: set = None) -> Tuple[int, int]:
    """
    Write one filing + its transactions using an already-open cursor.
    Returns (filing_id, tx_count). filing_id=0 means duplicate (skipped).

    known_ciks: set of CIKs already in the companies table.
      - Provided (bootstrap): INSERT DO NOTHING, add new CIKs to the set.
        Avoids row locks on existing rows entirely.
      - None (daily ingest): INSERT DO UPDATE, keeping ticker/name current.
    """
    issuer  = parsed.get("issuer", {})
    owner   = parsed.get("owner", {})
    raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
    cik     = issuer.get("cik") or raw_cik
    tkr     = clean_ticker(issuer.get("ticker") or ticker) or ""

    if known_ciks is not None:
        if cik not in known_ciks:
            cur.execute(
                "INSERT INTO companies (cik, ticker, name) VALUES (%s,%s,%s) "
                "ON CONFLICT (cik) DO NOTHING",
                (cik, tkr.upper() if tkr else None, issuer.get("name", "")),
            )
            known_ciks.add(cik)
    else:
        cur.execute(
            "INSERT INTO companies (cik, ticker, name) VALUES (%s,%s,%s) "
            "ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker, name=EXCLUDED.name",
            (cik, clean_ticker(tkr), issuer.get("name", "")),
        )

    cur.execute(
        "INSERT INTO form4_filings (accession_number, cik, filed_date, period_date) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (accession_number) DO NOTHING RETURNING id",
        (filing_meta["accession_number"], cik,
         filing_meta.get("filed_date") or None,
         filing_meta.get("period_date") or None),
    )
    row = cur.fetchone()
    if not row:
        return 0, 0
    filing_id = row[0]

    transactions = parsed.get("transactions", [])
    tx_rows = []
    for tx in transactions:
        # Only compute is_routine for open-market purchases — it's irrelevant for grants/sales.
        if tx.get("transaction_code") == "P" and not tx.get("is_10b51", False):
            is_routine = _compute_is_routine(cur, owner.get("name"), cik, tx.get("transaction_date"))
        else:
            is_routine = None
        tx_rows.append((
            filing_id,
            owner.get("name"), owner.get("role_raw"), owner.get("role_category"),
            tx.get("transaction_date"), tx.get("transaction_code"),
            tx.get("shares"), tx.get("price_per_share"), tx.get("total_value"),
            tx.get("shares_after"),
            bool(tx.get("is_10b51", False)), bool(tx.get("is_direct", True)),
            is_routine,
        ))
    if tx_rows:
        cur.executemany(_TX_INSERT_SQL, tx_rows)
    return filing_id, len(tx_rows)


def get_history_start() -> Optional[date]:
    """
    Earliest filing date the database holds — the point before which we saw
    nothing at all. Scoring uses it to tell "this insider had no prior purchase"
    apart from "we cannot see whether they did". Keyed off filed_date, not
    transaction_date, because a single very late filing would otherwise pull the
    floor years earlier than our real coverage.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(filed_date) FROM form4_filings")
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def get_last_filed_date() -> Optional[date]:
    """Returns the most recent filed_date stored, or None if DB is empty."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(filed_date) FROM form4_filings")
            row = cur.fetchone()
            return row[0] if row and row[0] else None


# Four years, and the number is set by `_compute_is_routine`, not by taste.
#
# The routine disqualifier asks whether an insider bought the same calendar
# month in 2 of the 3 prior years, and returns None rather than guess when the
# database does not reach that far back. Under 24-month retention it structurally
# could not: 3,603 of 13,355 stored P transactions carried a NULL flag and only
# 517 were ever True, so the rule was firing on a thin biased slice. Measured on
# 2026-09-08, excluding routine buyers scored -0.19pp against the months they
# came from, at the 0th percentile of its null, which is the wrong sign for a
# rule that deletes rows.
#
# 48 months is the smallest window that lets the check see its own lookback. At
# 24 months the database sits at 103MB of Neon's 500MB, so this lands near 206MB.
# Raise it further only after re-checking that headroom.
#
# It has a second effect worth knowing. `prune_old_data` is why the research
# sample slides rather than accumulating, and why the ruler sat at 16 predictable
# months. This does not recover what is already deleted; only
# `research/scripts/build_form4_archive.py` does that.
RETENTION_MONTHS = 48


def prune_old_data(months: int = RETENTION_MONTHS) -> Tuple[int, int, int]:
    """
    Delete transactions, filings and signals older than `months`.

    Signals were never pruned. Because backfill only rescores its own date
    range, anything older kept whatever scoring model was current when it was
    written, and the dashboard showed all of it side by side. Retention has to
    cover signals too, or the table accumulates rows no rescore can ever reach.

    Returns (tx_deleted, filing_deleted, signal_deleted).
    """
    cutoff = f"{int(months)} months"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM transactions WHERE transaction_date < NOW() - %s::interval",
                (cutoff,),
            )
            tx_deleted = cur.rowcount
            cur.execute(
                "DELETE FROM form4_filings WHERE filed_date < NOW() - %s::interval",
                (cutoff,),
            )
            filing_deleted = cur.rowcount
            cur.execute(
                "DELETE FROM signals WHERE signal_date < NOW() - %s::interval",
                (cutoff,),
            )
            signal_deleted = cur.rowcount
    return tx_deleted, filing_deleted, signal_deleted


def get_active_telegram_subscribers(cur) -> List[int]:
    """
    Chat ids the alerter should fan out to. Takes an open RealDictCursor so the
    caller owns the connection, the same way write_filing does.

    An empty list is a real answer, not an error: nobody has messaged the bot
    yet, or everyone has unsubscribed.
    """
    cur.execute(
        "SELECT chat_id FROM telegram_subscribers WHERE active = TRUE ORDER BY joined_at"
    )
    return [int(r["chat_id"]) for r in cur.fetchall()]


def fill_missing_price_context(since_date, limit: Optional[int] = 5000) -> tuple:
    """
    Give every purchase filed since `since_date` the price context the scorer ranks on.

    Returns (attempted, ranked). The daily ingest calls it between writing
    filings and scoring them, so a purchase written this morning is rankable
    this morning; `bootstrap.py` calls it over its whole range with no limit.
    It is idempotent and cheap on a normal day, because price_context_bars is
    only NULL for rows nobody has looked at yet.

    A purchase left without context scores zero and is never alerted. That is
    the conservative failure, but it is silent, so the count returned here is
    logged and `audit_data.py` counts the standing total.
    """
    from psycopg2.extras import RealDictCursor, execute_batch

    from src.market.context import context_for

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT t.id, t.transaction_date, c.ticker
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                JOIN companies c ON c.cik = f.cik
                WHERE t.transaction_code = 'P'
                  AND t.price_context_bars IS NULL
                  AND c.ticker IS NOT NULL
                  AND f.filed_date >= %s
                ORDER BY c.ticker, t.transaction_date
                LIMIT %s
                """,
                (since_date, limit),
            )
            rows = cur.fetchall()

    if not rows:
        return 0, 0

    updates = [{"id": r["id"], **context_for(r["ticker"], r["transaction_date"])}
               for r in rows]
    ranked = sum(1 for u in updates if u["pct_below_52wk_high"] is not None)

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_batch(cur, """
                UPDATE transactions
                SET px_close_at_tx = %(px_close_at_tx)s,
                    px_52wk_high = %(px_52wk_high)s,
                    pct_below_52wk_high = %(pct_below_52wk_high)s,
                    price_context_bars = %(price_context_bars)s
                WHERE id = %(id)s
            """, updates, page_size=500)
    # The reference series is built from exactly these columns, so anything
    # cached before this write is missing today's filings.
    clear_discount_reference_cache()
    return len(updates), ranked


_discount_series = None


def _load_discount_series():
    """
    Every disclosed purchase's discount with the date it was disclosed, once.

    The first version queried per scoring date, which is correct and made the
    backfill four times slower: 500-odd round trips to a database that scales to
    zero when idle. One query and a binary search per date does the same work,
    and keeps the free-tier connection count where it was.

    It also read `transactions` directly, which CLAUDE.md forbids for scoring and
    for good reason. One row is one broker fill, so a purchase that arrived as
    forty fills counted forty times, and a 4/A amendment counted its restated
    transactions twice. That inflated the reference by 1.23x and skewed it toward
    whichever purchases happened to be split across the most fills. The rollup is
    one row per decision, which is what the thing being ranked also is.
    """
    import numpy as np

    from src.db.purchases import purchase_rollup

    global _discount_series
    if _discount_series is not None:
        return _discount_series

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT filed_date, pct_below_52wk_high
                FROM ({purchase_rollup("AND t.pct_below_52wk_high IS NOT NULL")}) rolled
                WHERE is_10b51 = FALSE
                  AND COALESCE(total_value, 0) >= 2000
                  AND pct_below_52wk_high IS NOT NULL
                ORDER BY filed_date
                """
            )
            rows = cur.fetchall()

    dates = np.array([r[0] for r in rows], dtype="datetime64[D]")
    values = np.array([float(r[1]) for r in rows], dtype="float64")
    _discount_series = (dates, values)
    return _discount_series


def get_discount_reference(as_of, days: int = DISCOUNT_REFERENCE_DAYS):
    """
    The 52-week discounts of purchases disclosed in the `days` before `as_of`, sorted.

    This is what `discount_score` ranks a purchase against. A fixed cutoff on a
    two-year distribution selects 2% of one month's purchases and 24% of
    another's, because the market moves every stock's discount together; ranking
    against contemporaneous filings is what keeps the cut at a decile.

    Only filings dated on or before `as_of` are included, so the reference a
    purchase is scored against contains nothing that had not been disclosed when
    it was scored. That is what makes rescoring stable: a filing that arrives
    next year cannot change a score written today.
    """
    return reference_window(_load_discount_series(), as_of, days)


def clear_discount_reference_cache() -> None:
    """Drop the cached series. Call after writing new price context."""
    global _discount_series
    _discount_series = None
