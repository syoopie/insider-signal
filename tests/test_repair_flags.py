"""
The two pieces of `repair_transaction_flags.py` that can corrupt data silently.

Row matching decides which stored row gets which re-parsed flag. Get it wrong
and a 10b5-1 plan sale's flag lands on somebody else's open-market purchase,
which is the exact bug the parser fix was written to kill. It must refuse
rather than guess.

The eligibility count is the number a human approves the write on. It has to be
the scorer's own ladder, not a restatement of it.
"""
from datetime import date

import pytest

from tests.conftest import SCRIPTS, load_script

repair = load_script(SCRIPTS / "repair_transaction_flags.py")


def _stored(**overrides):
    row = {
        "tx_id": 1, "ticker": "ACME", "insider_name": "A BUYER",
        "transaction_date": date(2025, 3, 4), "transaction_code": "P",
        "shares": 1000.0, "price_per_share": 10.0, "total_value": 10_000.0,
        "is_direct": True, "is_10b51": False, "is_routine": False,
    }
    row.update(overrides)
    return repair.StoredRow(**row)


def _parsed(**overrides):
    tx = {
        "transaction_date": "2025-03-04", "transaction_code": "P",
        "shares": 1000.0, "price_per_share": 10.0, "is_direct": True,
        "is_10b51": False,
    }
    tx.update(overrides)
    return tx


def _match(stored, parsed):
    return repair.match_rows([repair.stored_key(s) for s in stored],
                             [repair.parsed_key(p) for p in parsed])


# ── Row matching ──────────────────────────────────────────────────────────────

def test_a_decimal_from_the_database_matches_a_float_from_the_parser():
    """psycopg2 returns Decimal('1000.0000'); the parser returns 1000.0."""
    from decimal import Decimal

    stored = _stored(shares=Decimal("1000.0000"), price_per_share=Decimal("10.00"))
    assert _match([stored], [_parsed()]) == [0]


def test_distinct_rows_match_by_key_whatever_order_they_arrive_in():
    stored = [_stored(tx_id=1, shares=100.0), _stored(tx_id=2, shares=200.0)]
    parsed = [_parsed(shares=200.0), _parsed(shares=100.0)]
    assert _match(stored, parsed) == [1, 0]


def test_a_parsed_row_with_no_stored_counterpart_does_not_block_the_match():
    """purge_debt_transactions.py deleted rows the parser now skips anyway."""
    stored = [_stored(shares=100.0)]
    parsed = [_parsed(shares=100.0), _parsed(shares=999.0)]
    assert _match(stored, parsed) == [0]


def test_a_stored_row_the_reparse_does_not_produce_refuses_the_whole_filing():
    stored = [_stored(shares=100.0), _stored(tx_id=2, shares=555.0)]
    parsed = [_parsed(shares=100.0)]
    assert _match(stored, parsed) is None


def test_identical_broker_fills_match_by_position_when_the_order_agrees():
    """Two fills of the same size at the same price are indistinguishable by key."""
    stored = [_stored(tx_id=1), _stored(tx_id=2), _stored(tx_id=3, shares=50.0)]
    parsed = [_parsed(is_10b51=True), _parsed(is_10b51=True), _parsed(shares=50.0)]
    assert _match(stored, parsed) == [0, 1, 2]


def test_identical_fills_in_a_different_order_are_refused():
    stored = [_stored(tx_id=1), _stored(tx_id=2), _stored(tx_id=3, shares=50.0)]
    parsed = [_parsed(shares=50.0), _parsed(), _parsed()]
    assert _match(stored, parsed) is None


def test_identical_fills_with_unequal_counts_are_refused():
    stored = [_stored(tx_id=1), _stored(tx_id=2)]
    parsed = [_parsed()]
    assert _match(stored, parsed) is None


def test_an_empty_reparse_against_stored_rows_is_refused():
    assert _match([_stored()], []) is None


def test_a_filing_with_nothing_stored_matches_vacuously():
    assert _match([], [_parsed()]) == []


def test_repair_10b51_refuses_the_filing_rather_than_guessing():
    filing = repair.Filing("0001-24-000001", "123", (
        _stored(tx_id=11), _stored(tx_id=12), _stored(tx_id=13, shares=50.0)))
    cached = {filing.accession: [_parsed(shares=50.0), _parsed(), _parsed()]}
    flags, matched, unmatched = repair.repair_10b51([filing], cached)
    assert flags == {}
    assert matched == 0
    assert [f.accession for f in unmatched] == [filing.accession]


def test_repair_10b51_writes_the_reparsed_flag_onto_the_row_it_matched():
    filing = repair.Filing("0001-24-000001", "123", (
        _stored(tx_id=11, shares=100.0), _stored(tx_id=12, shares=200.0)))
    cached = {filing.accession: [_parsed(shares=200.0, is_10b51=True),
                                 _parsed(shares=100.0)]}
    flags, matched, unmatched = repair.repair_10b51([filing], cached)
    assert flags == {11: False, 12: True}
    assert (matched, unmatched) == (1, [])


def test_a_filing_never_fetched_is_left_alone_rather_than_reported_unmatched():
    filing = repair.Filing("0001-24-000001", "123", (_stored(tx_id=11),))
    assert repair.repair_10b51([filing], {}) == ({}, 0, [])


def test_a_row_with_no_reparsed_flag_keeps_the_one_it_is_stored_with():
    """A filing EDGAR would not serve must not be silently reset to False."""
    filing = repair.Filing("0001-24-000001", "123", (
        _stored(tx_id=11, is_10b51=True), _stored(tx_id=12, transaction_code="S")))
    after = repair.repaired([filing], {}, {})
    assert [(r.tx_id, r.is_10b51) for r in after] == [(11, True)]


# ── Merging the routine flag ──────────────────────────────────────────────────

@pytest.mark.parametrize("stored,recomputed,merged", [
    (True, True, True),
    (True, False, True),
    (True, None, True),
    (False, True, True),
    (None, True, True),
    (False, False, False),
    (False, None, False),
    (None, False, False),
    (None, None, None),
])
def test_evidence_of_a_same_month_purchase_is_never_unfound(stored, recomputed, merged):
    """More history finds more same-month years, never fewer."""
    assert repair.merge_routine(stored, recomputed) is merged


def test_a_recompute_that_cannot_reach_a_prior_year_does_not_erase_a_decision():
    """3,831 stored False values land here: pruning deleted the months that decided them."""
    assert repair.merge_routine(False, None) is False


# ── Eligibility ───────────────────────────────────────────────────────────────

def test_an_ordinary_open_market_purchase_is_eligible():
    assert repair.is_eligible(_stored()) is True


@pytest.mark.parametrize("field,value", [
    ("is_10b51", True),
    ("total_value", 1_999.0),
    ("is_routine", True),
    ("transaction_code", "S"),
])
def test_each_disqualifier_makes_the_purchase_ineligible(field, value):
    assert repair.is_eligible(_stored(**{field: value})) is False


def test_an_undetermined_routine_flag_leaves_the_purchase_eligible():
    """None means cannot determine, and the scorer does not disqualify on it."""
    assert repair.is_eligible(_stored(is_routine=None)) is True


def test_a_missing_price_leaves_the_purchase_ineligible_on_value():
    """The scorer reads a null price as $0 on purpose; this must not diverge."""
    assert repair.is_eligible(_stored(total_value=None)) is False


def test_the_ten_b5_one_flip_is_what_moves_eligibility():
    from dataclasses import replace

    before = _stored(is_10b51=True)
    after = replace(before, is_10b51=False)
    assert (repair.is_eligible(before), repair.is_eligible(after)) == (False, True)
