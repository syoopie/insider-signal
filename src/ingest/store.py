"""
DB persistence layer for Form 4 filings and transactions.
All inserts are idempotent — safe to re-run on the same data.
"""

import json
import decimal
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List
from src.db.connection import get_conn
from src.ingest.common import _clean_ticker

_SIGNAL_COOLDOWN_DAYS = 7   # suppress follow-up signals within this window
_SIGNAL_SCORE_JUMP    = 10  # unless score increased by at least this much
_TYPE_RANK = {"CLUSTER_BUY": 3, "BUY": 2, "WATCH": 1, "LOW": 0}


def _is_suppressed(ticker: str, signal_date: date, score: int, signal_type: str,
                   recent: dict) -> bool:
    """
    Return True if this signal should be suppressed because a recent signal for
    the same ticker already covers the same episode.

    recent: {ticker: (signal_date, score, signal_type)} for the most recent
    signal per ticker in the last _SIGNAL_COOLDOWN_DAYS days.
    """
    prev = recent.get(ticker)
    if prev is None:
        return False
    prev_date, prev_score, prev_type = prev
    days_since = (signal_date - prev_date).days
    if days_since >= _SIGNAL_COOLDOWN_DAYS:
        return False
    if score >= prev_score + _SIGNAL_SCORE_JUMP:
        return False
    if _TYPE_RANK.get(signal_type, 0) > _TYPE_RANK.get(prev_type, 0):
        return False
    return True


class _JSONEncoder(json.JSONEncoder):
    """Handles types that come out of psycopg2 rows: Decimal, date, datetime."""
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_JSONEncoder)


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
            year_end   = _date(yr, tx_month, 28)
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
    tkr     = _clean_ticker(issuer.get("ticker") or ticker) or ""

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
            (cik, _clean_ticker(tkr), issuer.get("name", "")),
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


def update_company_market_data(cik: str, market_cap: Optional[int], cap_tier: Optional[str]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies
                SET market_cap = %s, cap_tier = %s, updated_at = now()
                WHERE cik = %s
                """,
                (market_cap, cap_tier, cik),
            )


def get_last_filed_date() -> Optional[date]:
    """Returns the most recent filed_date stored, or None if DB is empty."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(filed_date) FROM form4_filings")
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def save_signal(ticker: str, signal_date: date, score: int, signal_type: str,
                cluster_flag: bool, score_breakdown: dict, evidence: dict) -> int:
    """Returns the signal id, or 0 if suppressed as a near-duplicate."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check for a recent signal for this ticker within the cooldown window.
            cutoff = signal_date - timedelta(days=_SIGNAL_COOLDOWN_DAYS)
            cur.execute(
                """
                SELECT signal_date, score, signal_type FROM signals
                WHERE ticker = %s AND signal_date >= %s AND signal_date < %s
                ORDER BY signal_date DESC LIMIT 1
                """,
                (ticker, cutoff, signal_date),
            )
            row = cur.fetchone()
            recent = {ticker: row} if row else {}
            if _is_suppressed(ticker, signal_date, score, signal_type, recent):
                return 0

            cur.execute(
                """
                INSERT INTO signals
                    (ticker, signal_date, score, signal_type, cluster_flag, score_breakdown, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, signal_date) DO UPDATE SET
                    score           = EXCLUDED.score,
                    signal_type     = EXCLUDED.signal_type,
                    cluster_flag    = EXCLUDED.cluster_flag,
                    score_breakdown = EXCLUDED.score_breakdown,
                    evidence        = EXCLUDED.evidence
                RETURNING id
                """,
                (ticker, signal_date, score, signal_type, cluster_flag,
                 _dumps(score_breakdown),
                 _dumps(evidence)),
            )
            return cur.fetchone()[0]


def batch_save_signals(signals: list) -> int:
    """
    Insert or update a list of signal dicts in a single connection.
    Each dict must have the same keys as save_signal's parameters.

    Suppresses follow-up signals for the same ticker within _SIGNAL_COOLDOWN_DAYS
    unless the score jumps ≥ _SIGNAL_SCORE_JUMP or the type upgrades. Signals are
    processed in date order so earlier ones in the batch act as the "recent" anchor
    for later ones.

    Returns the count of rows written.
    """
    if not signals:
        return 0

    # Sort by (ticker, signal_date) so within-batch deduplication is date-ordered.
    signals = sorted(signals, key=lambda s: (s["ticker"], s["signal_date"]))
    tickers = list({s["ticker"] for s in signals})
    min_date = min(s["signal_date"] for s in signals)
    cutoff = min_date - timedelta(days=_SIGNAL_COOLDOWN_DAYS)

    # Bulk-load the most recent pre-existing signal per ticker in the lookback window.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker) ticker, signal_date, score, signal_type
                FROM signals
                WHERE ticker = ANY(%s) AND signal_date >= %s AND signal_date < %s
                ORDER BY ticker, signal_date DESC
                """,
                (tickers, cutoff, min_date),
            )
            recent = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    sql = """
        INSERT INTO signals
            (ticker, signal_date, score, signal_type, cluster_flag, score_breakdown, evidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, signal_date) DO UPDATE SET
            score           = EXCLUDED.score,
            signal_type     = EXCLUDED.signal_type,
            cluster_flag    = EXCLUDED.cluster_flag,
            score_breakdown = EXCLUDED.score_breakdown,
            evidence        = EXCLUDED.evidence
    """
    rows = []
    suppressed = 0
    for s in signals:
        ticker, sig_date, score, sig_type = (
            s["ticker"], s["signal_date"], s["score"], s["signal_type"]
        )
        if _is_suppressed(ticker, sig_date, score, sig_type, recent):
            suppressed += 1
            continue
        rows.append((
            ticker, sig_date, score, sig_type,
            s["cluster_flag"], _dumps(s["score_breakdown"]), _dumps(s["evidence"]),
        ))
        # Update recent so later signals in the same batch see this one.
        recent[ticker] = (sig_date, score, sig_type)

    if rows:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)

    if suppressed:
        from src.ingest.common import log
        log(f"  Suppressed {suppressed} near-duplicate signals (cooldown={_SIGNAL_COOLDOWN_DAYS}d)")
    return len(rows)


def dedup_suppressed_signals(since=None, until=None) -> int:
    """
    Delete signals that would be suppressed by the cooldown logic but slipped
    through because the prior signal didn't exist in the DB at write time.

    Removes S2 when, within the cooldown window, an earlier S1 exists for the
    same ticker that is of equal/higher type rank AND the score didn't jump by
    _SIGNAL_SCORE_JUMP.  Same logic as _is_suppressed, applied retroactively.

    Returns count of rows deleted.
    """
    type_rank_sql = """
        CASE signal_type
            WHEN 'CLUSTER_BUY' THEN 3
            WHEN 'BUY'         THEN 2
            WHEN 'WATCH'       THEN 1
            ELSE 0
        END
    """
    params = []
    date_filter = ""
    if since:
        date_filter += " AND s2.signal_date >= %s"
        params.append(since)
    if until:
        date_filter += " AND s2.signal_date <= %s"
        params.append(until)

    sql = f"""
        WITH prev AS (
            SELECT DISTINCT ON (s2.id)
                s2.id,
                ({type_rank_sql.replace('signal_type','s2.signal_type')}) AS s2_rank,
                s2.score                                                    AS s2_score,
                ({type_rank_sql.replace('signal_type','s1.signal_type')}) AS s1_rank,
                s1.score                                                    AS s1_score
            FROM signals s2
            JOIN signals s1
              ON  s1.ticker      = s2.ticker
              AND s1.signal_date < s2.signal_date
              AND s2.signal_date - s1.signal_date < {_SIGNAL_COOLDOWN_DAYS}
              AND s1.signal_type != 'LOW'
            {date_filter.replace('s2.', 's2.')}
            ORDER BY s2.id, s1.signal_date DESC
        )
        DELETE FROM signals
        WHERE id IN (
            SELECT id FROM prev
            WHERE s2_rank  <= s1_rank
              AND s2_score  <  s1_score + {_SIGNAL_SCORE_JUMP}
        )
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def mark_signal_alerted(signal_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE signals SET alerted = TRUE WHERE id = %s", (signal_id,))


def backfill_routine_flags(batch_size: int = 500) -> int:
    """
    Batch-compute is_routine for all P transactions where is_routine IS NULL.

    Run this once after bootstrap to pre-populate the flag before the
    2-year retention window starts pruning historical data that the live
    routine check depends on. Commits in batches of batch_size to avoid
    long-running transactions.

    Returns the number of transactions updated.
    """
    # Fetch all candidates first (read-only pass).
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.insider_name, t.transaction_date, f.cik
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                WHERE t.transaction_code = 'P'
                  AND t.is_10b51 = FALSE
                  AND t.is_routine IS NULL
                ORDER BY t.transaction_date
                """
            )
            candidates = cur.fetchall()

    if not candidates:
        return 0

    updated = 0
    batch: list = []
    for tx_id, insider_name, tx_date, cik in candidates:
        # Each flag computation opens its own short connection.
        with get_conn() as conn:
            with conn.cursor() as cur:
                flag = _compute_is_routine(cur, insider_name, cik, tx_date)
        if flag is not None:
            batch.append((flag, tx_id))
        if len(batch) >= batch_size:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for flag_val, tid in batch:
                        cur.execute("UPDATE transactions SET is_routine = %s WHERE id = %s", (flag_val, tid))
            updated += len(batch)
            batch = []

    if batch:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for flag_val, tid in batch:
                    cur.execute("UPDATE transactions SET is_routine = %s WHERE id = %s", (flag_val, tid))
        updated += len(batch)

    return updated


def prune_old_data(months: int = 24) -> Tuple[int, int]:
    """Delete transactions and filings older than `months` months. Returns (tx_deleted, filing_deleted)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM transactions WHERE transaction_date < NOW() - INTERVAL '%s months'",
                (months,),
            )
            tx_deleted = cur.rowcount
            cur.execute(
                "DELETE FROM form4_filings WHERE filed_date < NOW() - INTERVAL '%s months'",
                (months,),
            )
            filing_deleted = cur.rowcount
    return tx_deleted, filing_deleted


def get_unalerted_signals(min_score: int = 45) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ticker, signal_date, score, signal_type, cluster_flag,
                       score_breakdown, evidence
                FROM signals
                WHERE alerted = FALSE
                  AND (score >= %s OR cluster_flag = TRUE)
                ORDER BY score DESC, signal_date DESC
                """,
                (min_score,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]
