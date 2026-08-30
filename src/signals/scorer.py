"""
Signal scoring engine.

Two stages, and the split is the whole design.

**The filing decides eligibility.** A signal has to be a voluntary, non-trivial,
open-market purchase: transaction code P, not a 10b5-1 plan, at least $2,000,
not a filer error, and not a routine same-month repeat. These are hard
disqualifiers and they are what the research actually supports.

**The price decides the rank.** Among purchases that clear those gates, the one
thing that predicts out of sample is how far below its 52-week high the stock
sat on the day the insider bought. `src/signals/discount.py` holds the mapping
and the evidence; `docs/scoring-improvement-plan.md` section 7b holds the full
account. Measured walk-forward over 18 months and 6,690 purchases, the top
decile returns +11.13pp above its own month and volatility quintile with a
median of +7.39pp, above all 5,000 random rankings on both.

The additive factor table this replaced measured +0.78pp with a permutation p
of 0.27. Its factors are still emitted in the breakdown at zero points, because
they describe the filing usefully even though they do not rank it.

**Both stages matter, and the placebo control is why.** The same discount screen
run on stocks nobody bought, on the same dates with the same holding windows,
returns +5.55pp on the mean and −1.30pp on the median. A deeply discounted stock
in general is a lottery ticket whose typical outcome is a loss. A deeply
discounted stock an insider bought has a positive median and a 57.7% hit rate.
The Form 4 is the gate and the discount is the ranker; neither works alone.

Scores are a pure function of stored data. Nothing here reads a live price, so
the live ingest path and the historical backfill always agree on a given
transaction. `pct_below_52wk_high` is fetched once at ingest by
`src/market/context.py` and stored on the transaction row for exactly that
reason. Do not add a factor that only one path can compute.
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
from src.signals.discount import discount_score


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

# A single insider purchase above this is a filing error, not a signal.
MAX_PLAUSIBLE_PURCHASE = 1_000_000_000


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

    # Upper bound on a single insider's open-market purchase. EDGAR accepts
    # filer errors: Dover filed a code-A award of 25,788 shares with 25,788 in
    # the price field, and Table I debt filings put the principal amount in both
    # (see parser). No individual buy in an S&P 500 + Russell 2000 universe is
    # $1B, so anything above it is a data error, not a conviction signal.
    if total_value > MAX_PLAUSIBLE_PURCHASE:
        return {"score": 0, "breakdown": {"implausible_value": "DISQUALIFIED"}, "disqualified": True, "eligible": False}

    # Hard disqualifier: routine trader (CMP 2012)
    # Routine = bought in the same calendar month in ≥2 of the preceding 3 years.
    # If the transaction row already has is_routine pre-computed (stored at ingest
    # time), use it directly — avoids dependence on pruned historical data.
    # psycopg2 hands back a date object, not a string. Slicing one raises
    # TypeError, so this used to fall through to date.today() for every row that
    # came out of the database — which is every real row. Every timing factor
    # and the routine month check were then measured from today rather than from
    # the trade, so a 2024 purchase had its "prior 365 days" evaluated against
    # 2025-2026. _parse_date accepts both forms.
    tx_date = _parse_date(transaction.get("transaction_date")) or date.today()

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

    # --- The ranking ---
    # Everything above this line decides whether a purchase is a real, voluntary,
    # non-trivial open-market buy. Everything below decides how good it is, and
    # the answer measured out of sample is: how far below its 52-week high the
    # stock sat on the day the insider bought, and nothing else.
    #
    # `pct_below_52wk_high` is stored on the transaction at ingest by
    # src/market/context.py. It is never computed here, so this stays a pure
    # function of stored data and the live path and the backfill cannot diverge.
    score = discount_score(transaction.get("pct_below_52wk_high"))

    # The former factor table is kept as context rather than as points. Every
    # weight in it was set by univariate lift on a sample the model itself had
    # selected, and measured on the walk-forward ruler the whole thing returns
    # +0.78pp of selection alpha at a permutation p of 0.27. Three of its four
    # load-bearing factors are indistinguishable from zero and cap_small carries
    # +15 points with the opposite sign to its measured effect.
    #
    # They stay in the breakdown at zero so /how-it-works can still show what
    # the filing said and so a later round has the columns to work with. They do
    # not move the score, and adding them back as a tiebreak was measured: it
    # drops the result from +11.13 to +7.62 and fails the t>=2 bar.
    breakdown = _descriptive_factors(transaction, owner, company, market_data,
                                     prior_purchases, tx_date, history_start)

    if score is None:
        # No price context means no rank. Scoring it at the median would put an
        # unmeasurable purchase into WATCH on no evidence, so it is surfaced at
        # zero and never alerted. `audit_data.py` counts these, and a rise in
        # the count is a price-fetch problem, not a market one. The descriptive
        # factors are still recorded, so the dashboard can show what the filing
        # said even when the rank is unavailable.
        return {
            "score": 0,
            "breakdown": {"price_context_missing": 0, **breakdown},
            "disqualified": False,
            "eligible": True,
            "unranked": True,
        }

    return {
        "score": score,
        "breakdown": {"discount_rank": score, **breakdown},
        "disqualified": False,
        "eligible": True,
    }


def _descriptive_factors(transaction, owner, company, market_data,
                         prior_purchases, tx_date, history_start) -> dict:
    """
    What the filing says, recorded at zero points. Not part of the score.

    Keeping the names and the mutual exclusivity intact means the dashboard, the
    evidence blob and `analyze_factors.py` keep working, and a future round can
    ask whether any of them earn a weight without first having to re-derive them.
    """
    out = {}

    if transaction.get("is_direct", True) is False:
        out["indirect_purchase"] = 0

    out[f"role_{owner.get('role_category', 'other')}"] = 0

    cap_tier = company.get("cap_tier") or market_data.get("cap_tier", "unknown")
    out[f"cap_{cap_tier}"] = 0

    shares_bought = float(transaction.get("shares") or 0)
    shares_after = float(transaction.get("shares_after") or 0)
    if shares_bought > 0 and shares_after > shares_bought:
        if shares_bought / (shares_after - shares_bought) * 100 >= 5:
            out["holdings_increase_5pct"] = 0

    cutoff_365d = tx_date - timedelta(days=365)
    cutoff_30d = tx_date - timedelta(days=30)
    prior_30d = [p for p in prior_purchases
                 if cutoff_30d <= (_parse_date(p.get("transaction_date")) or date.min) < tx_date]
    prior_365d = [p for p in prior_purchases
                  if cutoff_365d <= (_parse_date(p.get("transaction_date")) or date.min) < tx_date]

    if prior_30d:
        out["sequenced_buying_30d"] = 0
    elif prior_365d:
        out["prior_purchase_31_365d"] = 0
    elif history_start is not None and cutoff_365d < history_start:
        out["first_purchase_unverifiable"] = 0
    else:
        out["first_purchase_12mo"] = 0
    return out


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

    Scores are percentiles of the 52-week discount, so BUY at 90 is the top
    decile and WATCH at 70 the top three. The top decile is where the entire
    measured effect sits.

    CLUSTER_BUY qualification:
      - avg(participant_scores) >= 80, so the group as a whole was buying into
        real weakness rather than one member of it
      - AND (tight_cluster OR max participant score >= 85)
      A cluster that does not clear the discount bar is surfaced as WATCH and
      never alerted. It used to be promoted on cluster size alone, which the
      data does not support: inside the most discounted third the number of
      cluster buyers points the wrong way at -4.53 with t=-1.85.
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
