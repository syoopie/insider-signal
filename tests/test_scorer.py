from datetime import date

from src.signals.scorer import classify_signal, score_transaction

DIRECTOR = {"role_category": "director"}
SMALL = {"cap_tier": "small"}
NO_MKT = {}


def score(tx, owner=DIRECTOR, company=SMALL, market=NO_MKT, priors=None):
    return score_transaction(tx, owner, company, market, priors or [])


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


def test_a_purchase_with_no_price_context_is_never_alerted(make_tx):
    """
    Unrankable is not average. Scoring a purchase we cannot measure at the
    median would place it in WATCH on no evidence.
    """
    result = score(make_tx(pct_below_52wk_high=None))
    assert result["score"] == 0
    assert result["eligible"] is True
    assert result["unranked"] is True
    assert result["breakdown"]["price_context_missing"] == 0


def test_the_filing_factors_are_recorded_but_score_nothing(make_tx):
    """
    Every weight in the old table was set by univariate lift on a sample the
    model had selected. Measured walk-forward the whole table returns +0.78pp at
    a permutation p of 0.27, and adding it back as a tiebreak drops the result
    from +11.13 to +7.62. It stays as description, not as points.
    """
    plain = score(make_tx(pct_below_52wk_high=30.0))
    loaded = score(make_tx(pct_below_52wk_high=30.0, is_direct=False,
                           shares=50, shares_after=1050),
                   owner={"role_category": "cfo"},
                   company={"cap_tier": "large"})
    assert plain["score"] == loaded["score"]
    assert loaded["breakdown"]["indirect_purchase"] == 0
    assert loaded["breakdown"]["role_cfo"] == 0
    assert loaded["breakdown"]["cap_large"] == 0
    assert loaded["breakdown"]["holdings_increase_5pct"] == 0


def test_timing_factors_are_still_mutually_exclusive(make_tx):
    ref = "2026-06-15"
    seq = score(make_tx(transaction_date=ref, pct_below_52wk_high=30.0),
                priors=[{"transaction_date": "2026-06-01"}])
    assert "sequenced_buying_30d" in seq["breakdown"]
    assert "prior_purchase_31_365d" not in seq["breakdown"]
    assert "first_purchase_12mo" not in seq["breakdown"]

    sustained = score(make_tx(transaction_date=ref, pct_below_52wk_high=30.0),
                      priors=[{"transaction_date": "2026-01-15"}])
    assert "prior_purchase_31_365d" in sustained["breakdown"]
    assert "sequenced_buying_30d" not in sustained["breakdown"]

    first = score(make_tx(transaction_date=ref, pct_below_52wk_high=30.0), priors=[])
    assert "first_purchase_12mo" in first["breakdown"]


def test_transaction_date_may_be_a_date_object(make_tx):
    """
    psycopg2 returns DATE columns as date objects, so every row scored from the
    database arrived this way. Slicing one raises TypeError, and the old parse
    swallowed it and fell back to date.today() — silently measuring every timing
    factor from today instead of from the trade.
    """
    as_obj = score(make_tx(transaction_date=date(2024, 9, 1), pct_below_52wk_high=30.0))
    as_str = score(make_tx(transaction_date="2024-09-01", pct_below_52wk_high=30.0))
    assert as_obj["breakdown"] == as_str["breakdown"]
    assert as_obj["score"] == as_str["score"]

    # A prior buy 40 days before the trade is sustained conviction, not a first
    # purchase. Reading the date as today would place it inside the 30d window.
    priors = [{"transaction_date": "2024-07-23"}]
    scored = score(make_tx(transaction_date=date(2024, 9, 1), pct_below_52wk_high=30.0),
                   priors=priors)
    assert "prior_purchase_31_365d" in scored["breakdown"]


def test_first_purchase_flag_needs_a_full_year_of_history(make_tx):
    """
    "No prior purchase in 365 days" is only meaningful when the database
    actually covers those 365 days. It did not for the first year of ingest, so
    87% of signals before 2025-04-03 carried the flag against 32% after, which
    is a fact about the ingest start date rather than about insiders. The flag
    no longer moves the score, but it still has to describe the filing honestly.
    """
    tx = make_tx(transaction_date="2026-06-15", pct_below_52wk_high=30.0)

    covered = score_transaction(tx, {"role_category": "director"}, {}, {}, [],
                                history_start=date(2024, 1, 1))
    assert "first_purchase_12mo" in covered["breakdown"]

    cold = score_transaction(tx, {"role_category": "director"}, {}, {}, [],
                             history_start=date(2026, 1, 1))
    assert "first_purchase_12mo" not in cold["breakdown"]
    assert "first_purchase_unverifiable" in cold["breakdown"]
    assert cold["score"] == covered["score"]

    assert "first_purchase_12mo" in score_transaction(
        tx, {"role_category": "director"}, {}, {}, [],
        history_start=None)["breakdown"]


def test_live_price_data_cannot_change_the_score(make_tx):
    """
    The score must depend only on stored filing data.

    The 52-week-low factor broke this: it fired in live ingest, which has a
    Yahoo quote, and never in the historical backfill, which does not. The same
    purchase scored up to 12 points apart depending on which path saw it, and a
    --force backfill silently reclassified signals the live path had written.
    """
    at_low = score(make_tx(price_per_share=10, pct_below_52wk_high=30.0),
                   market={"price_52wk_low": 10})
    no_market = score(make_tx(price_per_share=10, pct_below_52wk_high=30.0), market={})
    assert at_low["score"] == no_market["score"]
    assert not any("52wk_low" in f for f in at_low["breakdown"])


def test_the_score_stays_inside_zero_to_one_hundred(make_tx):
    for value in (-5.0, 0.0, 50.0, 99.15, 250.0):
        assert 0 <= score(make_tx(pct_below_52wk_high=value))["score"] <= 100


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


def test_purchase_with_no_price_is_disqualified(make_tx):
    """
    EDGAR lets a filer defer transactionPricePerShare to a footnote, which
    leaves total_value NULL. Every observed case is a private placement, a
    trust-to-trust transfer, or an award miscoded as P — never a market buy.
    """
    result = score(make_tx(price_per_share=None, total_value=None))
    assert result["disqualified"] is True
    assert result["breakdown"] == {"trivial_value": "DISQUALIFIED"}
