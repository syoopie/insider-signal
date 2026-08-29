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
  Four mutually exclusive purchase-history factors — exactly one fires per signal:
  - sequenced_buying_30d        (+10): prior buy within 30 days (rapid sequence)
  - prior_purchase_31_365d      (+15): prior buy in 31-364 days (sustained conviction)
  - first_purchase_12mo         (-10): no prior buy in 365 days, and the database
                                       covers that whole year so the absence is real
  - first_purchase_unverifiable   (0): no prior buy, but the database does not reach
                                       back a full year before the trade

Scores are a pure function of stored data. Nothing here reads a live price, so
the live ingest path and the historical backfill always agree on a given
transaction. Do not add a factor that only one of them can compute.
"""

import calendar
from datetime import date, timedelta
from typing import Optional

from src.signals.constants import (
    BUY_SCORE,
    CLUSTER_MIN_AVG_SCORE,
    CLUSTER_MIN_MAX_SCORE,
    WATCH_SCORE,
)


# Role → base score delta.
# Round 4 (2026-05-25): factor-lift analysis on 300/251 signals across 60d/90d.
# role_ceo: -17.3%/-13.4% lift → moderate penalty (-5) to suppress CEO-only signals.
# role_chairman: -4.4%/-10.1% → keep at 0 (n=2, too small to penalize confidently).
# role_officer: +20.8%/−10.5% → mixed; keep at 12.
# role_other: -24.4%/-11.9% → confirmed noise, keep at 0 (n=5, noisy).
ROLE_SCORES = {
    "cfo":       15,  # -0.2%/+6.8% — good at 90d, keep
    "director":  16,  # -2.4%/+0.9% — slight positive, keep
    "coo":       15,  # -1.3%/+6.4% — good at 90d, keep
    "chairman":   0,  # -4.4%/-10.1% — negative but n=2; keep at 0
    "officer":   12,  # +20.8%/-10.5% — mixed; keep
    "ceo":       -5,  # -17.3%/-13.4% — confirmed negative; moderate penalty
    "other":      0,  # -24.4%/-11.9% — noise; n=5 too small to penalize further
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
    history_start: Optional[date] = None,
) -> Optional[dict]:
    """
    Score a single transaction. Returns None if ineligible (not a P, is 10b5-1, etc.).

    history_start is the earliest transaction date the database can see. Without
    it, "no prior purchase in 365 days" is indistinguishable from "the database
    does not go back 365 days", and every trade in the first year of coverage
    collects the first_purchase_12mo penalty. Pass it and the penalty is withheld
    when the lookback window is not fully observable.

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
    #
    # The `or 0` also disqualifies a P with no price, which is deliberate. EDGAR
    # lets a filer omit transactionPricePerShare and defer it to a footnote, and
    # in practice that never means an ordinary market buy: it is a private
    # placement, a trust-to-trust transfer, or an award miscoded as P. Treating a
    # missing price as $0 keeps those out. Do not "fix" this by skipping the
    # check when total_value is None.
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
            year_end   = date(yr, tx_month, calendar.monthrange(yr, tx_month)[1])
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
    role_pts = ROLE_SCORES.get(role, 0)
    breakdown[f"role_{role}"] = role_pts
    score += role_pts

    # --- Market cap tier ---
    cap_tier = company.get("cap_tier") or market_data.get("cap_tier", "unknown")
    cap_pts = CAP_SCORES.get(cap_tier, 5)
    if cap_pts > 0:
        breakdown[f"cap_{cap_tier}"] = cap_pts
    score += cap_pts

    # Transaction value removed entirely (round 4): value_500k_plus had -4.7%/-6.5% lift
    # at 60d/90d despite weight +15 — the largest single scoring error. Large dollar
    # purchases correlate with negative outcomes, likely insiders averaging down in
    # declining stocks. Dollar size doesn't predict alpha; quality factors do.

    # --- Purchase as % of prior holdings (Pficdn et al.) ---
    # holdings_5pct: +9.2%/+9.3% lift at 60d/90d — best non-role factor. Increased to 15.
    #   Fires for insiders meaningfully adding to an existing position.
    # holdings_30pct/15pct: confirmed negative at both horizons (removed in round 3).
    shares_bought = float(transaction.get("shares") or 0)
    shares_after  = float(transaction.get("shares_after") or 0)
    if shares_bought > 0 and shares_after > shares_bought:
        shares_before = shares_after - shares_bought
        pct_increase = shares_bought / shares_before * 100
        if pct_increase >= 5:
            breakdown["holdings_increase_5pct"] = 15   # +9.2%/+9.3% — best factor; raised from 10
            score += 15

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
        breakdown["sequenced_buying_30d"] = 10
        score += 10
    elif prior_365d:
        # Sustained conviction: prior buy 31-364 days ago (+2.4%/60d).
        breakdown["prior_purchase_31_365d"] = 15
        score += 15
    elif history_start is not None and cutoff_365d < history_start:
        # The year before this trade is outside what the database stores, so the
        # absence of a prior purchase is not evidence of one. Charging the
        # penalty here made it fire on 87% of pre-2025-04 signals against 32%
        # after, which is a property of the ingest start date, not the insider.
        breakdown["first_purchase_unverifiable"] = 0
    else:
        # First-time buyer in 12mo: -4.2%/-1.7% lift (n=174/156, round 4). Strengthen penalty.
        breakdown["first_purchase_12mo"] = -10
        score += -10

    # The 52-week-low factor was removed. It could only ever fire in the live
    # path, because the historical backfill has no price history to compare
    # against, so the same purchase scored up to 12 points higher depending on
    # which entry point happened to see it. It was also comparing the purchase
    # price against *today's* 52-week low rather than the low as of the trade.
    # Reinstating it means storing the low as of the transaction date at ingest.

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
      - avg(participant_scores) >= 22 (lowered from 25; round 4 removes value_500k_plus
        (-15 pts) which compresses individual scores; director+small = 31, but many
        valid cluster participants may score 22-30 without a large holdings increase)
      - AND (tight_cluster OR max individual score >= 30)
        (lowered from 35 for same reason — director+small=31 already marginal)
      Loose clusters with weak individual scores are surfaced as WATCH.

    BUY threshold: 60.
      Achievable with: dir(16)+small(15)+holdings5pct(15)+prior_purchase(15) = 61.
      Or: cfo(15)+small(15)+holdings5pct(15)+prior_purchase(15) = 60.
      Effectively requires 3-4 strong positive factors — no cheap path via dollar-value.
    """
    if cluster_flag:
        if participant_scores:
            cluster_avg = int(sum(participant_scores) / len(participant_scores))
        else:
            cluster_avg = score  # fallback for callers that don't supply scores
        if cluster_avg >= CLUSTER_MIN_AVG_SCORE:
            if tight_cluster or score >= CLUSTER_MIN_MAX_SCORE:
                return "CLUSTER_BUY"
            return "WATCH"  # loose cluster with weak individual scores
        return "WATCH"  # very weak cluster: surface on dashboard, no alert
    if score >= BUY_SCORE:
        return "BUY"
    if score >= WATCH_SCORE:
        return "WATCH"
    return "LOW"


def cluster_size_bonus(insider_count: int) -> tuple:
    """
    Disabled in round 5: cluster_size_5plus had -1.5%/-0.3% lift — not discriminating.
    Returns (0, "") for all inputs.
    """
    return 0, ""


def filing_lag_bonus(min_lag_days: int) -> tuple:
    """
    Disabled in round 4: fast_filing_0_1d had -2.5%/-1.1% lift while firing on 61% of
    signals — too broad to discriminate. Returns (0, "") for all inputs.
    """
    return 0, ""


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None
