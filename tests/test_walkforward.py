"""
The ruler has to move when the thing it measures moves, and stay still otherwise.

A metric that cannot tell a perfect ranking from a random one will happily
report a null result forever, and the first protocol came close to doing exactly
that: its verdict rested on 762 rows, 77% of them inside one month. These tests
are the sensitivity proof that has to pass before any hillclimb number counts.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.research.walkforward import (
    amputation_curve,
    class_alpha,
    minimum_detectable_effect,
    MIN_MONTH_ROWS,
    STATISTICS,
    folds,
    percentile_of,
    permutation_alpha,
    rank_ic,
    random_selection_alpha,
    score_of,
    selection_alpha,
    stable_across_folds,
    walk_forward,
)


def _panel(months: int = 24, per_month: int = 60, seed: int = 0) -> pd.DataFrame:
    """
    A synthetic book of purchases with a known signal and a large month effect.

    `signal` predicts the outcome; `noise` does not. The month effect is bigger
    than the signal, which is the situation in the real data and the reason a
    pooled top-k metric is untrustworthy.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(months):
        start = date(2024, 1, 1) + timedelta(days=31 * m)
        month_effect = rng.normal(0, 8)
        for i in range(per_month):
            signal = rng.normal()
            rows.append({
                "exec_date": start + timedelta(days=i % 25),
                "ticker": f"T{rng.integers(0, 200)}",
                "signal": signal,
                "noise": rng.normal(),
                "excess_spy_90d": month_effect + 4.0 * signal + rng.normal(0, 6),
            })
    return pd.DataFrame(rows)


# ── folds ────────────────────────────────────────────────────────────────────

def test_a_fold_never_trains_on_a_hold_that_was_still_open():
    made = folds(_panel(), 90)
    assert made
    for fold in made:
        opens = fold.month.to_timestamp()
        closes = pd.to_datetime(fold.train["exec_date"]) + pd.Timedelta(days=90)
        assert (closes < opens).all()


def test_a_fold_never_trains_on_the_month_it_predicts():
    for fold in folds(_panel(), 90):
        assert pd.to_datetime(fold.train["exec_date"]).max() < fold.month.to_timestamp()


def test_a_longer_horizon_leaves_fewer_predictable_months():
    assert len(folds(_panel(), 180)) < len(folds(_panel(), 30))


def test_every_predicted_row_appears_exactly_once():
    panel = _panel()
    scored = walk_forward(panel, score_of("signal"), 90)
    assert not scored.duplicated(subset=["exec_date", "ticker", "signal"]).all()
    assert len(scored) == sum(len(f.predict) for f in folds(panel, 90))


def test_a_thin_month_is_not_predicted():
    panel = _panel(months=20, per_month=60)
    thin = panel[panel["exec_date"] < date(2025, 6, 1)]
    tail = panel[panel["exec_date"] >= date(2025, 6, 1)].head(MIN_MONTH_ROWS - 1)
    made = folds(pd.concat([thin, tail]), 90)
    assert all(len(f.predict) >= MIN_MONTH_ROWS for f in made)


# ── sensitivity ──────────────────────────────────────────────────────────────

def test_a_perfect_ranking_scores_a_high_positive_ic():
    panel = _panel()
    scored = walk_forward(panel, score_of("excess_spy_90d"), 90)
    stat = rank_ic(scored)
    assert stat.mean == pytest.approx(1.0)
    assert stat.t_stat is None, "a perfect IC has no month-to-month spread to test"


def test_the_real_signal_is_detected_despite_a_larger_month_effect():
    panel = _panel()
    stat = rank_ic(walk_forward(panel, score_of("signal"), 90))
    assert stat.mean > 0.2
    assert stat.t_stat > 4


def test_a_reversed_ranking_scores_negative():
    panel = _panel()
    panel["backwards"] = -panel["signal"]
    stat = rank_ic(walk_forward(panel, score_of("backwards"), 90))
    assert stat.mean < -0.2


def test_pure_noise_does_not_look_like_skill():
    """Across ten seeds the noise ranking must not clear t=2 more than by chance."""
    cleared = 0
    for seed in range(10):
        panel = _panel(seed=seed)
        stat = rank_ic(walk_forward(panel, score_of("noise"), 90))
        assert abs(stat.mean) < 0.1
        if stat.t_stat is not None and abs(stat.t_stat) >= 2.0:
            cleared += 1
    assert cleared <= 1


def test_selection_alpha_is_positive_for_signal_and_flat_for_noise():
    panel = _panel()
    good = selection_alpha(walk_forward(panel, score_of("signal"), 90))
    bad = selection_alpha(walk_forward(panel, score_of("noise"), 90))
    assert good.mean > 2.0
    assert good.t_stat > 3
    assert abs(bad.mean) < 1.5


# ── immunity to the failure that produced the null result ────────────────────

def test_selection_alpha_cannot_be_won_by_preferring_good_months():
    """
    A ranking that knows only which months went up scores zero.

    The pooled top-k metric it replaces scores this strategy as skill, because
    the picks all land in the months whose returns were high.
    """
    panel = _panel()
    month_mean = panel.groupby(
        pd.to_datetime(panel["exec_date"]).dt.to_period("M")
    )["excess_spy_90d"].transform("mean")
    panel["month_oracle"] = month_mean + np.random.default_rng(1).normal(0, 1e-6, len(panel))

    stat = selection_alpha(walk_forward(panel, score_of("month_oracle"), 90))
    assert abs(stat.mean) < 1.0
    assert abs(rank_ic(walk_forward(panel, score_of("month_oracle"), 90)).mean) < 0.1


def test_the_random_baseline_is_a_distribution_not_one_draw():
    panel = _panel()
    scored = walk_forward(panel, score_of("signal"), 90)
    draws = random_selection_alpha(scored, draws=60)
    assert draws.size >= 50
    assert draws.std() > 0
    assert abs(float(np.median(draws))) < 1.0
    assert percentile_of(selection_alpha(scored).mean, draws) > 95


def test_a_drifting_feature_is_dropped_and_a_steady_one_is_kept():
    panel = _panel()
    order = np.argsort(pd.to_datetime(panel["exec_date"]).to_numpy())
    ramp = np.empty(len(panel))
    ramp[order] = np.linspace(0, 20, len(panel))
    panel["clock"] = ramp
    keep = stable_across_folds(panel, ["signal", "noise", "clock"], 90)
    assert "clock" not in keep
    assert {"signal", "noise"} <= set(keep)


@pytest.mark.parametrize("rate", [0.05, 0.10, 0.25])
def test_selection_alpha_survives_the_selectivity_it_is_asked_for(rate):
    panel = _panel()
    stat = selection_alpha(walk_forward(panel, score_of("signal"), 90), rate=rate)
    assert stat.mean > 1.0


# ── the risk tilt, which the first version of this metric rewarded ───────────

def _risk_panel(months: int = 24, per_month: int = 80, seed: int = 3) -> pd.DataFrame:
    """
    A book where volatility buys return and nothing else does.

    Every purchase's outcome is its own volatility times a positive market
    factor, plus noise. There is no skill to find. A metric that scores a
    volatility ranking as skill here would score leverage as alpha in the real
    data, and ranking purchases by prior 21-day volatility does exactly that.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(months):
        start = date(2024, 1, 1) + timedelta(days=31 * m)
        factor = abs(rng.normal(3, 2))
        for i in range(per_month):
            vol = rng.lognormal(0, 0.6)
            rows.append({
                "exec_date": start + timedelta(days=i % 25),
                "ticker": f"T{rng.integers(0, 200)}",
                "tx_vol_21d": vol,
                "skill": rng.normal(),
                "excess_spy_90d": vol * factor + rng.normal(0, 4 * vol),
            })
    return pd.DataFrame(rows)


def test_a_pure_volatility_tilt_reads_as_skill_until_risk_is_matched():
    panel = _risk_panel()
    scored = walk_forward(panel, score_of("tx_vol_21d"), 90)
    naive = selection_alpha(scored, rate=0.10)
    matched = selection_alpha(scored, rate=0.10, risk_matched=True)
    assert naive.mean > 3.0 and naive.t_stat > 3
    # Quintiles are non-parametric, so they leave the within-bucket spread of a
    # lognormal volatility behind. Two thirds of a pure tilt is what the control
    # actually removes, and claiming more than it delivers is the failure this
    # whole exercise is about.
    assert abs(matched.mean) < naive.mean * 0.4


def test_risk_matching_keeps_real_skill_that_has_no_risk_tilt():
    panel = _risk_panel()
    panel["excess_spy_90d"] += 6.0 * panel["skill"]
    scored = walk_forward(panel, score_of("skill"), 90)
    assert selection_alpha(scored, rate=0.10, risk_matched=True).mean > 2.0


def test_risk_matching_reduces_to_the_plain_month_benchmark_when_flat():
    panel = _panel()
    panel["tx_vol_21d"] = 1.0
    scored = walk_forward(panel, score_of("signal"), 90)
    plain = selection_alpha(scored, rate=0.10)
    matched = selection_alpha(scored, rate=0.10, risk_matched=True)
    assert matched.mean == pytest.approx(plain.mean, abs=1e-9)


# ── the median, where the previous shipped candidate actually failed ─────────

def test_a_fat_right_tail_lifts_the_mean_but_not_the_median():
    """
    Half the picks lose a little, a few win enormously. The mean says skill,
    the median says the typical pick is worse than the pool.
    """
    rng = np.random.default_rng(11)
    rows = []
    for m in range(24):
        start = date(2024, 1, 1) + timedelta(days=31 * m)
        for i in range(80):
            lottery = float(i < 20)
            payoff = (rng.random() < 0.15) * rng.exponential(80) - 3.0
            rows.append({
                "exec_date": start + timedelta(days=i % 25),
                "ticker": f"T{rng.integers(0, 200)}",
                "lottery": lottery + rng.normal(0, 0.01),
                "excess_spy_90d": payoff if lottery else rng.normal(0, 6),
            })
    scored = walk_forward(pd.DataFrame(rows), score_of("lottery"), 90)
    assert selection_alpha(scored, rate=0.10, statistic="mean").mean > 0.5
    assert selection_alpha(scored, rate=0.10, statistic="median").mean < 0


# ── the permutation test, which prices the search itself ────────────────────

def test_a_fitted_model_on_shuffled_labels_lands_where_the_real_one_does_not():
    """
    The threat this exists for. A model fitted on many features across many
    folds can manufacture an edge from nothing, and a random-pick baseline never
    pays that price so it never detects it. Under permuted labels the same fit
    must come out flat, and the real signal must sit far above the draws.
    """
    panel = _panel(months=20, per_month=70)
    fitter = score_of("signal")
    null = permutation_alpha(panel, fitter, draws=40, rate=0.10)
    real = selection_alpha(walk_forward(panel, fitter, 90), rate=0.10,
                           risk_matched=True).mean
    assert null.size >= 30
    assert abs(float(np.median(null))) < 1.0
    assert percentile_of(real, null) == 100.0


def test_the_permutation_null_catches_a_model_that_only_fits_noise():
    panel = _panel(months=20, per_month=70)
    fitter = score_of("noise")
    null = permutation_alpha(panel, fitter, draws=40, rate=0.10)
    real = selection_alpha(walk_forward(panel, fitter, 90), rate=0.10,
                           risk_matched=True).mean
    assert (percentile_of(real, null) or 0) < 95


def test_amputation_keeps_a_real_edge_high_and_drops_a_lucky_one():
    """
    Cutting the biggest contributors sinks a real strategy too, so the curve is
    only readable against a null that has been cut the same way.
    """
    real = amputation_curve(walk_forward(_panel(), score_of("signal"), 90),
                            drops=(0, 3, 10), draws=40)
    assert (real["percentile"] >= 90).all()

    lucky = amputation_curve(walk_forward(_panel(), score_of("noise"), 90),
                             drops=(0, 3, 10), draws=40)
    assert (lucky["percentile"] < 90).all()


def test_the_median_statistic_still_sees_a_genuinely_better_pick():
    scored = walk_forward(_panel(), score_of("signal"), 90)
    stat = selection_alpha(scored, rate=0.10, statistic="median")
    assert stat.mean > 1.0
    assert stat.t_stat > 3


# ── the gate estimand, where an exclusion is the hypothesis ─────────────────

def _class_panel(months: int = 24, per_month: int = 80, seed: int = 5,
                 penalty: float = -9.0) -> pd.DataFrame:
    """
    A book where one fifth of every month is a class that reliably underperforms.

    No ranking is planted. The only structure is membership, which is the shape
    a veto candidate has: net-seller firms, late filings, indirect purchases.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(months):
        start = date(2024, 1, 1) + timedelta(days=31 * m)
        month_effect = rng.normal(0, 8)
        for i in range(per_month):
            bad = i % 5 == 0
            rows.append({
                "exec_date": start + timedelta(days=i % 25),
                "ticker": f"T{rng.integers(0, 200)}",
                "bad_class": bad,
                "excess_spy_90d": month_effect + (penalty if bad else 0.0)
                + rng.normal(0, 6),
            })
    return pd.DataFrame(rows)


def test_excluding_a_class_that_is_no_different_scores_zero():
    panel = _class_panel(penalty=0.0)
    rng = np.random.default_rng(3)
    keep = pd.Series(rng.random(len(panel)) > 0.2, index=panel.index)
    stat = class_alpha(panel, keep)
    assert abs(stat.mean) < 0.6
    assert abs(stat.t_stat) < 2


def test_excluding_a_class_that_loses_money_raises_what_is_left():
    panel = _class_panel()
    stat = class_alpha(panel, ~panel["bad_class"])
    assert stat.mean > 1.0
    assert stat.t_stat > 3


def test_the_same_class_measured_as_a_promotion_reads_the_other_way():
    panel = _class_panel()
    assert class_alpha(panel, panel["bad_class"]).mean < -4.0


def test_keeping_everything_scores_exactly_zero():
    panel = _class_panel()
    keep = pd.Series(True, index=panel.index)
    assert class_alpha(panel, keep).mean == pytest.approx(0.0, abs=1e-9)


def test_a_gate_on_the_top_decile_reproduces_the_ranking_metric():
    """
    The two estimands are one core seen from two sides, and this is the check
    that keeps them that way. Feeding `class_alpha` exactly the rows the top
    decile selected must return the number `selection_alpha` printed.
    """
    scored = walk_forward(_panel(), score_of("signal"), 90)
    ranked = selection_alpha(scored, rate=0.10, risk_matched=True)

    work = scored.copy()
    work["month"] = pd.to_datetime(work["exec_date"]).dt.to_period("M")
    picked = pd.concat([
        block.nlargest(max(2, int(round(len(block) * 0.10))), "oos")
        for _month, block in work.groupby("month")
        if len(block) >= MIN_MONTH_ROWS
    ]).index
    keep = pd.Series(scored.index.isin(picked), index=scored.index)

    assert class_alpha(scored, keep).mean == pytest.approx(ranked.mean, abs=1e-9)


def test_a_veto_uses_far_more_rows_than_the_ranking_it_replaces():
    panel = _class_panel()
    ranked = selection_alpha(walk_forward(panel, score_of("bad_class"), 90),
                             rate=0.10)
    assert class_alpha(panel, ~panel["bad_class"]).n_rows > ranked.n_rows * 5


# ── the left tail, where a feature that cannot rank winners may drop losers ──

def _tail_panel(months: int = 24, per_month: int = 80, seed: int = 9) -> pd.DataFrame:
    """
    A class with the same mean as the rest and a much fatter left tail.

    `mean` cannot see it and `p10` and `loss20` must. This is the case the
    strategy actually has: the archive holds one ticker at +541% and -91%.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(months):
        start = date(2024, 1, 1) + timedelta(days=31 * m)
        for i in range(per_month):
            fragile = i % 4 == 0
            outcome = rng.normal(0, 40) if fragile else rng.normal(0, 7)
            rows.append({
                "exec_date": start + timedelta(days=i % 25),
                "ticker": f"T{rng.integers(0, 200)}",
                "fragile": fragile,
                "excess_spy_90d": outcome,
            })
    return pd.DataFrame(rows)


def test_the_mean_cannot_see_a_fat_left_tail_and_p10_can():
    panel = _tail_panel()
    keep = ~panel["fragile"]
    assert abs(class_alpha(panel, keep, statistic="mean").mean) < 1.5
    assert class_alpha(panel, keep, statistic="p10").mean > 5.0


def test_the_loss_rate_statistic_counts_down_not_up():
    panel = _tail_panel()
    stat = class_alpha(panel, ~panel["fragile"], statistic="loss20")
    assert stat.mean < -2.0
    assert STATISTICS["loss20"].lower_is_better


# ── the resolution the ruler runs at ────────────────────────────────────────

def test_a_noisier_book_can_only_be_measured_at_a_coarser_resolution():
    """
    The number that reframes seven rounds of null results. The resolution is a
    property of the book and the pick pattern, not of the candidate, so "did not
    clear t=2" means different things in different places and a single shared
    threshold hides that.
    """
    quiet = _panel(months=24, per_month=60)
    loud = quiet.copy()
    rng = np.random.default_rng(4)
    loud["excess_spy_90d"] = loud["excess_spy_90d"] + rng.normal(0, 25, len(loud))

    fitter = score_of("signal")
    assert (minimum_detectable_effect(loud, fitter, draws=25).detectable
            > minimum_detectable_effect(quiet, fitter, draws=25).detectable * 1.5)


def test_an_effect_below_the_resolution_is_not_detected_and_one_above_is():
    panel = _panel(months=24, per_month=60)
    fitter = score_of("signal")
    mde = minimum_detectable_effect(panel, fitter, draws=25).detectable
    assert mde is not None and mde > 0

    def alpha_at(strength: float) -> float:
        work = panel.copy()
        work["excess_spy_90d"] = (work["excess_spy_90d"]
                                  - 4.0 * work["signal"]
                                  + strength * work["signal"])
        return selection_alpha(walk_forward(work, fitter, 90), rate=0.10,
                               risk_matched=True).t_stat

    assert alpha_at(0.0) < 2.0
    assert alpha_at(8.0) > 2.0
