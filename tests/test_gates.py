"""
The gate masks and the SIC map, which are both tables that rot silently.

A wrong sector range sends a biotech's label to the industrials fund and nothing
downstream complains; a mask that returns the wrong dtype quietly keeps every
row. Both failures look like a null result.
"""
from datetime import date, timedelta

import pandas as pd

from src.research.gates import DISQUALIFIERS, GATES, LATE_FILING_DAYS, NET_BUYING
from src.research.sectors import SIC_RANGES, sector_etf
from src.market.panel import BENCHMARK_SYMBOLS


def _frame() -> pd.DataFrame:
    start = date(2025, 1, 1)
    return pd.DataFrame({
        "exec_date": [start + timedelta(days=i) for i in range(6)],
        "ticker": list("ABCDEF"),
        "role_category": ["ceo", "director", "cfo", "other", "officer", "director"],
        "insider_role": ["CEO", "Director", "CFO", "", "EVP", "Director"],
        "demand_buy_ratio": [0.9, 0.1, 0.5, None, 0.49, 0.8],
        "filing_lag_days": [2, 400, 3, None, 30, 1],
        "is_direct": [True, False, True, None, True, False],
        "is_10b51": [False, True, False, None, False, False],
        "is_routine": [False, False, True, None, False, None],
        "total_value": [50_000, 1_000, 900_000, None, 2_000, 1_999],
        "cluster_roster_share": [0.5, 0.1, 0.25, None, 0.24, 1.0],
        "is_averaging_down": [False, True, False, None, True, False],
        "excess_spy_90d": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


def test_every_gate_returns_one_boolean_per_row():
    frame = _frame()
    for name, mask_of in {**GATES, **DISQUALIFIERS}.items():
        mask = mask_of(frame)
        assert len(mask) == len(frame), name
        assert mask.dtype == bool, name


def test_a_gate_on_a_missing_number_drops_the_row():
    """
    Row 3 has no demand ratio, no filing lag and no roster share. A gate that
    keeps it has failed open, which reads downstream as "this exclusion is
    worth nothing" and is indistinguishable from a real null.
    """
    frame = _frame()
    for name in ("firm not net selling", "filed within 30 days",
                 "buyers are 25% of the roster"):
        assert not bool(GATES[name](frame).iloc[3]), name


def test_a_gate_on_a_missing_flag_does_what_the_scorer_does():
    """
    Null is not missing for the booleans, it is a legacy row, and the scorer
    reads it as "not routine" and "not a plan trade". A gate that dropped those
    would measure a rule production does not apply.
    """
    frame = _frame()
    assert bool(DISQUALIFIERS["not a routine buyer"](frame).iloc[3])
    assert bool(DISQUALIFIERS["not a 10b5-1 plan trade"](frame).iloc[3])
    assert not bool(GATES["direct only"](frame).iloc[3])


def test_the_thresholds_are_the_ones_the_module_declares():
    frame = _frame()
    net = GATES["firm not net selling"](frame)
    assert list(net) == [r is not None and r >= NET_BUYING
                         for r in [0.9, 0.1, 0.5, None, 0.49, 0.8]]

    late = GATES["filed within 30 days"](frame)
    assert list(late) == [d is not None and d < LATE_FILING_DAYS
                          for d in [2, 400, 3, None, 30, 1]]


def test_the_disqualifiers_drop_exactly_what_the_scorer_drops():
    frame = _frame()
    assert list(DISQUALIFIERS["not a 10b5-1 plan trade"](frame)) == \
        [True, False, True, True, True, True]
    assert list(DISQUALIFIERS["not a routine buyer"](frame)) == \
        [True, True, False, True, True, True]
    assert list(DISQUALIFIERS["at least $2,000"](frame)) == \
        [True, False, True, False, True, False]


def test_the_coin_flip_control_drops_roughly_a_fifth():
    frame = pd.DataFrame({"x": range(4000)})
    kept = GATES["coin flip"](frame).mean()
    assert 0.75 < kept < 0.85


# ── the SIC map ─────────────────────────────────────────────────────────────

def test_every_sector_fund_is_one_the_panel_fetches():
    assert {etf for _lo, _hi, etf in SIC_RANGES} <= set(BENCHMARK_SYMBOLS)


def test_no_range_is_inverted():
    assert all(lo <= hi for lo, hi, _etf in SIC_RANGES)


def test_a_narrow_range_wins_over_the_wide_one_it_sits_inside():
    assert sector_etf("2834") == "XLV"   # pharmaceuticals inside chemicals
    assert sector_etf("2810") == "XLB"
    assert sector_etf("3572") == "XLK"   # computer storage inside machinery
    assert sector_etf("3550") == "XLI"
    assert sector_etf("6798") == "XLRE"  # REITs inside finance
    assert sector_etf("6021") == "XLF"


def test_the_groups_that_carry_the_sample_all_resolve():
    """The twelve most common SIC prefixes in `companies` must all land."""
    for prefix in ("28", "73", "60", "38", "36", "67", "35", "49", "63", "13",
                   "37", "80"):
        assert sector_etf(prefix + "00") is not None, prefix


def test_an_unmappable_code_gets_no_sector_rather_than_a_wrong_one():
    assert sector_etf(None) is None
    assert sector_etf("") is None
    assert sector_etf("9995") is None    # nonclassifiable establishments
    assert sector_etf("not a code") is None
