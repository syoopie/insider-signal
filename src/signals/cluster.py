"""
Cluster detection: 3 or more distinct insiders buying one company inside 14 days.

A cluster changes how a signal is classified and adds no points. Measured on
this data, cluster size does not order returns, and inside the most discounted
third of purchases the number of buyers points the wrong way.

Sub-flags on the returned dict:
  executive_cluster: a CFO, CEO, COO or Chairman is among the participants.
  tight_cluster: 3+ distinct insiders bought within a 5-day sub-window.

`cluster_from_transactions` is the one definition of a cluster, and
`src/signals/batch.py` is its only production caller.
"""

from collections import Counter
from datetime import date, timedelta

CLUSTER_WINDOW_DAYS = 14
CLUSTER_MIN_INSIDERS = 3
TIGHT_CLUSTER_DAYS = 5

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
    is kept. The caller is responsible for passing only rows already disclosed
    by `as_of_date`.

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
