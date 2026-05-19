"""
Signal scoring engine.

Scores each open-market purchase (transaction code 'P') against the
research-backed factor table. Returns a score 0–100 with a full
breakdown of which factors fired and why.

Research basis:
  - Cohen, Malloy & Pomorski (2012): opportunistic trades >> routine trades;
    routine = same calendar month in ≥2 of preceding 3 years → disqualified
  - Lakonishok & Lee (2001): small-cap insider buys = +7.4% abnormal at 12mo
  - TipRanks/ResearchGate CFO study: CFO > Director > Officer > CEO by return
  - Cluster research: 3+ insiders buying ≈ 2× alpha of single buy
  - Pficdn et al.: large purchases as % of holdings predict abnormal returns;
    small fraction-of-holdings purchases are not informative

Cluster qualification note:
  classify_signal() uses the *average* of all participant scores (not the max
  individual score) to gate CLUSTER_BUY signals. This means three directors
  each scoring 42 qualify as CLUSTER_BUY (avg=42 ≥ 35 threshold) even though
  no single insider cleared 50. The collective action is the signal.

Score factor mutual exclusivity:
  first_purchase_12mo and sequenced_buying_30d are mutually exclusive by
  definition — first_purchase_12mo fires only when there are NO purchases in
  the preceding 365 days, while sequenced_buying_30d fires only when there IS
  a purchase within the preceding 30 days.
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
# unknown defaults to small-cap score: the Russell 2000 universe skews small,
# and assigning 0 under-penalises every company without fresh market data.
CAP_SCORES = {
    "small":   15,
    "mid":      8,
    "large":    0,
    "unknown": 15,
}

# Indirect purchases (is_direct=False) are made through LLCs/trusts/family
# entities — demonstrably weaker signals and inflate artificial cluster counts
# (e.g. fund partners each filing separately for the same block trade).
INDIRECT_PENALTY = -8


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

    # Hard disqualifier: trivially small purchase (< $2,000).
    # Sub-threshold buys are noise — automatic DRIP/401k contributions, dividend
    # reinvestment, or negligible open-market buys with no informational content.
    total_value = transaction.get("total_value") or 0
    if total_value < 2_000:
        return {"score": 0, "breakdown": {"trivial_value": "DISQUALIFIED"}, "disqualified": True, "eligible": False}

    # Hard disqualifier: routine trader (CMP 2012)
    # Routine = bought in the same calendar month in ≥2 of the preceding 3 years.
    # If the transaction row already has is_routine pre-computed (stored at ingest
    # time), use it directly — avoids dependence on pruned historical data.
    tx_date_str = transaction.get("transaction_date") or ""
    try:
        tx_date = date.fromisoformat(tx_date_str[:10])
    except (ValueError, TypeError):
        tx_date = date.today()

    stored_is_routine = transaction.get("is_routine")
    if stored_is_routine is True:
        return {"score": 0, "breakdown": {"routine_trader": "DISQUALIFIED"}, "disqualified": True, "eligible": False}
    elif stored_is_routine is None:
        # Not yet computed — fall back to live calculation from prior_purchases.
        tx_month = tx_date.month
        oldest_available = min(
            (_parse_date(p.get("transaction_date")) for p in prior_purchases
             if _parse_date(p.get("transaction_date"))),
            default=None,
        )
        routine_years = 0
        for yr_back in (1, 2, 3):
            yr = tx_date.year - yr_back
            if oldest_available is None or oldest_available > date(yr, 12, 31):
                continue
            year_start = date(yr, tx_month, 1)
            year_end   = date(yr, tx_month, 28)
            if any(year_start <= (_parse_date(p.get("transaction_date")) or date.min) <= year_end
                   for p in prior_purchases):
                routine_years += 1
        if routine_years >= 2:
            return {"score": 0, "breakdown": {"routine_trader": "DISQUALIFIED"}, "disqualified": True, "eligible": False}

    breakdown = {}
    score = 0

    # --- Indirect purchase penalty ---
    # is_direct=False means the purchase was made through a trust, LLC, or family
    # entity. These inflate cluster counts (fund partners filing separately for
    # the same block) and carry less personal conviction than direct account buys.
    is_direct = transaction.get("is_direct", True)
    if is_direct is False:
        breakdown["indirect_purchase"] = INDIRECT_PENALTY
        score += INDIRECT_PENALTY

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

    # --- Transaction value (absolute) ---
    # total_value already validated above (≥ $2,000)
    if total_value >= 500_000:
        breakdown["value_500k_plus"] = 12
        score += 12
    elif total_value >= 100_000:
        breakdown["value_100k_plus"] = 8
        score += 8

    # --- Purchase as % of prior holdings (Pficdn et al.) ---
    shares_bought = float(transaction.get("shares") or 0)
    shares_after  = float(transaction.get("shares_after") or 0)
    if shares_bought > 0 and shares_after > shares_bought:
        shares_before = shares_after - shares_bought
        pct_increase = shares_bought / shares_before * 100
        if pct_increase >= 30:
            breakdown["holdings_increase_30pct"] = 15
            score += 15
        elif pct_increase >= 15:
            breakdown["holdings_increase_15pct"] = 10
            score += 10
        elif pct_increase >= 5:
            breakdown["holdings_increase_5pct"] = 5
            score += 5

    # --- First purchase in 12+ months ---
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

    # --- Near 52-week low (tiered) ---
    price = float(transaction.get("price_per_share") or 0) or None
    low_52wk = market_data.get("price_52wk_low")
    if price and low_52wk and low_52wk > 0:
        pct_above_low = (price - low_52wk) / low_52wk * 100
        if pct_above_low <= 5:
            breakdown["near_52wk_low_5pct"] = 12
            score += 12
        elif pct_above_low <= 10:
            breakdown["near_52wk_low_10pct"] = 7
            score += 7

    return {
        "score": min(score, 100),
        "breakdown": breakdown,
        "disqualified": False,
        "eligible": True,
    }


def classify_signal(
    score: int,
    cluster_flag: bool,
    participant_scores: list = None,
) -> str:
    """
    Classify a signal given the max individual score and cluster information.

    score: max individual transaction score (0–100)
    cluster_flag: True if 3+ distinct insiders bought in the 14-day window
    participant_scores: list of individual eligible scores for each cluster
        participant. Used to compute the cluster-aggregate score so that a
        group of moderately-scoring insiders qualifies as CLUSTER_BUY even
        when no single insider clears the individual threshold.

    Cluster qualification uses average(participant_scores) ≥ 35.
    A lower bar is justified because the collective action is the signal —
    research shows cluster buys generate ~2× alpha vs single buys regardless
    of the absolute score of any one participant.
    """
    if cluster_flag:
        if participant_scores:
            cluster_avg = int(sum(participant_scores) / len(participant_scores))
        else:
            cluster_avg = score  # fallback for callers that don't supply scores
        if cluster_avg >= 35:
            return "CLUSTER_BUY"
        return "WATCH"  # very weak cluster: surface on dashboard, no alert
    if score >= 65:
        return "BUY"
    if score >= 45:
        return "WATCH"
    return "LOW"


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None
