"""
Fit the candidate models, select on validation, leave the test split alone.

Everything is fitted on training and scored on validation. The winner is the one
that beats all four baselines at equal selectivity, and if none does, that is
the result. A null result here is publishable and shipping nothing is a valid
outcome.

Usage:
  python3 scripts/fit_models.py
  python3 scripts/fit_models.py --horizon 60
  python3 scripts/fit_models.py --report-test     # once, after selecting
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.backtest.engine import HORIZONS
from src.ingest.common import setup_log_tee, log, phase
from src.market.panel import PANEL_PATH
from src.research.baselines import evaluate_baselines, random_ranking_distribution
from src.research.features import ALL_CANDIDATES, CURRENT_FACTORS, TIER1
from src.research.models import fit_linear_significant_only, fit_logistic
from src.research.protocol import (
    PRIMARY_HORIZON,
    decile_spread,
    decile_table,
    evaluable,
    label_column,
    split_frames,
    stable_features,
    summarise,
)

setup_log_tee("fit_models")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"
MODEL_OUT = PANEL_PATH.parent / "fitted_model.json"

SELECTION_RATE = 0.10
RIDGE_GRID = [0.3, 1.0, 3.0, 10.0, 30.0, 100.0]

FEATURE_SETS = {
    "current factors only": CURRENT_FACTORS,
    "current + tier 1": CURRENT_FACTORS + TIER1,
    "current + tier 1 + tier 2": ALL_CANDIDATES,
}


def _score_column(frame: pd.DataFrame, model, name: str) -> pd.DataFrame:
    out = frame.copy()
    out[name] = model.raw_score(out)
    return out


def _report(work: pd.DataFrame, column: str, label: str, horizon: int, k: int) -> dict:
    stat = summarise(work.nlargest(k, column), horizon)
    table = decile_table(work, column, horizon)
    mean_spread, median_spread = decile_spread(table)
    draws = random_ranking_distribution(work, k, horizon=horizon)
    percentile = float((draws < stat.mean).mean() * 100) if stat.mean is not None else None

    head = (f"  {label:<34} top{k} mean={stat.mean:+7.2f}%  "
            f"median={stat.median:+7.2f}%  hit={stat.hit_rate:5.1f}%  "
            f"tickers={stat.n_tickers:>4}")
    if stat.se_clustered:
        head += f"  se={stat.se_clustered:5.2f}  t={stat.t_stat:+5.2f}"
    log(head)
    log(f"  {'':<34}       decile spread mean={mean_spread:+6.2f}pp "
        f"median={median_spread:+6.2f}pp  vs random p{percentile:.0f}")
    return {
        "label": label, "mean": stat.mean, "median": stat.median,
        "hit_rate": stat.hit_rate, "mean_spread": mean_spread,
        "median_spread": median_spread, "random_percentile": percentile,
        "t": stat.t_stat, "n_tickers": stat.n_tickers,
    }


def _feature_exposure(train: pd.DataFrame, valid: pd.DataFrame,
                      selected: pd.DataFrame, columns) -> None:
    """
    How often each feature fires, and whether the picks are made of it.

    A weight fitted where a feature is common, applied where it is rare, is a
    weight doing nothing. The reverse is worse: a feature that fires on a
    handful of validation rows can carry the whole selection while resting on
    almost no evidence.
    """
    log(f"  {'feature':<30} {'train':>8} {'valid':>8} {'selected':>9}")
    for name in columns:
        if name not in valid.columns:
            continue
        values = pd.to_numeric(valid[name], errors="coerce")
        if not set(values.dropna().unique()) <= {0.0, 1.0}:
            continue
        t_rate = pd.to_numeric(train[name], errors="coerce").mean()
        v_rate = values.mean()
        s_rate = pd.to_numeric(selected[name], errors="coerce").mean()
        flag = "  <-- rare in validation" if v_rate < 0.02 and s_rate > 0.1 else ""
        log(f"  {name:<30} {t_rate:>7.1%} {v_rate:>8.1%} {s_rate:>9.1%}{flag}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--out", type=Path, default=MODEL_OUT)
    parser.add_argument("--report-test", action="store_true",
                        help="Also score the test split. Do this once, after selecting.")
    args = parser.parse_args()

    label = label_column(args.horizon)

    phase("DATA")
    frame = pd.read_parquet(args.dataset)
    usable = evaluable(frame, args.horizon)
    splits = split_frames(usable, args.horizon)
    train, valid, test = (splits[k].frame for k in ("train", "validation", "test"))
    log(f"train n={len(train):,}  validation n={len(valid):,}  test n={len(test):,}  "
        f"at {args.horizon}d")
    if train.empty or valid.empty:
        log("Not enough data to fit and select.")
        return

    k = max(20, int(len(valid) * SELECTION_RATE))
    log(f"Selectivity: top {k} of {len(valid):,} validation rows ({SELECTION_RATE:.0%})")

    phase("FEATURE STABILITY")
    log("  A feature whose average moves between splits is not measuring the same")
    log("  thing in both. Fitting on one regime and deploying into another reads a clock.")
    keep, dropped = stable_features(train, valid, ALL_CANDIDATES)
    if dropped.empty:
        log("  every candidate is stable across the split boundary")
    else:
        for row in dropped.itertuples():
            log(f"  DROPPED {row.feature:<30} train={row.train_mean:>8.3f}  "
                f"validation={row.other_mean:>8.3f}  drift={row.drift_sd:.2f} sd")
    log(f"  {len(keep)} of {len(ALL_CANDIDATES)} candidates survive")
    feature_sets = {
        name: [c for c in cols if c in keep] for name, cols in FEATURE_SETS.items()
    }

    phase("BASELINES ON VALIDATION")
    baselines = evaluate_baselines(valid, k, args.horizon)
    for name, stat in baselines.items():
        log(stat.line(name))
    beat_mean = max(s.mean for s in baselines.values() if s.mean is not None)
    log(f"\n  A challenger must beat {beat_mean:+.2f}% mean at top {k}.")

    phase("CURRENT MODEL ON VALIDATION")
    current = _report(valid, "score", "current score", args.horizon, k)

    results = []

    phase("MODEL B - regularised logistic")
    best_b, best_b_stat = None, None
    for set_name, columns in feature_sets.items():
        for alpha in RIDGE_GRID:
            model = fit_logistic(train, columns, label, alpha)
            if model is None:
                continue
            stat = summarise(_score_column(valid, model, "_m").nlargest(k, "_m"),
                             args.horizon)
            if stat.mean is None:
                continue
            if best_b_stat is None or stat.mean > best_b_stat.mean:
                best_b, best_b_stat = (model, set_name, alpha), stat
    if best_b:
        model, set_name, alpha = best_b
        scored = _score_column(valid, model, "_m")
        results.append(("B", model,
                        _report(scored, "_m", f"logistic {set_name} a={alpha:g}",
                                args.horizon, k)))
        log("\n  coefficients (per standard deviation, log-odds):")
        for row in model.coefficient_table().head(12).itertuples():
            log(f"    {row.feature:<28} {row.beta:+7.3f}")

    phase("MODEL A - recalibrated additive, significant features only")
    best_a, best_a_stat = None, None
    for set_name, columns in feature_sets.items():
        model = fit_linear_significant_only(train, columns, label)
        if model is None:
            log(f"  {set_name}: nothing cleared the false-discovery rate")
            continue
        stat = summarise(_score_column(valid, model, "_m").nlargest(k, "_m"),
                         args.horizon)
        if stat.mean is None:
            continue
        if best_a_stat is None or stat.mean > best_a_stat.mean:
            best_a, best_a_stat = (model, set_name), stat

    if best_a:
        model, set_name = best_a
        scored = _score_column(valid, model, "_m")
        results.append(("A", model,
                        _report(scored, "_m", f"additive {set_name}", args.horizon, k)))
        log("\n  weights (per standard deviation, percentage points):")
        for row in model.coefficient_table().itertuples():
            log(f"    {row.feature:<28} {row.beta:+7.3f}")

        phase("IS THE EDGE THE MODEL OR THE PERIOD?")
        k_train = max(20, int(len(train) * SELECTION_RATE))
        train_scored = _score_column(train, model, "_m")
        train_pick = _report(train_scored, "_m", "winner, on its own training data",
                             args.horizon, k_train)
        train_base = summarise(train, args.horizon)
        valid_base = summarise(valid, args.horizon)
        log(f"  baseline mean:  train {train_base.mean:+.2f}%   "
            f"validation {valid_base.mean:+.2f}%   "
            f"the period alone moved {valid_base.mean - train_base.mean:+.2f}pp")
        log(f"  edge over baseline:  in-sample "
            f"{train_pick['mean'] - train_base.mean:+.2f}pp   "
            f"out-of-sample {best_a_stat.mean - valid_base.mean:+.2f}pp")
        log("  An out-of-sample edge far below the in-sample one is overfitting. "
            "A similar one is not.")

        phase("WHAT THE PICKS ARE MADE OF")
        _feature_exposure(train, valid, scored.nlargest(k, "_m"),
                          model.standardizer.columns)

    phase("MODEL C - ceiling check")
    log(f"  Gradient boosting is not fitted. Training holds {len(train):,} rows over "
        f"{train['ticker'].nunique():,} tickers,")
    log("  and the protocol cannot reliably separate a tree model's fit from its "
        "overfit at that size.")
    log("  It stays out until the sample supports it, per the plan's recommendation.")

    phase("SELECTION")
    if not results:
        log("  No model was fitted. Nothing to select.")
        return

    log(f"  benchmark to beat: {beat_mean:+.2f}% mean, "
        f"and the current score's {current['mean']:+.2f}%")
    winner = None
    for tag, model, res in sorted(results, key=lambda r: -(r[2]["mean"] or -1e9)):
        beats = res["mean"] is not None and res["mean"] > beat_mean
        ranks = (res["mean_spread"] or 0) > 0 and (res["median_spread"] or 0) > 0
        not_luck = (res["random_percentile"] or 0) >= 95
        significant = (res["t"] or 0) >= 2.0
        verdict = "SHIPPABLE" if (beats and ranks and not_luck and significant) else "rejected"
        log(f"  {tag} {res['label']:<36} beats baselines={beats}  ranks={ranks}  "
            f"beats chance={not_luck}  t>=2={significant}  -> {verdict}")
        if verdict == "SHIPPABLE" and winner is None:
            winner = (tag, model, res)

    if winner is None:
        log("\n  Nothing clears all four bars on validation.")
        log("  That is the result. Shipping nothing beats shipping a coin flip.")
        return

    tag, model, res = winner
    payload = {
        "model": tag,
        "label": res["label"],
        "horizon": args.horizon,
        "kind": model.kind,
        "features": model.standardizer.columns,
        "means": model.standardizer.means.tolist(),
        "sds": model.standardizer.sds.tolist(),
        "clips": {name: list(v) for name, v in model.standardizer.clips.items()},
        "beta": model.beta.tolist(),
        "validation": res,
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"\n  Selected {tag}: {res['label']} -> {args.out}")

    if args.report_test:
        phase("TEST SPLIT - looked at once")
        k_test = max(20, int(len(test) * SELECTION_RATE))
        for name, stat in evaluate_baselines(test, k_test, args.horizon).items():
            log(stat.line(name))
        log("")
        test_res = _report(_score_column(test, model, "_m"), "_m", f"{tag} on test",
                           args.horizon, k_test)
        current_test = _report(test, "score", "current score on test",
                               args.horizon, k_test)

        phase("DOES IT REPLICATE?")
        bars = {
            "beats every baseline": test_res["mean"] > max(
                s.mean for s in evaluate_baselines(test, k_test, args.horizon).values()
                if s.mean is not None),
            "ranks on the mean": (test_res["mean_spread"] or 0) > 0,
            "ranks on the median": (test_res["median_spread"] or 0) > 0,
            "beats chance": (test_res["random_percentile"] or 0) >= 95,
            "clusters to t >= 2": (test_res["t"] or 0) >= 2.0,
            "beats the current score": test_res["mean"] > current_test["mean"],
        }
        for name, passed in bars.items():
            log(f"  {name:<28} {'yes' if passed else 'NO'}")
        if all(bars.values()):
            log("\n  Replicates on every pre-registered bar. Ship it.")
        else:
            failed = [n for n, p in bars.items() if not p]
            log(f"\n  Fails {len(failed)} of {len(bars)} bars: {', '.join(failed)}.")
            log("  The validation advantage did not survive. Do not ship this model.")
            log("  A null result is the result, and reporting it is the point of "
                "pre-registering.")


if __name__ == "__main__":
    main()

