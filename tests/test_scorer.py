from datetime import date

from src.signals.scorer import (
    TIMING_FIRST,
    TIMING_PRIOR_YEAR,
    TIMING_SEQUENCED,
    TIMING_UNVERIFIABLE,
    classify_signal,
    score_transaction,
)

DIRECTOR = {"role_category": "director"}


def score(tx, owner=DIRECTOR, priors=None, history_start=None):
    return score_transaction(tx, owner, priors or [], history_start=history_start)


# ── Eligibility / disqualifiers ──────────────────────────────────────────────

def test_non_purchase_returns_none(make_tx):
    assert score(make_tx(transaction_code="S")) is None
    assert score(make_tx(transaction_code="A")) is None


def test_10b51_is_disqualified(make_tx):
    r = score(make_tx(is_10b51=True))
    assert r["disqualified"] and not r["eligible"]
    assert r["breakdown"] == {"10b5_1_plan": "DISQUALIFIED"}


def test_trivial_value_is_disqualified(make_tx):
    r = score(make_tx(total_value=1999))
    assert r["disqualified"]
    assert r["breakdown"] == {"trivial_value": "DISQUALIFIED"}
    assert score(make_tx(total_value=2000))["eligible"] is True


def test_stored_routine_flag_disqualifies(make_tx):
    r = score(make_tx(is_routine=True))
    assert r["disqualified"]
    assert r["breakdown"] == {"routine_trader": "DISQUALIFIED"}


def test_stored_routine_false_overrides_routine_looking_history(make_tx):
    priors = [{"transaction_date": "2025-06-10"}, {"transaction_date": "2024-06-10"}]
    r = score(make_tx(transaction_date="2026-06-15", is_routine=False), priors=priors)
    assert r["eligible"] is True


def test_computed_routine_disqualifies_when_flag_is_null(make_tx):
    priors = [{"transaction_date": "2025-06-10"}, {"transaction_date": "2024-06-10"}]
    r = score(make_tx(transaction_date="2026-06-15", is_routine=None), priors=priors)
    assert r["disqualified"]
    assert r["breakdown"] == {"routine_trader": "DISQUALIFIED"}


def test_purchase_with_no_price_is_disqualified(make_tx):
    """
    EDGAR lets a filer defer transactionPricePerShare to a footnote, which
    leaves total_value NULL. Every observed case is a private placement, a
    trust-to-trust transfer, or an award miscoded as P — never a market buy.
    """
    result = score(make_tx(price_per_share=None, total_value=None))
    assert result["disqualified"] is True
    assert result["breakdown"] == {"trivial_value": "DISQUALIFIED"}


# ── The ranking ─────────────────────────────────────────────────────────────

def test_the_score_is_the_discount_percentile(make_tx):
    """
    24.87% below the 52-week high is the median of the research sample, so it
    scores 50. 60.12% is the ninetieth percentile, so it scores 90 and is a BUY.
    """
    assert score(make_tx(pct_below_52wk_high=24.87))["score"] == 50
    assert score(make_tx(pct_below_52wk_high=60.12))["score"] == 90
    assert score(make_tx(pct_below_52wk_high=0.0))["score"] == 0


def test_the_score_is_monotone_in_the_discount(make_tx):
    """
    Depth still orders the tail. Inside the most discounted third, more discount
    is worth +10.6pp with t=+2.22, so the score must not flatten into a flag.
    """
    scores = [score(make_tx(pct_below_52wk_high=v))["score"]
              for v in (0, 5, 12, 25, 40, 55, 70, 95)]
    assert scores == sorted(scores)
    assert len(set(scores)) > 5


def test_the_score_stays_inside_zero_to_one_hundred(make_tx):
    for value in (-5.0, 0.0, 50.0, 99.15, 250.0):
        assert 0 <= score(make_tx(pct_below_52wk_high=value))["score"] <= 100


def test_the_breakdown_holds_only_what_moved_the_score(make_tx):
    result = score(make_tx(pct_below_52wk_high=60.12))
    assert result["breakdown"] == {"discount_rank": 90}


def test_a_purchase_with_no_price_context_is_never_alerted(make_tx):
    """
    Unrankable is not average. Scoring a purchase we cannot measure at the
    median would place it in WATCH on no evidence.
    """
    result = score(make_tx(pct_below_52wk_high=None))
    assert result["score"] == 0
    assert result["eligible"] is True
    assert result["unranked"] is True
    assert result["breakdown"] == {"price_context_missing": 0}


# ── What the filing says, recorded and not scored ───────────────────────────

def test_the_filing_facts_are_recorded_but_score_nothing(make_tx):
    """
    Every weight in the old factor table was set by univariate lift on a sample
    the model had selected. Measured walk-forward it ranked no better than
    chance, so role, ownership form and position size describe the purchase and
    move nothing.
    """
    plain = score(make_tx(pct_below_52wk_high=30.0))
    loaded = score(make_tx(pct_below_52wk_high=30.0, is_direct=False,
                           shares=50, shares_after=1050),
                   owner={"role_category": "cfo"})
    assert plain["score"] == loaded["score"]
    assert plain["breakdown"] == loaded["breakdown"]
    assert loaded["facts"]["role_category"] == "cfo"
    assert loaded["facts"]["is_direct"] is False
    assert loaded["facts"]["holdings_increase_pct"] == 5.0
    assert plain["facts"]["is_direct"] is True
    assert plain["facts"]["holdings_increase_pct"] is None


def test_timing_is_one_of_four_mutually_exclusive_values(make_tx):
    ref = "2026-06-15"
    tx = make_tx(transaction_date=ref, pct_below_52wk_high=30.0)
    assert score(tx, priors=[{"transaction_date": "2026-06-01"}])["facts"]["timing"] == TIMING_SEQUENCED
    assert score(tx, priors=[{"transaction_date": "2026-01-15"}])["facts"]["timing"] == TIMING_PRIOR_YEAR
    assert score(tx, priors=[])["facts"]["timing"] == TIMING_FIRST


def test_transaction_date_may_be_a_date_object(make_tx):
    """
    psycopg2 returns DATE columns as date objects, so every row scored from the
    database arrives this way. Slicing one raises TypeError, and an old parse
    swallowed it and fell back to date.today(), measuring every timing fact from
    today instead of from the trade.
    """
    as_obj = score(make_tx(transaction_date=date(2024, 9, 1), pct_below_52wk_high=30.0))
    as_str = score(make_tx(transaction_date="2024-09-01", pct_below_52wk_high=30.0))
    assert as_obj == as_str

    # A prior buy 40 days before the trade is inside the year, not inside 30 days.
    scored = score(make_tx(transaction_date=date(2024, 9, 1), pct_below_52wk_high=30.0),
                   priors=[{"transaction_date": "2024-07-23"}])
    assert scored["facts"]["timing"] == TIMING_PRIOR_YEAR


def test_first_purchase_needs_a_full_year_of_history(make_tx):
    """
    "No prior purchase in 365 days" is only meaningful when the data actually
    covers those 365 days. It did not for the first year of ingest, so the flag
    fired on 87% of early signals against 32% later, a fact about the ingest
    start date rather than about insiders.
    """
    tx = make_tx(transaction_date="2026-06-15", pct_below_52wk_high=30.0)
    covered = score(tx, history_start=date(2024, 1, 1))
    cold = score(tx, history_start=date(2026, 1, 1))
    assert covered["facts"]["timing"] == TIMING_FIRST
    assert cold["facts"]["timing"] == TIMING_UNVERIFIABLE
    assert cold["score"] == covered["score"]
    assert score(tx, history_start=None)["facts"]["timing"] == TIMING_FIRST


# ── classify_signal ─────────────────────────────────────────────────────────

def test_classify_non_cluster_thresholds():
    assert classify_signal(90, False) == "BUY"
    assert classify_signal(89, False) == "WATCH"
    assert classify_signal(70, False) == "WATCH"
    assert classify_signal(69, False) == "LOW"


def test_classify_cluster_buy_requires_the_group_to_be_buying_weakness():
    assert classify_signal(85, True, [80, 85, 90], tight_cluster=False) == "CLUSTER_BUY"
    # Average clears 80 but the window is loose and no participant reaches 85.
    assert classify_signal(82, True, [80, 82, 82], tight_cluster=False) == "WATCH"
    assert classify_signal(82, True, [80, 82, 82], tight_cluster=True) == "CLUSTER_BUY"


def test_a_cluster_in_a_stock_near_its_high_is_not_an_alert():
    """
    Cluster size alone used to promote a signal. Inside the most discounted
    third the number of cluster buyers points the wrong way at -4.53, t=-1.85,
    so three insiders buying a stock at its 52-week high is a WATCH.
    """
    assert classify_signal(30, True, [28, 30, 32], tight_cluster=True) == "WATCH"
    assert classify_signal(30, True, [28, 30, 32], tight_cluster=False) == "WATCH"


def test_a_large_cap_cluster_is_a_watch():
    assert classify_signal(95, True, [90, 92, 95], True, cap_tier="small") == "CLUSTER_BUY"
    assert classify_signal(95, True, [90, 92, 95], True, cap_tier="large") == "WATCH"
    assert classify_signal(95, False, cap_tier="large") == "BUY"
