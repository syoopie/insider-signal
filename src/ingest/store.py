"""
DB persistence layer for Form 4 filings and transactions.
All inserts are idempotent — safe to re-run on the same data.
"""

import json
import decimal
from datetime import date, datetime
from typing import Optional, Tuple, List
from src.db.connection import get_conn


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


_INVALID_TICKERS = {"", "NONE", "NA", "N/A", "NULL"}


def _clean_ticker(ticker: str):
    """Return the ticker uppercased, or None if it's a sentinel/missing value."""
    if not ticker:
        return None
    t = ticker.strip().upper()
    return None if t in _INVALID_TICKERS else t


def upsert_company(cik: str, ticker: str, name: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO companies (cik, ticker, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (cik) DO UPDATE
                    SET ticker = EXCLUDED.ticker,
                        name   = EXCLUDED.name
                """,
                (cik, _clean_ticker(ticker), name),
            )


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


def insert_filing(accession_number: str, cik: str, filed_date: str, period_date: str) -> Optional[int]:
    """
    Insert a Form 4 filing record. Returns the filing ID, or None if it already exists.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO form4_filings (accession_number, cik, filed_date, period_date)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (accession_number) DO NOTHING
                RETURNING id
                """,
                (accession_number, cik, filed_date or None, period_date or None),
            )
            row = cur.fetchone()
            return row[0] if row else None


def insert_transactions(filing_id: int, owner: dict, transactions: list) -> int:
    """
    Insert all transactions for a filing. Returns count of rows inserted.
    """
    if not transactions:
        return 0

    rows = []
    for tx in transactions:
        rows.append((
            filing_id,
            owner.get("name"),
            owner.get("role_raw"),
            owner.get("role_category"),
            tx.get("transaction_date"),
            tx.get("transaction_code"),
            tx.get("shares"),
            tx.get("price_per_share"),
            tx.get("total_value"),
            tx.get("shares_after"),
            bool(tx.get("is_10b51", False)),
            bool(tx.get("is_direct", True)),
        ))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO transactions (
                    filing_id, insider_name, insider_role, role_category,
                    transaction_date, transaction_code, shares, price_per_share,
                    total_value, shares_after, is_10b51, is_direct
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )
    return len(rows)


def get_last_filed_date() -> Optional[date]:
    """Returns the most recent filed_date stored, or None if DB is empty."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(filed_date) FROM form4_filings")
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def save_signal(ticker: str, signal_date: date, score: int, signal_type: str,
                cluster_flag: bool, score_breakdown: dict, evidence: dict) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signals
                    (ticker, signal_date, score, signal_type, cluster_flag, score_breakdown, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (ticker, signal_date, score, signal_type, cluster_flag,
                 _dumps(score_breakdown),
                 _dumps(evidence)),
            )
            return cur.fetchone()[0]


def mark_signal_alerted(signal_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE signals SET alerted = TRUE WHERE id = %s", (signal_id,))


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
