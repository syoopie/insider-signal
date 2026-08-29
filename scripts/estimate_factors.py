"""
Which candidate factors actually predict, estimated properly.

Replaces the univariate lift table in analyze_factors.py. Regression separates
correlated factors instead of crediting each with the other's effect, errors are
clustered on ticker so overlapping holds do not manufacture precision, and
Benjamini-Hochberg is applied across the whole candidate set at once rather than
reading 27 raw p-values as if each stood alone.

Runs on the training split only. The validation split selects between models;
the test split is looked at once, at the end.

Usage:
  python3 scripts/estimate_factors.py
  python3 scripts/estimate_factors.py --horizon 60 --split train
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.backtest.engine import HORIZONS
from src.ingest.common import setup_log_tee, log, phase
from src.market.panel import PANEL_PATH
from src.research.estimate import ols_clustered, standardize
from src.research.features import (
    ALL_CANDIDATES,
    CURRENT_FACTORS,
    TIER1,
    TIER2,
    log_scale,
    winsorize,
)
from src.research.protocol import (
    PRIMARY_HORIZON,
    evaluable,
    label_column,
    split_frames,
)

setup_log_tee("estimate_factors")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"

GROUPS = {
    "current model factors": CURRENT_FACTORS,
    "tier 1 (already in the database)": TIER1,
    "tier 2 (from the price panel)": TIER2,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--split", default="train", choices=["train", "validation", "test"])
    args = parser.parse_args()

    phase("DATA")
    frame = pd.read_parquet(args.dataset)
    usable = evaluable(frame, args.horizon)
    work = split_frames(usable, args.horizon)[args.split].frame
    if work.empty:
        log(f"Split '{args.split}' is empty at {args.horizon}d.")
        return

    label = label_column(args.horizon)
    log(f"{args.split} split at {args.horizon}d: n={len(work):,}  "
        f"tickers={work['ticker'].nunique():,}  "
        f"months={pd.to_datetime(work['exec_date']).dt.to_period('M').nunique()}")
    log(f"Label: {label}   mean={work[label].mean():+.2f}%   "
        f"median={work[label].median():+.2f}%")

    prepared = winsorize(log_scale(work), ALL_CANDIDATES)

    phase("UNIVARIATE, FOR CONTRAST")
    log("  What the old procedure would have said, on the same rows.")
    for name in ("f_cap_small", "f_role_director", "f_holdings_increase_5pct",
                 "f_prior_purchase_31_365d"):
        if name not in prepared.columns:
            continue
        present = prepared[prepared[name] != 0][label]
        absent = prepared[prepared[name] == 0][label]
        if len(present) and len(absent):
            log(f"  {name:<28} lift={present.mean() - absent.mean():+7.2f}pp  "
                f"(n_with={len(present):,})")

    phase(f"MULTIVARIATE — all {len(ALL_CANDIDATES)} candidates, clustered on ticker")
    X, names = standardize(prepared, ALL_CANDIDATES)
    if not names:
        log("  no usable columns")
        return
    y = prepared[label].to_numpy(dtype="float64")
    clusters = prepared["ticker"].to_numpy()

    coefficients = ols_clustered(X, y, clusters, names)
    if not coefficients:
        log("  fit failed (too few clusters)")
        return

    by_name = {c.name: c for c in coefficients}
    for group, members in GROUPS.items():
        present = [by_name[m] for m in members if m in by_name]
        if not present:
            continue
        log(f"\n  ── {group} ──")
        for c in sorted(present, key=lambda c: c.p_adjusted):
            log(c.line())

    others = [c for c in coefficients
              if not any(c.name in m for m in GROUPS.values())]
    if others:
        log("\n  ── other terms ──")
        for c in sorted(others, key=lambda c: c.p_adjusted):
            log(c.line())

    phase("COLLINEARITY AMONG SURVIVORS")
    survivor_names = [c.name for c in coefficients if c.significant]
    if len(survivor_names) > 1:
        matrix = pd.DataFrame(X, columns=names)[survivor_names].corr()
        pairs = [
            (a, b, matrix.loc[a, b])
            for i, a in enumerate(survivor_names)
            for b in survivor_names[i + 1:]
        ]
        pairs.sort(key=lambda p: -abs(p[2]))
        for a, b, r in pairs[:5]:
            flag = "  <-- near-duplicate; the pair is one finding, not two" if abs(r) > 0.7 else ""
            log(f"  corr({a}, {b}) = {r:+.3f}{flag}")
    else:
        log("  fewer than two survivors; nothing to check")

    phase("SURVIVORS")
    survivors = [c for c in coefficients if c.significant]
    if not survivors:
        log("  Nothing clears a 5% false-discovery rate on this split.")
        log("  That is a real result, not a failed run. Report it as one.")
    else:
        log(f"  {len(survivors)} of {len(coefficients)} candidates clear FDR 5%:")
        for c in sorted(survivors, key=lambda c: -abs(c.t)):
            direction = "predicts higher" if c.beta > 0 else "predicts lower"
            log(f"    {c.name:<28} {direction} excess return, "
                f"{abs(c.beta):.2f}pp per standard deviation")

    log("\n  Beta is in percentage points of excess return per standard deviation "
        "of the feature.")


if __name__ == "__main__":
    main()
