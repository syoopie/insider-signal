"""
`build_signal` is the one place a signal is assembled, for the daily ingest and
the backfill alike. These pin the point-in-time rules the two used to disagree on.
"""
from datetime import date

from src.signals.batch import build_signal
from src.signals.scorer import TIMING_FIRST, TIMING_SEQUENCED

# 0.0 to 99.5 in steps of 0.5, so a discount of v scores v.
REFERENCE = sorted(float(i) / 2 for i in range(200))


def _reference(_filed):
    return REFERENCE


def _row(name, traded, filed, pct, *, value=30_000.0, price=10.0, cap="small", **extra):
    row = {
        "ticker": "ACME", "company_name": "Acme Corp", "cap_tier": cap,
        "insider_name": name, "insider_role": "Director", "role_category": "director",
        "transaction_code": "P", "transaction_date": traded, "filed_date": filed,
        "is_10b51": False, "is_direct": True, "is_routine": False,
        "shares": value / price, "shares_after": value / price * 4,
        "price_per_share": price, "total_value": value, "pct_below_52wk_high": pct,
    }
    row.update(extra)
    return row


def _newest_first(rows):
    return sorted(rows, key=lambda r: r["transaction_date"], reverse=True)


def test_the_signal_is_dated_by_its_trade_and_keyed_by_its_newest_filing():
    rows = _newest_first([
        _row("Early Filer", date(2026, 3, 2), date(2026, 3, 4), 95.0),
        _row("Late Filer", date(2026, 3, 1), date(2026, 3, 6), 20.0),
    ])
    signal = build_signal("ACME", rows, date(2026, 3, 6), None, _reference)
    assert signal.signal_date == date(2026, 3, 2)
    assert signal.filed_date == date(2026, 3, 6)
    assert signal.evidence["filed_date"] == "2026-03-06"
    assert signal.score == 95
    assert signal.signal_type == "BUY"
    assert signal.score_breakdown == {"discount_rank": 95}


def test_a_purchase_filed_after_the_work_item_is_invisible_to_it():
    rows = _newest_first([
        _row("Known", date(2026, 3, 2), date(2026, 3, 4), 80.0),
        _row("Not Yet Public", date(2026, 3, 3), date(2026, 3, 9), 99.0),
    ])
    signal = build_signal("ACME", rows, date(2026, 3, 6), None, _reference)
    assert signal.score == 80
    assert {i["name"] for i in signal.evidence["insiders"]} == {"Known"}


def test_a_cluster_cannot_form_before_its_members_filed():
    rows = _newest_first([
        _row("A", date(2026, 3, 2), date(2026, 3, 3), 95.0, price=10.0),
        _row("B", date(2026, 3, 3), date(2026, 3, 4), 92.0, price=10.5),
        _row("C", date(2026, 3, 4), date(2026, 3, 9), 90.0, price=11.0),
    ])
    before = build_signal("ACME", rows, date(2026, 3, 4), None, _reference)
    after = build_signal("ACME", rows, date(2026, 3, 9), None, _reference)
    assert before.cluster_flag is False
    assert after.cluster_flag is True
    assert after.signal_type == "CLUSTER_BUY"


def test_a_large_cap_cluster_is_stored_as_a_watch():
    rows = _newest_first([
        _row("A", date(2026, 3, 2), date(2026, 3, 3), 95.0, price=10.0, cap="large"),
        _row("B", date(2026, 3, 3), date(2026, 3, 4), 92.0, price=10.5, cap="large"),
        _row("C", date(2026, 3, 4), date(2026, 3, 5), 90.0, price=11.0, cap="large"),
    ])
    signal = build_signal("ACME", rows, date(2026, 3, 5), None, _reference)
    assert signal.cluster_flag is True
    assert signal.signal_type == "WATCH"


def test_only_purchases_filed_before_the_window_count_as_priors():
    rows = _newest_first([
        _row("Repeat", date(2026, 3, 5), date(2026, 3, 6), 60.0),
        _row("Repeat", date(2026, 3, 1), date(2026, 3, 2), 50.0),
    ])
    inside = build_signal("ACME", rows, date(2026, 3, 6), None, _reference)
    assert inside.evidence["insiders"][0]["timing"] == TIMING_FIRST

    earlier = _row("Repeat", date(2026, 2, 10), date(2026, 2, 12), 40.0)
    with_prior = build_signal("ACME", rows + [earlier], date(2026, 3, 6), None, _reference)
    assert with_prior.evidence["insiders"][0]["timing"] == TIMING_SEQUENCED


def test_a_weak_window_is_returned_as_low_and_an_ineligible_one_as_none():
    low = build_signal("ACME", [_row("A", date(2026, 3, 2), date(2026, 3, 3), 10.0)],
                       date(2026, 3, 3), None, _reference)
    assert low.signal_type == "LOW"

    trivial = [_row("A", date(2026, 3, 2), date(2026, 3, 3), 95.0, value=1_000.0)]
    assert build_signal("ACME", trivial, date(2026, 3, 3), None, _reference) is None
    assert build_signal("ACME", trivial, date(2026, 4, 30), None, _reference) is None


def test_the_evidence_carries_no_price_that_is_not_stored():
    signal = build_signal("ACME", [_row("A", date(2026, 3, 2), date(2026, 3, 3), 95.0)],
                          date(2026, 3, 3), None, _reference)
    assert not {"current_price", "near_52wk_low", "price_52wk_low", "market_cap"} & set(signal.evidence)
    assert signal.evidence["insiders"][0]["pct_below_52wk_high"] == 95.0
