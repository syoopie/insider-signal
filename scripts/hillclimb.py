"""
The frozen ruler. One command, one table, walk-forward and month-neutral.

  uv run python scripts/hillclimb.py
  uv run python scripts/hillclimb.py --only "current score" --horizon 60

Two numbers decide everything. Rank IC is the mean within-month Spearman
correlation between a ranking and what happened next. Selection alpha is what
the top decile of each month returned minus what that whole month returned.
Both are averaged over months and tested on the spread between them, and both
are computed only on predictions made by a model refitted on data that closed
before the month opened.

Changing anything in `src/research/walkforward.py` invalidates every number this
has printed. Add hypotheses to `src/research/candidates.py` instead.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.engine import HORIZONS
from src.ingest.common import log, phase, setup_log_tee
from src.market.panel import PANEL_PATH
from src.research.candidates import CANDIDATES
from src.research.protocol import LABEL_FAMILIES, PRIMARY_HORIZON, evaluable, label_column
from src.research.walkforward import (
    folds,
    minimum_detectable_effect,
    percentile_of,
    rank_ic,
    random_selection_alpha,
    selection_alpha,
    walk_forward,
)

setup_log_tee("hillclimb")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"
RESULTS = Path("data/prices/hillclimb_results.csv")

SELECTION_RATE = 0.10
RANDOM_DRAWS = 400

# Each draw refits every fold, so this is the expensive column. 60 is enough to
# put the null spread inside about 10% of itself.
MDE_DRAWS = 60


def _n(value, width: int, places: int) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return " " * (width - 3) + "n/a"
    return f"{value:>+{width}.{places}f}"


def _append(rows: list[dict]) -> None:
    """
    Append, rewriting the whole file when the columns have changed.

    A plain append against a stale header writes every value one column left of
    where it is read back from, which is worse than losing the run.
    """
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if RESULTS.exists():
        with RESULTS.open(newline="", encoding="utf-8") as handle:
            existing = [row for row in csv.DictReader(handle) if None not in row]
    fields = list(existing[0]) if existing else []
    fields += [name for name in rows[0] if name not in fields]

    with RESULTS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(existing + rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--rate", type=float, default=SELECTION_RATE)
    parser.add_argument("--label", default="spy", choices=sorted(LABEL_FAMILIES),
                        help="What a purchase is charged against. Every published "
                             "number is on spy; the others are cross-checks.")
    parser.add_argument("--only", action="append", default=None,
                        help="Run one candidate by name. Repeatable.")
    parser.add_argument("--draws", type=int, default=RANDOM_DRAWS)
    parser.add_argument("--mde", type=int, nargs="?", const=MDE_DRAWS, default=0,
                        metavar="DRAWS",
                        help="Permutation draws per candidate for the resolution "
                             "column. Slow; refits the whole walk-forward per draw.")
    args = parser.parse_args()

    phase("DATA")
    frame = pd.read_parquet(args.dataset)
    label = label_column(args.horizon, args.label)
    if label not in frame.columns:
        raise SystemExit(f"{args.dataset} has no {label}; rebuild it with "
                         "scripts/build_research_dataset.py")
    usable = evaluable(frame, args.horizon, label)
    made = folds(usable, args.horizon)
    predicted = sum(len(f.predict) for f in made)
    log(f"{len(usable):,} evaluable rows at {args.horizon}d, charged against {label}")
    log(f"{len(made)} predictable months, {predicted:,} rows scored out of sample")
    if made:
        log(f"first predicted month {made[0].month}, last {made[-1].month}")
    if len(made) < 6:
        log("Too few folds for a verdict. Widen the horizon or rebuild the panel.")
        return

    phase("THE COIN FLIP")
    reference = walk_forward(usable, CANDIDATES["noise"], args.horizon, label=label)
    draws = random_selection_alpha(reference, args.draws, args.rate, args.horizon,
                                   risk_matched=True, label=label)
    log(f"random risk-matched selection alpha over {draws.size} rankings: "
        f"p5={np.percentile(draws, 5):+.3f}  p50={np.percentile(draws, 50):+.3f}  "
        f"p95={np.percentile(draws, 95):+.3f}  sd={draws.std():.3f}")

    phase(f"CANDIDATES at {args.horizon}d, top {args.rate:.0%} of each month")
    log("  alpha is the picks minus their own month; matched charges each pick")
    log("  against its volatility quintile inside that month, so a leverage tilt")
    log("  cannot read as skill. med is the same on medians, where a fat right")
    log("  tail stops helping.")
    if args.mde:
        log("  mde is the smallest constant effect this book could be told apart")
        log("  from zero at 80% power, from the spread of the same fit under")
        log("  labels shuffled inside each month. A candidate under it is not a")
        log("  measured zero, it is below the resolution.")
    head = (f"  {'candidate':<26} {'ic':>7} {'ic t':>6} {'alpha':>8} {'matched':>8} "
            f"{'m t':>6} {'med':>8} {'p':>4}")
    log(head + (f" {'mde':>6}" if args.mde else ""))
    names = args.only if args.only else list(CANDIDATES)
    rows = []
    for name in names:
        fitter = CANDIDATES.get(name)
        if fitter is None:
            log(f"  {name}: not registered")
            continue
        scored = walk_forward(usable, fitter, args.horizon, label=label)
        if scored.empty:
            log(f"  {name}: no fold produced a prediction")
            continue
        ic = rank_ic(scored, "oos", args.horizon, label=label)
        plain = selection_alpha(scored, "oos", args.rate, args.horizon, label=label)
        matched = selection_alpha(scored, "oos", args.rate, args.horizon,
                                  risk_matched=True, label=label)
        median = selection_alpha(scored, "oos", args.rate, args.horizon,
                                 statistic="median", risk_matched=True, label=label)
        pct = percentile_of(matched.mean, draws)
        mde = (minimum_detectable_effect(usable, fitter, args.mde, args.rate,
                                         args.horizon, label=label).detectable
               if args.mde else None)
        row = {
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate": name, "horizon": args.horizon, "rate": args.rate,
            "label": args.label,
            "months": ic.n_months, "n": ic.n_rows,
            "rank_ic": ic.mean, "ic_t": ic.t_stat,
            "alpha": plain.mean, "matched": matched.mean, "matched_t": matched.t_stat,
            "median": median.mean, "median_t": median.t_stat,
            "vs_chance_pct": pct, "mde": mde,
        }
        rows.append(row)
        log(f"  {name:<26} {_n(row['rank_ic'], 7, 4)} {_n(row['ic_t'], 6, 2)} "
            f"{_n(row['alpha'], 8, 3)} {_n(row['matched'], 8, 3)} "
            f"{_n(row['matched_t'], 6, 2)} {_n(row['median'], 8, 3)} "
            f"{_n(pct, 4, 0)}" + (f" {_n(mde, 6, 2)}" if args.mde else ""))

    phase("VERDICT")
    log("  Pre-registered. A ranking beats a coin flip when its risk-matched")
    log("  selection alpha clears the 95th percentile of random rankings, holds")
    log("  t >= 2 across months, stays positive on the median, and rests on 15")
    log("  months or more.")
    for row in sorted(rows, key=lambda r: -(r["matched"] or -9e9)):
        bars = {
            "beats chance": (row["vs_chance_pct"] or 0) >= 95,
            "t >= 2": (row["matched_t"] or 0) >= 2.0,
            "median positive": (row["median"] or -1) > 0,
            "15+ months": row["months"] >= 15,
        }
        failed = [n for n, ok in bars.items() if not ok]
        verdict = "BEATS CHANCE" if not failed else f"no ({', '.join(failed)})"
        # A candidate under the resolution has not been measured, so saying it
        # failed is the error this column exists to stop.
        if failed and row["mde"] and abs(row["matched"] or 0) < row["mde"]:
            verdict = f"BELOW RESOLUTION ({row['mde']:.1f}pp)"
        log(f"  {row['candidate']:<26} matched={_n(row['matched'], 7, 3)} "
            f"t={_n(row['matched_t'], 5, 2)} med={_n(row['median'], 7, 3)} "
            f"-> {verdict}")

    if rows:
        _append(rows)
        log(f"\n  appended {len(rows)} rows to {RESULTS}")


if __name__ == "__main__":
    main()
