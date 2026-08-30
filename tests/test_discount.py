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
from src.signals.discount import DEEP_DISCOUNT_PCT, KNOTS, discount_score


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
