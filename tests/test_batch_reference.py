"""
A window spans seven days of filings, and each purchase is ranked against what
had been disclosed when it was filed.

Passing one reference for the whole window scored a Monday filing against
Friday's distribution. That is a few days of look-ahead, and it made the backfill
disagree with the research builder on 5.8% of signals, which
`verify_scoring_parity.py` caught before either number was reported.
"""
from datetime import date

from src.signals.batch import score_window


def _row(filed, pct, name="A Buyer"):
    return {
        "transaction_code": "P", "transaction_date": filed, "filed_date": filed,
        "is_10b51": False, "is_direct": True, "is_routine": False,
        "shares": 1000.0, "shares_after": 1000.0, "price_per_share": 10.0,
        "total_value": 10_000.0, "pct_below_52wk_high": pct,
        "insider_name": name, "role_category": "director", "cap_tier": "small",
    }


def test_each_purchase_uses_the_reference_of_its_own_filing_date():
    calm = sorted([2.0] * 200)     # nothing is down, so 40% off is remarkable
    crash = sorted([80.0] * 200)   # everything is down, so 40% off is ordinary

    def reference_for(filed):
        return calm if filed == date(2026, 1, 5) else crash

    window = score_window(
        [_row(date(2026, 1, 5), 40.0, "Early"), _row(date(2026, 1, 9), 40.0, "Late")],
        [], None, reference_for,
    )
    scores = {t["owner"]["name"]: t["score_result"]["score"] for t in window.scored_txs}
    assert scores["Early"] == 100
    assert scores["Late"] == 0


def test_the_window_score_is_the_best_purchase_in_it():
    # 0.0 to 99.5 in steps of 0.5, so a value of v sits at the v-th percentile.
    reference = sorted([float(i) / 2 for i in range(200)])
    window = score_window(
        [_row(date(2026, 1, 5), 10.0, "Small"), _row(date(2026, 1, 5), 80.0, "Deep")],
        [], None, lambda _filed: reference,
    )
    assert window.aggregate_score == 80
    assert sorted(window.participant_scores) == [10, 80]


def test_without_a_reference_the_window_still_scores_off_the_fixed_table():
    window = score_window([_row(date(2026, 1, 5), 24.87)], [], None, None)
    assert window.aggregate_score == 50
