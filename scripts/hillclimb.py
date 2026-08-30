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
from src.research.protocol import PRIMARY_HORIZON, evaluable
from src.research.walkforward import (
    folds,
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


def _n(value, width: int, places: int) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return " " * (width - 3) + "n/a"
    return f"{value:>+{width}.{places}f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--rate", type=float, default=SELECTION_RATE)
    parser.add_argument("--only", action="append", default=None,
                        help="Run one candidate by name. Repeatable.")
    parser.add_argument("--draws", type=int, default=RANDOM_DRAWS)
    args = parser.parse_args()

    phase("DATA")
    frame = pd.read_parquet(args.dataset)
    usable = evaluable(frame, args.horizon)
    made = folds(usable, args.horizon)
    predicted = sum(len(f.predict) for f in made)
    log(f"{len(usable):,} evaluable rows at {args.horizon}d")
    log(f"{len(made)} predictable months, {predicted:,} rows scored out of sample")
    if made:
        log(f"first predicted month {made[0].month}, last {made[-1].month}")
    if len(made) < 6:
        log("Too few folds for a verdict. Widen the horizon or rebuild the panel.")
        return

    phase("THE COIN FLIP")
    reference = walk_forward(usable, CANDIDATES["noise"], args.horizon)
    draws = random_selection_alpha(reference, args.draws, args.rate, args.horizon,
                                   risk_matched=True)
    log(f"random risk-matched selection alpha over {draws.size} rankings: "
        f"p5={np.percentile(draws, 5):+.3f}  p50={np.percentile(draws, 50):+.3f}  "
        f"p95={np.percentile(draws, 95):+.3f}  sd={draws.std():.3f}")

    phase(f"CANDIDATES at {args.horizon}d, top {args.rate:.0%} of each month")
    log("  alpha is the picks minus their own month; matched charges each pick")
    log("  against its volatility quintile inside that month, so a leverage tilt")
    log("  cannot read as skill. med is the same on medians, where a fat right")
    log("  tail stops helping.")
    log(f"  {'candidate':<26} {'ic':>7} {'ic t':>6} {'alpha':>8} {'matched':>8} "
        f"{'m t':>6} {'med':>8} {'p':>4}")
    names = args.only if args.only else list(CANDIDATES)
    rows = []
    for name in names:
        fitter = CANDIDATES.get(name)
        if fitter is None:
            log(f"  {name}: not registered")
            continue
        scored = walk_forward(usable, fitter, args.horizon)
        if scored.empty:
            log(f"  {name}: no fold produced a prediction")
            continue
        ic = rank_ic(scored, "oos", args.horizon)
        plain = selection_alpha(scored, "oos", args.rate, args.horizon)
        matched = selection_alpha(scored, "oos", args.rate, args.horizon,
                                  risk_matched=True)
        median = selection_alpha(scored, "oos", args.rate, args.horizon,
                                 statistic="median", risk_matched=True)
        pct = percentile_of(matched.mean, draws)
        row = {
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate": name, "horizon": args.horizon, "rate": args.rate,
            "months": ic.n_months, "n": ic.n_rows,
            "rank_ic": ic.mean, "ic_t": ic.t_stat,
            "alpha": plain.mean, "matched": matched.mean, "matched_t": matched.t_stat,
            "median": median.mean, "median_t": median.t_stat,
            "vs_chance_pct": pct,
        }
        rows.append(row)
        log(f"  {name:<26} {_n(row['rank_ic'], 7, 4)} {_n(row['ic_t'], 6, 2)} "
            f"{_n(row['alpha'], 8, 3)} {_n(row['matched'], 8, 3)} "
            f"{_n(row['matched_t'], 6, 2)} {_n(row['median'], 8, 3)} "
            f"{_n(pct, 4, 0)}")

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
        log(f"  {row['candidate']:<26} matched={_n(row['matched'], 7, 3)} "
            f"t={_n(row['matched_t'], 5, 2)} med={_n(row['median'], 7, 3)} "
            f"-> {verdict}")

    if rows:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        new = not RESULTS.exists()
        with RESULTS.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            if new:
                writer.writeheader()
            writer.writerows(rows)
        log(f"\n  appended {len(rows)} rows to {RESULTS}")


if __name__ == "__main__":
    main()
