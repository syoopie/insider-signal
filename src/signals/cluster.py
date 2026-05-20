"""
Cluster signal detector.

A cluster signal fires when 3 or more distinct insiders purchase shares
in the same company within a 14-day rolling window. Research shows cluster
signals generate approximately double the alpha of single-insider buys.

Sub-flags added to the returned dict:
  executive_cluster: True if any participant is CFO, CEO, COO, or Chairman.
    Per Kang/Kim/Wang research, executive+director clusters are more informative
    than director-only clusters.
  tight_cluster: True if 3+ distinct insiders bought within a 5-day window.
    Tighter temporal clustering has stronger signal per empirical studies.

(Cohen, Malloy & Pomorski 2012; multiple empirical studies on cluster buys)
"""

from datetime import date, timedelta
from typing import List
from psycopg2.extras import RealDictCursor
from src.db.connection import get_conn


CLUSTER_WINDOW_DAYS  = 14
CLUSTER_MIN_INSIDERS = 3
TIGHT_CLUSTER_DAYS   = 5  # sub-window for tight_cluster flag

# Minimum purchase value to count toward cluster threshold.
# Filters out DRIP/401k noise (tiny automatic contributions).
CLUSTER_MIN_VALUE = 25_000

_EXECUTIVE_ROLES = {"cfo", "ceo", "coo", "chairman"}


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
                    total_value, price_per_share, shares, is_direct
                FROM (
                    SELECT DISTINCT ON (t.insider_name, t.transaction_date, t.transaction_code)
                        t.insider_name, t.role_category, t.transaction_date,
                        t.total_value, t.price_per_share, t.shares, t.is_10b51, t.is_direct
                    FROM transactions t
                    JOIN form4_filings f ON f.id = t.filing_id
                    JOIN companies c ON c.cik = f.cik
                    WHERE c.ticker = %s
                      AND t.transaction_code = 'P'
                      AND t.transaction_date BETWEEN %s AND %s
                      AND t.is_direct = TRUE
                      AND COALESCE(t.total_value, 0) >= %s
                    ORDER BY t.insider_name, t.transaction_date, t.transaction_code,
                             f.filed_date DESC
                ) deduped
                WHERE is_10b51 = FALSE
                ORDER BY insider_name, transaction_date DESC
                """,
                (ticker.upper(), window_start, as_of_date, CLUSTER_MIN_VALUE),
            )
            rows = cur.fetchall()

    all_insiders = [dict(r) for r in rows]

    # Filter offering contamination — two complementary checks:
    #
    # 1. Identical-block (exact duplicate): same shares + price + date with ≥3 buyers
    #    → DRIP plan lots or exact-allocation blocks. Remove the entire group.
    #
    # 2. Same-price offering: same price + date (different share amounts) with ≥3 buyers
    #    → IPO/PIPE/secondary where each insider gets a different allocation size at a
    #    fixed offer price. These are not independent buying decisions.
    #    (BKV IPO at $18.00, COSO at $21.50, BETA at $34.00 confirmed by backtest.)
    from collections import Counter
    block_keys = Counter(
        (ins["shares"], ins["price_per_share"], ins["transaction_date"])
        for ins in all_insiders
    )
    price_date_keys = Counter(
        (ins["price_per_share"], ins["transaction_date"])
        for ins in all_insiders
    )
    insiders = [
        ins for ins in all_insiders
        if (block_keys[(ins["shares"], ins["price_per_share"], ins["transaction_date"])] < 3
            and price_date_keys[(ins["price_per_share"], ins["transaction_date"])] < 3)
    ]

    is_cluster = len(insiders) >= CLUSTER_MIN_INSIDERS

    executive_cluster = is_cluster and any(
        (ins.get("role_category") or "").lower() in _EXECUTIVE_ROLES
        for ins in insiders
    )

    tight_cluster = False
    if is_cluster:
        raw_dates = [ins.get("transaction_date") for ins in insiders if ins.get("transaction_date")]
        parsed = []
        for d in raw_dates:
            if isinstance(d, date):
                parsed.append(d)
            else:
                try:
                    parsed.append(date.fromisoformat(str(d)[:10]))
                except (ValueError, TypeError):
                    pass
        parsed.sort()
        # Slide a TIGHT_CLUSTER_DAYS window looking for 3+ insiders in span
        for i in range(len(parsed) - CLUSTER_MIN_INSIDERS + 1):
            span = (parsed[i + CLUSTER_MIN_INSIDERS - 1] - parsed[i]).days
            if span <= TIGHT_CLUSTER_DAYS:
                tight_cluster = True
                break

    return {
        "is_cluster": is_cluster,
        "insider_count": len(insiders),
        "insiders": insiders,
        "window_start": window_start,
        "window_end": as_of_date,
        "executive_cluster": executive_cluster,
        "tight_cluster": tight_cluster,
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
