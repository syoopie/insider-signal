"""
The one factor that ranks, and the properties the pipeline depends on.

`discount_score` is the whole scoring model now, so its contract is narrow and
load-bearing. It must be monotone, because depth still orders the tail. It must
be a pure function, because the live path and the backfill both call it and the
old 52-week factors were deleted precisely for disagreeing. And it must refuse
to answer rather than guess, because a purchase scored at the median on no
evidence lands in WATCH.
"""
import pytest

from src.signals.constants import BUY_SCORE, WATCH_SCORE
from src.signals.discount import (
    DEEP_DISCOUNT_PCT,
    KNOTS,
    MIN_REFERENCE,
    discount_score,
)


def test_the_knots_are_monotone():
    values = [v for v, _ in KNOTS]
    scores = [s for _, s in KNOTS]
    assert values == sorted(values)
    assert scores == sorted(scores)
    assert scores[0] == 0 and scores[-1] == 100


@pytest.mark.parametrize("value,expected", [(v, s) for v, s in KNOTS])
def test_every_knot_maps_to_its_own_percentile(value, expected):
    assert discount_score(value) == expected


def test_the_mapping_is_monotone_between_knots():
    previous = -1
    for tenth in range(0, 1000):
        score = discount_score(tenth / 10.0)
        assert score >= previous
        previous = score


def test_it_stays_inside_the_scale():
    for value in (-100.0, -0.01, 0.0, 50.0, 99.15, 500.0):
        assert 0 <= discount_score(value) <= 100


def test_missing_context_is_none_rather_than_a_default():
    assert discount_score(None) is None
    assert discount_score(float("nan")) is None
    assert discount_score("not a number") is None


def test_the_buy_cutoff_is_the_top_decile():
    """
    Deciles 1 through 9 return between -0.8% and +2.9% with a negative median
    in every one; the tenth returns +17.5% mean and +6.6% median. The cutoff has
    to sit on that boundary or it is selecting from the flat part.
    """
    assert discount_score(DEEP_DISCOUNT_PCT) == BUY_SCORE
    assert discount_score(DEEP_DISCOUNT_PCT - 1.0) < BUY_SCORE
    assert WATCH_SCORE < BUY_SCORE


def test_a_stock_at_its_high_is_never_a_buy():
    assert discount_score(0.0) < WATCH_SCORE


def test_the_same_input_always_gives_the_same_answer():
    """
    The live path has a Yahoo quote and the backfill does not. Both call this
    with a number stored on the transaction row, so the only way they can
    disagree is if this function is not pure.
    """
    assert discount_score(37.5) == discount_score(37.5)
    assert discount_score(37.5) == discount_score(37.50000)


# ── the trailing reference, which the absolute cutoff needed ────────────────

def test_a_reference_makes_the_score_a_rank_inside_it():
    reference = [i / 2 for i in range(200)]  # 0.0 to 99.5, uniform
    assert discount_score(50.0, reference) == 50
    assert discount_score(90.0, reference) == 90
    assert discount_score(5.0, reference) == 5


def test_the_same_discount_scores_differently_in_different_regimes():
    """
    The point of the reference. A stock 40% off its high is remarkable when
    nothing else is down and ordinary in a drawdown, and a fixed table cannot
    tell the difference. It selected 2.0% of one month's purchases and 23.7% of
    another's, reaching past the top decile into the flat nine.
    """
    calm = sorted([5.0] * 200)
    crash = sorted([50.0] * 200)
    assert discount_score(40.0, calm) == 100
    assert discount_score(40.0, crash) == 0


def test_a_thin_reference_leaves_the_purchase_unranked():
    """
    Not the fixed table. The two rules disagree, and falling back means a
    signal's meaning depends on how busy the filing calendar was that month. The
    four picks that came from the fallback averaged −34.07pp against the ranked
    picks' +15.59pp.
    """
    assert discount_score(24.87, [10.0] * (MIN_REFERENCE - 1)) is None
    assert discount_score(24.87, [10.0] * MIN_REFERENCE) is not None


def test_no_reference_at_all_still_uses_the_fixed_table():
    """For callers with no database: the tests, and the web explainer's mirror."""
    assert discount_score(24.87) == 50


def test_the_cutoff_means_exactly_the_top_decile():
    """
    Truncation, not rounding. `int(round(x)) >= 90` admits everything from 89.5,
    which is the top 10.5% and not the decile the effect was measured on.
    """
    reference = [float(i) / 10 for i in range(1000)]  # 0.0 to 99.9
    assert discount_score(89.9, reference) == 89
    assert discount_score(90.0, reference) == 90
    selected = sum(1 for v in reference if (discount_score(v, reference) or 0) >= 90)
    assert selected == 100  # exactly a tenth of 1000


def test_the_reference_keeps_the_score_monotone():
    rng = __import__("numpy").random.default_rng(0)
    reference = sorted(rng.uniform(0, 90, 500).tolist())
    previous = -1
    for tenth in range(0, 1000):
        score = discount_score(tenth / 10.0, reference)
        assert score >= previous
        previous = score


def test_a_missing_discount_is_still_none_with_a_reference():
    assert discount_score(None, list(range(200))) is None
