"""
Cluster signal detector.

A cluster signal fires when 3 or more distinct insiders purchase shares
in the same company within a 14-day rolling window. Research shows cluster
signals generate approximately double the alpha of single-insider buys.

(Cohen, Malloy & Pomorski 2012; multiple empirical studies on cluster buys)
"""

from datetime import date, timedelta
from typing import List
from psycopg2.extras import RealDictCursor
from src.db.connection import get_conn


CLUSTER_WINDOW_DAYS = 14
CLUSTER_MIN_INSIDERS = 3


def detect_clusters_for_ticker(ticker: str, as_of_date: date) -> dict:
    """
    Check if there is a cluster signal for `ticker` as of `as_of_date`.
    Looks back CLUSTER_WINDOW_DAYS days for distinct insiders with P transactions.

    Returns:
        {
          "is_cluster": bool,
          "insider_count": int,
          "insiders": [{"name": str, "role": str, "date": str, "value": float}],
          "window_start": date,
          "window_end": date,
        }
    """
    window_start = as_of_date - timedelta(days=CLUSTER_WINDOW_DAYS)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (insider_name)
                    insider_name, role_category, transaction_date,
                    total_value, price_per_share, shares
                FROM (
                    SELECT DISTINCT ON (t.insider_name, t.transaction_date, t.transaction_code)
                        t.insider_name, t.role_category, t.transaction_date,
                        t.total_value, t.price_per_share, t.shares, t.is_10b51
                    FROM transactions t
                    JOIN form4_filings f ON f.id = t.filing_id
                    JOIN companies c ON c.cik = f.cik
                    WHERE c.ticker = %s
                      AND t.transaction_code = 'P'
                      AND t.transaction_date BETWEEN %s AND %s
                    ORDER BY t.insider_name, t.transaction_date, t.transaction_code,
                             f.filed_date DESC
                ) deduped
                WHERE is_10b51 = FALSE
                ORDER BY insider_name, transaction_date DESC
                """,
                (ticker.upper(), window_start, as_of_date),
            )
            rows = cur.fetchall()

    insiders = [dict(r) for r in rows]
    is_cluster = len(insiders) >= CLUSTER_MIN_INSIDERS

    return {
        "is_cluster": is_cluster,
        "insider_count": len(insiders),
        "insiders": insiders,
        "window_start": window_start,
        "window_end": as_of_date,
    }


def get_tickers_with_recent_purchases(since_date: date) -> List[str]:
    """
    Returns all tickers that have at least one open-market purchase (P)
    with a transaction_date >= since_date. Used to know which tickers
    to run the cluster detector on.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT c.ticker
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                JOIN companies c ON c.cik = f.cik
                WHERE t.transaction_code = 'P'
                  AND t.is_10b51 = FALSE
                  AND t.transaction_date >= %s
                  AND c.ticker IS NOT NULL
                  AND c.ticker NOT IN ('NONE', 'NA', 'N/A', 'NULL', '')
                """,
                (since_date,),
            )
            rows = cur.fetchall()
    return [r[0] for r in rows if r[0]]
