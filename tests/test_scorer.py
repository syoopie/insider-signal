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


# ── Additive factors ────────────────────────────────────────────────────────

def test_base_director_small_first_buy(make_tx):
    # director +16, small +15, first_purchase_12mo -10
    r = score(make_tx())
    assert r["score"] == 21
    assert r["breakdown"]["role_director"] == 16
    assert r["breakdown"]["cap_small"] == 15
    assert r["breakdown"]["first_purchase_12mo"] == -10


def test_indirect_purchase_penalty(make_tx):
    r = score(make_tx(is_direct=False))
    assert r["breakdown"]["indirect_purchase"] == -15
    assert r["score"] == 21 - 15


def test_role_scores(make_tx):
    assert score(make_tx(), owner={"role_category": "cfo"})["breakdown"]["role_cfo"] == 15
    assert score(make_tx(), owner={"role_category": "ceo"})["breakdown"]["role_ceo"] == -5
    assert score(make_tx(), owner={"role_category": "coo"})["breakdown"]["role_coo"] == 15
    assert score(make_tx(), owner={"role_category": "officer"})["breakdown"]["role_officer"] == 12
    # chairman and other score 0 and are not written into the breakdown as points
    assert score(make_tx(), owner={"role_category": "chairman"})["breakdown"]["role_chairman"] == 0


def test_cap_tier_scores(make_tx):
    assert score(make_tx(), company={"cap_tier": "small"})["breakdown"]["cap_small"] == 15
    assert score(make_tx(), company={"cap_tier": "unknown"})["breakdown"]["cap_unknown"] == 5
    mid = score(make_tx(), company={"cap_tier": "mid"})
    assert "cap_mid" not in mid["breakdown"]  # zero-point tiers are omitted
    large = score(make_tx(), company={"cap_tier": "large"})
    assert "cap_large" not in large["breakdown"]


def test_holdings_increase_5pct(make_tx):
    at_5 = score(make_tx(shares=50, shares_after=1050))  # +5.0%
    assert at_5["breakdown"]["holdings_increase_5pct"] == 15
    under_5 = score(make_tx(shares=40, shares_after=1040))  # +4.0%
    assert "holdings_increase_5pct" not in under_5["breakdown"]


def test_timing_factors_are_mutually_exclusive(make_tx):
    ref = "2026-06-15"
    seq = score(make_tx(transaction_date=ref), priors=[{"transaction_date": "2026-06-01"}])
    assert seq["breakdown"].get("sequenced_buying_30d") == 10
    assert "prior_purchase_31_365d" not in seq["breakdown"]
    assert "first_purchase_12mo" not in seq["breakdown"]

    sustained = score(make_tx(transaction_date=ref), priors=[{"transaction_date": "2026-01-15"}])
    assert sustained["breakdown"].get("prior_purchase_31_365d") == 15
    assert "sequenced_buying_30d" not in sustained["breakdown"]

    first = score(make_tx(transaction_date=ref), priors=[])
    assert first["breakdown"].get("first_purchase_12mo") == -10


def test_near_52wk_low_tiers(make_tx):
    at_low = score(make_tx(price_per_share=10), market={"price_52wk_low": 10})
    assert at_low["breakdown"]["near_52wk_low_5pct"] == 12
    mid_band = score(make_tx(price_per_share=10.6), market={"price_52wk_low": 10})
    assert mid_band["breakdown"]["near_52wk_low_10pct"] == 7
    far = score(make_tx(price_per_share=12), market={"price_52wk_low": 10})
    assert "near_52wk_low_5pct" not in far["breakdown"]
    assert "near_52wk_low_10pct" not in far["breakdown"]


def test_score_is_capped_at_100(make_tx):
    r = score(
        make_tx(shares=50, shares_after=1050, price_per_share=10),
        owner={"role_category": "director"},
        company={"cap_tier": "small"},
        market={"price_52wk_low": 10},
        priors=[{"transaction_date": (date.today().replace(year=date.today().year - 1)).isoformat()}],
    )
    assert r["score"] <= 100


# ── classify_signal ─────────────────────────────────────────────────────────

def test_classify_non_cluster_thresholds():
    assert classify_signal(60, False) == "BUY"
    assert classify_signal(59, False) == "WATCH"
    assert classify_signal(45, False) == "WATCH"
    assert classify_signal(44, False) == "LOW"


def test_classify_cluster_buy_requires_avg_and_tight_or_maxscore():
    # avg 30, max 30 -> CLUSTER_BUY
    assert classify_signal(30, True, [30, 30, 30], tight_cluster=False) == "CLUSTER_BUY"
    # avg 25 >= 22 but loose and max 25 < 30 -> WATCH
    assert classify_signal(25, True, [25, 25, 25], tight_cluster=False) == "WATCH"
    # avg 25, loose, but max score 30 -> CLUSTER_BUY
    assert classify_signal(30, True, [25, 25, 35], tight_cluster=False) == "CLUSTER_BUY"
    # avg 25, tight -> CLUSTER_BUY
    assert classify_signal(25, True, [25, 25, 25], tight_cluster=True) == "CLUSTER_BUY"
    # avg below 22 -> WATCH regardless
    assert classify_signal(21, True, [10, 20, 21], tight_cluster=True) == "WATCH"
