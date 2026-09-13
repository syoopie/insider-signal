"""
Signal scoring engine.

Two stages, and the split is the whole design.

**The filing decides eligibility.** A signal has to be a voluntary, non-trivial,
open-market purchase: transaction code P, not a 10b5-1 plan, at least $2,000,
not a filer error, and not a routine same-month repeat. These are hard
disqualifiers and they are what the research actually supports.

**The price decides the rank.** Among purchases that clear those gates, the one
thing that predicts out of sample is how far below its 52-week high the stock
sat on the day the insider bought. `src/signals/discount.py` holds the mapping;
`docs/findings.md` holds the evidence.

**Both stages matter, and the placebo control is why.** The same discount screen
run on stocks nobody bought, on the same dates with the same holding windows,
has a negative median. A deeply discounted stock an insider bought has a
positive one. The Form 4 is the gate and the discount is the ranker; neither
works alone.

Scores are a pure function of stored data. Nothing here reads a live price.
`pct_below_52wk_high` is fetched once at ingest by `src/market/context.py` and
stored on the transaction row for exactly that reason. Do not add a factor that
only one path can compute.
"""

import calendar
from datetime import date, timedelta
from typing import Optional, Sequence

from src.signals.constants import (
    BUY_SCORE,
    CLUSTER_MIN_AVG_SCORE,
    CLUSTER_MIN_MAX_SCORE,
    WATCH_SCORE,
)
from src.signals.discount import discount_score

# A single purchase above this is a filing error, not a signal.
MAX_PLAUSIBLE_PURCHASE = 1_000_000_000

TIMING_SEQUENCED = "sequenced_30d"
TIMING_PRIOR_YEAR = "prior_31_365d"
TIMING_FIRST = "first_12mo"
TIMING_UNVERIFIABLE = "unverifiable"


def _disqualified(reason: str) -> dict:
    return {"score": 0, "breakdown": {reason: "DISQUALIFIED"},
            "disqualified": True, "eligible": False}


def score_transaction(
    transaction: dict,
    owner: dict,
    prior_purchases: list,
    history_start: Optional[date] = None,
    discount_reference: Optional[Sequence[float]] = None,
) -> Optional[dict]:
    """
    Score one purchase against that insider's earlier ones. None if it is not a P.

    history_start is the earliest filing the data source can see. Without it,
    "no prior purchase in 365 days" is indistinguishable from "the data does not
    go back 365 days".

    Returns, for an eligible purchase:
        {"score": int, "breakdown": {"discount_rank": int}, "facts": {...},
         "disqualified": False, "eligible": True}
    and `{"price_context_missing": 0}` with `"unranked": True` when there is no
    52-week high to rank against.
    """
    if (transaction.get("transaction_code") or "").upper() != "P":
        return None

    if transaction.get("is_10b51"):
        return _disqualified("10b5_1_plan")

    # The `or 0` also disqualifies a P with no price, which is deliberate. EDGAR
    # lets a filer omit transactionPricePerShare and defer it to a footnote, and
    # in practice that never means an ordinary market buy: it is a private
    # placement, a trust-to-trust transfer, or an award miscoded as P. Do not
    # "fix" this by skipping the check when total_value is None.
    total_value = transaction.get("total_value") or 0
    if total_value < 2_000:
        return _disqualified("trivial_value")

    # EDGAR accepts filer errors: Dover filed a code-A award of 25,788 shares
    # with 25,788 in the price field. No individual buy in this universe is $1B.
    if total_value > MAX_PLAUSIBLE_PURCHASE:
        return _disqualified("implausible_value")

    # psycopg2 hands back a date object and the archive a string; both parse.
    tx_date = _parse_date(transaction.get("transaction_date")) or date.today()

    # Routine = bought in the same calendar month in >=2 of the preceding 3
    # years (Cohen, Malloy & Pomorski 2012). A stored flag wins; NULL falls back
    # to the purchases the caller can see.
    stored_is_routine = transaction.get("is_routine")
    if stored_is_routine is True:
        return _disqualified("routine_trader")
    if stored_is_routine is None and _looks_routine(tx_date, prior_purchases):
        return _disqualified("routine_trader")

    facts = purchase_facts(transaction, owner, prior_purchases, tx_date, history_start)
    score = discount_score(transaction.get("pct_below_52wk_high"), discount_reference)

    if score is None:
        # No price context means no rank. Scoring it at the median would put an
        # unmeasurable purchase into WATCH on no evidence, so it is surfaced at
        # zero and never alerted. `audit_data.py` counts these.
        return {"score": 0, "breakdown": {"price_context_missing": 0}, "facts": facts,
                "disqualified": False, "eligible": True, "unranked": True}

    return {"score": score, "breakdown": {"discount_rank": score}, "facts": facts,
            "disqualified": False, "eligible": True}


def _looks_routine(tx_date: date, prior_purchases: list) -> bool:
    oldest_available = min(
        (d for d in (_parse_date(p.get("transaction_date")) for p in prior_purchases) if d),
        default=None,
    )
    routine_years = 0
    for yr_back in (1, 2, 3):
        yr = tx_date.year - yr_back
        if oldest_available is None or oldest_available > date(yr, 12, 31):
            continue
        year_start = date(yr, tx_date.month, 1)
        year_end = date(yr, tx_date.month, calendar.monthrange(yr, tx_date.month)[1])
        if any(year_start <= (_parse_date(p.get("transaction_date")) or date.min) <= year_end
               for p in prior_purchases):
            routine_years += 1
    return routine_years >= 2


def purchase_facts(transaction: dict, owner: dict, prior_purchases: list,
                   tx_date: date, history_start: Optional[date]) -> dict:
    """
    What the filing says about the buyer. Recorded on the evidence, never scored.

    Each of these was a weighted factor once. Measured walk-forward, the whole
    table ranked purchases no better than chance, and adding it back as a
    tiebreak lowered the result.
    """
    shares = float(transaction.get("shares") or 0)
    after = float(transaction.get("shares_after") or 0)
    increase = shares / (after - shares) * 100 if shares > 0 and after > shares else None

    cutoff_365d = tx_date - timedelta(days=365)
    cutoff_30d = tx_date - timedelta(days=30)
    prior_dates = [d for d in (_parse_date(p.get("transaction_date")) for p in prior_purchases)
                   if d and d < tx_date]
    if any(d >= cutoff_30d for d in prior_dates):
        timing = TIMING_SEQUENCED
    elif any(d >= cutoff_365d for d in prior_dates):
        timing = TIMING_PRIOR_YEAR
    elif history_start is not None and cutoff_365d < history_start:
        timing = TIMING_UNVERIFIABLE
    else:
        timing = TIMING_FIRST

    return {
        "role_category": owner.get("role_category") or "other",
        "is_direct": transaction.get("is_direct", True) is not False,
        "holdings_increase_pct": increase,
        "timing": timing,
    }


def classify_signal(
    score: int,
    cluster_flag: bool,
    participant_scores: Optional[list] = None,
    tight_cluster: bool = False,
    cap_tier: str = "unknown",
) -> str:
    """
    Classify a signal given the max individual score and cluster information.

    Scores are percentiles of the 52-week discount, so BUY at 90 is the top
    decile and WATCH at 70 the top three.

    CLUSTER_BUY needs avg(participant_scores) >= 80, so the group as a whole was
    buying into weakness, AND (tight_cluster OR max score >= 85). Cluster size
    alone promotes nothing: inside the most discounted third the number of
    cluster buyers points the wrong way. A large-cap CLUSTER_BUY is a WATCH.
    """
    if cluster_flag:
        scores = participant_scores or [score]
        cluster_avg = int(sum(scores) / len(scores))
        if (cluster_avg >= CLUSTER_MIN_AVG_SCORE
                and (tight_cluster or score >= CLUSTER_MIN_MAX_SCORE)
                and cap_tier != "large"):
            return "CLUSTER_BUY"
        return "WATCH"
    if score >= BUY_SCORE:
        return "BUY"
    if score >= WATCH_SCORE:
        return "WATCH"
    return "LOW"


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
