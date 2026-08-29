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

`cluster_from_transactions` is the one definition of a cluster. Both the live
path (`detect_clusters_for_ticker`, which loads rows from the DB) and the
historical backfill (`scripts/backfill_signals.py`, which pre-loads rows in
bulk) call it, so the eligibility rules can't drift between the two.

(Cohen, Malloy & Pomorski 2012; multiple empirical studies on cluster buys)
"""

from collections import Counter
from datetime import date, timedelta
from typing import List

from psycopg2.extras import RealDictCursor

from src.db.connection import get_conn

CLUSTER_WINDOW_DAYS = 14
CLUSTER_MIN_INSIDERS = 3
TIGHT_CLUSTER_DAYS = 5  # sub-window for the tight_cluster flag

# Minimum purchase value to count toward the cluster threshold.
# Filters out DRIP/401k noise (tiny automatic contributions).
CLUSTER_MIN_VALUE = 25_000

_EXECUTIVE_ROLES = {"cfo", "ceo", "coo", "chairman"}


def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _drop_offering_contamination(insiders: list) -> list:
    """
    Remove buyers that were part of an offering rather than independent decisions:
      1. Identical block: 3+ buyers sharing (shares, price, date) — DRIP lots or
         exact-allocation blocks.
      2. Same-price offering: 3+ buyers sharing (price, date) with different share
         counts — IPO/PIPE/secondary at a fixed offer price (BKV at $18.00,
         COSO at $21.50, BETA at $34.00, all confirmed underperformers).
    """
    block = Counter(
        (i.get("shares"), i.get("price_per_share"), i.get("transaction_date")) for i in insiders
    )
    price_date = Counter(
        (i.get("price_per_share"), i.get("transaction_date")) for i in insiders
    )
    return [
        i
        for i in insiders
        if block[(i.get("shares"), i.get("price_per_share"), i.get("transaction_date"))] < 3
        and price_date[(i.get("price_per_share"), i.get("transaction_date"))] < 3
    ]


def cluster_from_transactions(txs: list, as_of_date: date) -> dict:
    """
    Decide whether `txs` form a cluster as of `as_of_date`.

    Each dict needs: insider_name, role_category, transaction_date, total_value,
    price_per_share, shares, is_direct, and optionally is_10b51. Rows should be
    ordered newest-first; when an insider bought more than once, the newest row
    is kept.

    Returns:
        {
          "is_cluster": bool,
          "insider_count": int,
          "insiders": [tx dict, ...],   # the rows that survived every filter
          "window_start": date,
          "window_end": date,
          "executive_cluster": bool,
          "tight_cluster": bool,
        }
    """
    window_start = as_of_date - timedelta(days=CLUSTER_WINDOW_DAYS)

    one_per_insider: dict = {}
    for tx in txs:
        td = _as_date(tx.get("transaction_date"))
        if td is None or not (window_start <= td <= as_of_date):
            continue
        if tx.get("is_direct") is False:
            continue
        if (tx.get("total_value") or 0) < CLUSTER_MIN_VALUE:
            continue
        if tx.get("is_10b51") is True:
            continue
        one_per_insider.setdefault(tx.get("insider_name") or "Unknown", tx)

    insiders = _drop_offering_contamination(list(one_per_insider.values()))
    is_cluster = len(insiders) >= CLUSTER_MIN_INSIDERS

    executive_cluster = is_cluster and any(
        (i.get("role_category") or "").lower() in _EXECUTIVE_ROLES for i in insiders
    )

    tight_cluster = False
    if is_cluster:
        days = sorted(d for d in (_as_date(i.get("transaction_date")) for i in insiders) if d)
        for i in range(len(days) - CLUSTER_MIN_INSIDERS + 1):
            if (days[i + CLUSTER_MIN_INSIDERS - 1] - days[i]).days <= TIGHT_CLUSTER_DAYS:
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


def detect_clusters_for_ticker(ticker: str, as_of_date: date) -> dict:
    """
    Cluster verdict for `ticker` as of `as_of_date`, reading the last
    CLUSTER_WINDOW_DAYS days of open-market purchases from the database.
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
            rows = [dict(r) for r in cur.fetchall()]

    return cluster_from_transactions(rows, as_of_date)


def get_tickers_with_recent_purchases(since_date: date) -> List[str]:
    """
    Tickers with at least one open-market purchase *disclosed* since since_date.

    Keyed off filed_date rather than transaction_date so a Form 4 reporting an
    older trade still puts its ticker in the scoring queue on the day it lands.
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
                  AND f.filed_date >= %s
                  AND c.ticker IS NOT NULL
                  AND c.ticker NOT IN ('NONE', 'NA', 'N/A', 'NULL', '')
                """,
                (since_date,),
            )
            rows = cur.fetchall()
    return [r[0] for r in rows if r[0]]
