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
    draws = random_selection_alpha(reference, args.draws, args.rate, args.horizon)
    log(f"random selection alpha over {draws.size} rankings: "
        f"p5={np.percentile(draws, 5):+.3f}  p50={np.percentile(draws, 50):+.3f}  "
        f"p95={np.percentile(draws, 95):+.3f}  sd={draws.std():.3f}")
    log("A candidate beats chance when its selection alpha lands above p95.")

    phase(f"CANDIDATES at {args.horizon}d, top {args.rate:.0%} of each month")
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
        alpha = selection_alpha(scored, "oos", args.rate, args.horizon)
        pct = percentile_of(alpha.mean, draws)
        log(ic.line(f"{name} | rank IC"))
        log(alpha.line(f"{name} | selection alpha") +
            (f"  vs chance p{pct:.0f}" if pct is not None else ""))
        rows.append({
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate": name, "horizon": args.horizon, "rate": args.rate,
            "months": ic.n_months, "n": ic.n_rows,
            "rank_ic": ic.mean, "ic_t": ic.t_stat,
            "sel_alpha": alpha.mean, "alpha_t": alpha.t_stat,
            "vs_chance_pct": pct,
        })

    phase("VERDICT")
    log("  A ranking is better than a coin flip when rank IC clears t >= 2 and")
    log("  selection alpha lands above the 95th percentile of random rankings.")
    for row in sorted(rows, key=lambda r: -(r["rank_ic"] or -9)):
        ranks = (row["ic_t"] or 0) >= 2.0
        beats = (row["vs_chance_pct"] or 0) >= 95
        verdict = "BEATS CHANCE" if (ranks and beats) else "no"
        log(f"  {row['candidate']:<26} ic={row['rank_ic']:+.4f} "
            f"t={row['ic_t'] or float('nan'):+5.2f}  "
            f"alpha={row['sel_alpha']:+6.3f} p{row['vs_chance_pct']:.0f}  -> {verdict}")

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
