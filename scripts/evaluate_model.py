"""
Run the evaluation protocol against the research dataset.

Reports, for a chosen split, how the current score ranks and how it compares
against the four baselines it has to beat. This is the harness every later
scoring change is judged by, and it exists before any model is fitted so that
its rules cannot be chosen to flatter a result.

The test split is not reported unless --split test is passed explicitly. Look at
it once, at the end, and report whatever it says.

Usage:
  python3 scripts/evaluate_model.py
  python3 scripts/evaluate_model.py --split validation --horizon 60
  python3 scripts/evaluate_model.py --split test          # once, at the end
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.backtest.engine import HORIZONS
from src.ingest.common import setup_log_tee, log, phase
from src.market.panel import PANEL_PATH
from src.research.baselines import evaluate_baselines, random_ranking_distribution
from src.research.protocol import (
    PRIMARY_HORIZON,
    decile_spread,
    decile_table,
    evaluable,
    split_bounds,
    split_frames,
    summarise,
)

setup_log_tee("evaluate_model")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"

# How many trades a challenger is allowed to select, as a share of the split.
# Fixed so every model is compared at equal selectivity.
SELECTION_RATE = 0.10


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", default="validation",
                        choices=["train", "validation", "test"])
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--rank-by", default="score",
                        help="Column to rank by. Defaults to the current model's score.")
    args = parser.parse_args()

    phase("SPLITS")
    frame = pd.read_parquet(args.dataset)
    usable = evaluable(frame, args.horizon)
    log(f"Dataset {args.dataset}: {len(frame):,} rows, "
        f"{len(usable):,} evaluable at {args.horizon}d")
    log(f"Purge and embargo: {args.horizon} + 5 days before each boundary")
    for name, (lo, hi) in split_bounds(args.horizon).items():
        shown = "open" if hi == pd.Timestamp.max.date() or hi.year > 9000 else str(hi)
        log(f"  {name:<12} entries {lo} → {shown}")

    splits = split_frames(usable, args.horizon)
    for name, split in splits.items():
        if len(split) == 0:
            log(f"  {name:<12} empty")
            continue
        exec_dates = pd.to_datetime(split.frame["exec_date"])
        log(f"  {name:<12} n={len(split):>5}  "
            f"tickers={split.frame['ticker'].nunique():>4}  "
            f"months={exec_dates.dt.to_period('M').nunique():>3}  "
            f"{exec_dates.min().date()} → {exec_dates.max().date()}")

    work = splits[args.split].frame
    if work.empty:
        log(f"\nSplit '{args.split}' is empty at {args.horizon}d. Nothing to report.")
        return

    if args.split == "test":
        log("\n  *** TEST SPLIT. Report what it says, including a null result. ***")

    k = max(20, int(len(work) * SELECTION_RATE))

    phase(f"RANKING — does '{args.rank_by}' sort by outcome?")
    table = decile_table(work, args.rank_by, args.horizon)
    if table.empty:
        log("  too few rows to decile")
    else:
        log(f"  {'decile':>6} {'n':>6} {'range':>14} {'mean':>9} {'median':>9} {'hit':>7}")
        for row in table.itertuples():
            log(f"  {int(row.decile):>6} {int(row.n):>6} "
                f"{row.lo:>6.0f}-{row.hi:<7.0f} {row.mean:>+8.2f}% "
                f"{row.median:>+8.2f}% {row.hit:>6.1f}%")
        mean_spread, median_spread = decile_spread(table)
        log(f"\n  top-minus-bottom decile:  mean {mean_spread:+.2f}pp   "
            f"median {median_spread:+.2f}pp")
        log("  A ranking that works has a positive spread on both. "
            "A flat or negative spread means the column is not a ranking.")

    phase(f"BASELINES — {args.split} split, {args.horizon}d, k={k}")
    for label, stat in evaluate_baselines(work, k, args.horizon).items():
        log(stat.line(label))

    phase("HOW MUCH OF THAT COULD BE LUCK")
    draws = random_ranking_distribution(work, k, horizon=args.horizon)
    selected = summarise(work.nlargest(k, args.rank_by), args.horizon)
    pct = float((draws < selected.mean).mean() * 100) if selected.mean is not None else None
    log(f"  {draws.count()} random selections of {k}: "
        f"mean {draws.mean():+.2f}%  p5 {draws.quantile(0.05):+.2f}%  "
        f"p95 {draws.quantile(0.95):+.2f}%")
    if pct is not None:
        log(f"  ranking by '{args.rank_by}' scores {selected.mean:+.2f}%, "
            f"the {pct:.1f}th percentile of random")
        if pct < 95:
            log("  That is inside the range random selection produces. "
                "It is not evidence of a working ranking.")

    phase("VERDICT")
    log(f"  Split: {args.split}   horizon: {args.horizon}d   ranked by: {args.rank_by}")
    log("  A challenger must beat every baseline above on this split before it ships.")


if __name__ == "__main__":
    main()
