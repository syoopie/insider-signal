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

Score factor mutual exclusivity (timing factors):
  Three mutually exclusive purchase-history factors — exactly one fires per signal:
  - sequenced_buying_30d  (+8):  prior buy within 30 days (rapid sequence)
  - prior_purchase_31_365d (+12): prior buy in 31-364 days (sustained conviction)
  - first_purchase_12mo   (+3):  no prior buy in 365 days (new/returning buyer)
"""

from datetime import date, timedelta
from typing import Optional


# Role → base score delta.
# Empirical calibration round 2 (2026-05-25): full factor-lift analysis on 229/199 signals.
# role_ceo removed (0): -12%/-11% lift at 60d/90d — confirmed harmful, not informative.
# role_chairman reduced (8): only n=2 signals, -4%/-11% lift — precautionary reduction.
# role_other kept at 0: -22%/-3% — confirmed noise.
ROLE_SCORES = {
    "cfo":       15,  # +2.4%/-0.1% — slight positive, keep
    "director":  16,  # -2.7%/+6.2% — positive at 90d, keep
    "coo":       15,  # +7.6%/+27.5% — strong (small sample n=3-5)
    "chairman":   8,  # -4.4%/-11.2% — negative, reduced from 14; too few samples to zero
    "officer":   12,  # +17.1%/-13.5% — mixed; 60d very strong
    "ceo":        0,  # -12.1%/-11.4% — confirmed negative at both horizons
    "other":      0,  # -22.2%/-2.8% — confirmed noise
}

# Market cap tier → score delta.
# cap_mid removed (0): -2.8%/-7.6% — confirmed negative at both horizons.
# cap_unknown restored to 5: empirical lift shows +2.6% at 60d, +6.9% at 90d.
CAP_SCORES = {
    "small":    15,  # +0.6%/-0.1% — slightly positive, keep
    "mid":       0,  # -2.8%/-7.6% — confirmed negative; removed
    "large":     0,
    "unknown":   5,  # +2.6%/+6.9% — positive; restored from 0
}

# Indirect purchase penalty confirmed at -15.
# Empirical lift: -10.2% at 60d, -18.2% at 90d — severe and consistent.
INDIRECT_PENALTY = -15


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
    # value_500k_plus: increased to 15; strong 90d lift (+10.5%). Large committed buys predict returns.
    # value_100k_plus: removed (0); confirmed negative at both horizons (-3.6%/-7.6%).
    #   The 100k-500k range does not meaningfully separate insiders willing to commit real capital.
    if total_value >= 500_000:
        breakdown["value_500k_plus"] = 15  # was 9; +0.0%/+10.5% lift — 90d strong
        score += 15

    # --- Purchase as % of prior holdings (Pficdn et al.) ---
    # holdings_30pct: reduced to 10 (was 15); mixed signal (-3.8%/+2.1% lift).
    # holdings_15pct: reduced to 3 (was 5); essentially neutral (+0.9%/-0.8%).
    # holdings_5pct: increased to 10 (was 5); strongly positive (+11.7%/+4.5% lift).
    shares_bought = float(transaction.get("shares") or 0)
    shares_after  = float(transaction.get("shares_after") or 0)
    if shares_bought > 0 and shares_after > shares_bought:
        shares_before = shares_after - shares_bought
        pct_increase = shares_bought / shares_before * 100
        if pct_increase >= 30:
            breakdown["holdings_increase_30pct"] = 10  # was 15; mixed signal
            score += 10
        elif pct_increase >= 15:
            breakdown["holdings_increase_15pct"] = 3   # was 5; essentially neutral
            score += 3
        elif pct_increase >= 5:
            breakdown["holdings_increase_5pct"] = 10   # was 5; +11.7%/+4.5% — strong
            score += 10

    # --- Timing: three mutually exclusive purchase-history factors ---
    #
    # Empirical finding round 2 (2026-05-25): factor-lift analysis confirms:
    # - first_purchase_12mo (no prior in 365d): -7.4%/-14.8% lift → removed (0)
    #   Insiders buying for the first time in a year are not more informed; they may
    #   simply be reacting to news or filling a position quota.
    # - prior_purchase_31_365d: signals WITHOUT first_purchase_12mo (n=21) averaged
    #   +8.2% at 60d and +15.7% at 90d — sustained conviction is the strongest timing signal.
    # - sequenced_buying_30d: increased to 10; rapid re-entry in 30d shows urgency.
    cutoff_365d = tx_date - timedelta(days=365)
    cutoff_30d  = tx_date - timedelta(days=30)

    prior_30d  = [p for p in prior_purchases
                  if cutoff_30d  <= (_parse_date(p.get("transaction_date")) or date.min) < tx_date]
    prior_365d = [p for p in prior_purchases
                  if cutoff_365d <= (_parse_date(p.get("transaction_date")) or date.min) < tx_date]

    if prior_30d:
        # Rapid sequential buyer — high urgency/conviction
        breakdown["sequenced_buying_30d"] = 10  # was 8
        score += 10
    elif prior_365d:
        # Sustained conviction: prior buy 31-364 days ago, coming back for more
        breakdown["prior_purchase_31_365d"] = 15  # was 12; +8.2%/+15.7% — strongest timing signal
        score += 15
    # else: first-time buyer (no prior in 365d) — no score added (was +3, confirmed negative)

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
    tight_cluster: bool = False,
) -> str:
    """
    Classify a signal given the max individual score and cluster information.

    score: max individual transaction score (0–100)
    cluster_flag: True if 3+ distinct insiders bought in the 14-day window
    participant_scores: list of individual eligible scores for each cluster
        participant. Used to compute the cluster-aggregate score.
    tight_cluster: True if 3+ insiders bought within a 5-day sub-window.

    CLUSTER_BUY qualification:
      - avg(participant_scores) >= 25 (was 28; reduced because removing first_purchase_12mo
        and value_100k_plus lowers typical individual scores by ~8 pts; keeps same
        relative filtering intensity)
      - AND (tight_cluster OR max individual score >= 35)
        (max threshold reduced from 45→35 for same reason — e.g. director+small=31,
        adding holdings_5pct gets to 41, but tight cluster alone should qualify)
      Loose clusters with weak individual scores are surfaced as WATCH.

    BUY threshold: 60 (unchanged; strong single-insider signals still reach 60+
      e.g. director+small+500k_plus+prior_purchase = 16+15+15+15 = 61).
    """
    if cluster_flag:
        if participant_scores:
            cluster_avg = int(sum(participant_scores) / len(participant_scores))
        else:
            cluster_avg = score  # fallback for callers that don't supply scores
        if cluster_avg >= 25:
            if tight_cluster or score >= 35:
                return "CLUSTER_BUY"
            return "WATCH"  # loose cluster with weak individual scores
        return "WATCH"  # very weak cluster: surface on dashboard, no alert
    if score >= 60:
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
