"""
Signal scoring engine.

Scores each open-market purchase (transaction code 'P') against the
research-backed factor table. Returns a score 0–100 with a full
breakdown of which factors fired and why.

Research basis:
  - Cohen, Malloy & Pomorski (2012): opportunistic trades >> routine trades
  - Lakonishok & Lee (2001): small-cap insider buys = +7.4% abnormal at 12mo
  - TipRanks/ResearchGate CFO study: CFO > Director > Officer > CEO by return
  - Cluster research: 3+ insiders buying ≈ 2× alpha of single buy
"""

from datetime import date, timedelta
from typing import Optional


# Role → base score delta
ROLE_SCORES = {
    "cfo":      20,
    "director": 16,
    "coo":      12,
    "chairman": 14,
    "officer":  12,
    "ceo":      10,
    "other":     6,
}

# Market cap tier → score delta
CAP_SCORES = {
    "small":   15,
    "mid":      8,
    "large":    0,
    "unknown":  5,
}


def score_transaction(
    transaction: dict,
    owner: dict,
    company: dict,
    market_data: dict,
    prior_purchases: list,  # previous P transactions by same insider (any date)
) -> Optional[dict]:
    """
    Score a single transaction. Returns None if ineligible (not a P, is 10b5-1, etc.).

    Returns:
        {
          "score": int,
          "breakdown": {factor_name: points},
          "disqualified": False,
          "eligible": True,
        }
    """
    tx_code = (transaction.get("transaction_code") or "").upper()
    is_10b51 = bool(transaction.get("is_10b51", False))

    # Only score open-market purchases
    if tx_code != "P":
        return None

    # Hard disqualifier: 10b5-1 pre-arranged plan
    if is_10b51:
        return {"score": 0, "breakdown": {"10b5_1_plan": "DISQUALIFIED"}, "disqualified": True, "eligible": False}

    breakdown = {}
    score = 0

    # --- Role ---
    role = owner.get("role_category", "other")
    role_pts = ROLE_SCORES.get(role, 6)
    breakdown[f"role_{role}"] = role_pts
    score += role_pts

    # --- Market cap tier ---
    cap_tier = company.get("cap_tier") or market_data.get("cap_tier", "unknown")
    cap_pts = CAP_SCORES.get(cap_tier, 5)
    if cap_pts > 0:
        breakdown[f"cap_{cap_tier}"] = cap_pts
    score += cap_pts

    # --- Transaction value ---
    total_value = transaction.get("total_value") or 0
    if total_value >= 500_000:
        breakdown["value_500k_plus"] = 12
        score += 12
    elif total_value >= 100_000:
        breakdown["value_100k_plus"] = 8
        score += 8

    # --- First purchase in 12+ months ---
    tx_date_str = transaction.get("transaction_date") or ""
    try:
        tx_date = date.fromisoformat(tx_date_str[:10])
    except (ValueError, TypeError):
        tx_date = date.today()

    cutoff_12mo = tx_date - timedelta(days=365)
    recent_prior = [
        p for p in prior_purchases
        if _parse_date(p.get("transaction_date")) and
        _parse_date(p.get("transaction_date")) >= cutoff_12mo
    ]
    if not recent_prior:
        breakdown["first_purchase_12mo"] = 10
        score += 10

    # --- Sequenced buying (2nd purchase within 30 days) ---
    cutoff_30d = tx_date - timedelta(days=30)
    recent_30d = [
        p for p in prior_purchases
        if _parse_date(p.get("transaction_date")) and
        cutoff_30d <= _parse_date(p.get("transaction_date")) < tx_date
    ]
    if recent_30d:
        breakdown["sequenced_buying_30d"] = 8
        score += 8

    # --- Near 52-week low ---
    price = float(transaction.get("price_per_share") or 0) or None
    low_52wk = market_data.get("price_52wk_low")
    if price and low_52wk and low_52wk > 0:
        pct_above_low = (price - low_52wk) / low_52wk * 100
        if pct_above_low <= 10:
            breakdown["near_52wk_low"] = 10
            score += 10

    return {
        "score": min(score, 100),
        "breakdown": breakdown,
        "disqualified": False,
        "eligible": True,
    }


def classify_signal(score: int, cluster_flag: bool) -> str:
    if cluster_flag and score >= 50:
        return "CLUSTER_BUY"
    if score >= 65:
        return "BUY"
    if score >= 45:
        return "WATCH"
    if cluster_flag:
        return "WATCH"  # weak cluster: save for dashboard but don't alert
    return "LOW"


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None
