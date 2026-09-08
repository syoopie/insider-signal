"""
The frozen ruler, asked the other question. One command, one table.

  uv run python scripts/gates.py
  uv run python scripts/gates.py --statistic median --label vol

`hillclimb.py` asks which purchases to put at the top. This asks which to throw
away. A gate is charged against the whole month it came from, including the rows
it drops, and against each pick's own volatility quintile inside that month, so
the number is what excluding the class was worth and not what the month did.

Every gate is also given its resolution, from the same statistic under labels
shuffled inside each month. A gate scoring under it has not been measured. That
column is why this exists at all: the ranking metric spends the sample on a
37-row decile, a gate spends every row, and the difference is roughly three
times the precision for no new data.

Changing anything in `src/research/walkforward.py` invalidates every number this
has printed. Add hypotheses to `src/research/gates.py` instead.
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
from src.research.estimate import benjamini_hochberg, normal_sf
from src.research.gates import DISQUALIFIERS, GATES
from src.research.protocol import (
    LABEL_FAMILIES,
    PRIMARY_HORIZON,
    evaluable,
    label_column,
)
from src.research.walkforward import (
    STATISTICS,
    class_alpha,
    class_alpha_null,
    percentile_of,
)

setup_log_tee("gates")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"
RESULTS = Path("data/prices/gate_results.csv")

NULL_DRAWS = 200
Z_CRITICAL, Z_POWER = 1.96, 0.8416


def _n(value, width: int, places: int) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return " " * (width - 3) + "n/a"
    return f"{value:>+{width}.{places}f}"


def _wide(frame: pd.DataFrame, horizon: int, label: str) -> pd.DataFrame:
    """
    Every purchase with a closed hold, eligible or not.

    The pool a disqualifier has to be judged against. `evaluable` keeps only
    what the pipeline scores, which is exactly the rows a disqualifier already
    removed, so measuring one there would compare a class against itself.
    """
    keep = ~frame[f"exit_in_future_{horizon}d"] & frame[label].notna()
    return frame[keep].copy()


def _run(frame: pd.DataFrame, gates: dict, horizon: int, label: str,
         statistic: str, draws: int, family: str) -> list[dict]:
    rows = []
    for name, mask_of in gates.items():
        keep = mask_of(frame).reindex(frame.index).fillna(False).astype(bool)
        stat = class_alpha(frame, keep, horizon, statistic=statistic, label=label)
        if stat.mean is None:
            log(f"  {name}: no month had enough rows")
            continue
        null = class_alpha_null(frame, keep, draws, horizon,
                                statistic=statistic, label=label)
        sd = float(null.std(ddof=1)) if null.size > 1 else None
        t = stat.t_stat
        rows.append({
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "family": family, "gate": name, "horizon": horizon, "label": label,
            "statistic": statistic,
            "kept": int(keep.sum()), "dropped": int((~keep).sum()),
            "months": stat.n_months, "n": stat.n_rows,
            "alpha": stat.mean, "t": t,
            "null_sd": sd,
            "mde": (Z_CRITICAL + Z_POWER) * sd if sd else None,
            "vs_chance_pct": percentile_of(stat.mean, null),
            # normal_sf is already the two-sided tail.
            "p": normal_sf(t) if t is not None else None,
        })
    return rows


def _report(rows: list[dict], statistic: str) -> None:
    if not rows:
        return
    lower_better = STATISTICS[statistic].lower_is_better
    pvalues = [r["p"] if r["p"] is not None else 1.0 for r in rows]
    for row, q in zip(rows, benjamini_hochberg(pvalues)):
        row["q"] = q

    log(f"  {'gate':<32} {'kept':>6} {'drop':>6} {'alpha':>8} {'t':>6} "
        f"{'mde':>6} {'p':>4} {'q':>7}")
    for row in rows:
        log(f"  {row['gate']:<32} {row['kept']:>6,} {row['dropped']:>6,} "
            f"{_n(row['alpha'], 8, 3)} {_n(row['t'], 6, 2)} "
            f"{_n(row['mde'], 6, 2)} {_n(row['vs_chance_pct'], 4, 0)} "
            f"{_n(row['q'], 7, 4)}")

    log("")
    for row in sorted(rows, key=lambda r: -abs(r["alpha"] or 0)):
        helps = (row["alpha"] < 0) if lower_better else (row["alpha"] > 0)
        under = row["mde"] and abs(row["alpha"]) < row["mde"]
        if under:
            verdict = f"BELOW RESOLUTION ({row['mde']:.2f})"
        elif not helps:
            verdict = "WRONG DIRECTION"
        elif (row["q"] or 1.0) > 0.05:
            verdict = f"not past FDR 5% (q={row['q']:.3f})"
        elif abs(row["t"] or 0) < 2.0:
            verdict = f"t below 2 ({row['t']:+.2f})"
        else:
            verdict = "SURVIVES"
        log(f"  {row['gate']:<32} alpha={_n(row['alpha'], 8, 3)} -> {verdict}")


def _append(rows: list[dict]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if RESULTS.exists():
        with RESULTS.open(newline="", encoding="utf-8") as handle:
            existing = [r for r in csv.DictReader(handle) if None not in r]
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
    parser.add_argument("--label", default="spy", choices=sorted(LABEL_FAMILIES))
    parser.add_argument("--statistic", default="mean", choices=sorted(STATISTICS))
    parser.add_argument("--draws", type=int, default=NULL_DRAWS)
    args = parser.parse_args()

    phase("DATA")
    frame = pd.read_parquet(args.dataset)
    label = label_column(args.horizon, args.label)
    if label not in frame.columns:
        raise SystemExit(f"{args.dataset} has no {label}; rebuild it with "
                         "scripts/build_research_dataset.py")
    narrow = evaluable(frame, args.horizon, label)
    wide = _wide(frame, args.horizon, label)
    log(f"{len(narrow):,} eligible rows and {len(wide):,} of any kind at "
        f"{args.horizon}d, charged against {label} on the {args.statistic}")

    phase("PROPOSALS, on the purchases the pipeline already scores")
    log("  alpha is what the kept rows returned minus what their whole month")
    log("  returned, risk matched. mde is the same statistic under labels")
    log("  shuffled inside each month, so a gate below it is not a measured")
    log("  zero. q is Benjamini-Hochberg across the whole table.")
    proposals = _run(narrow, GATES, args.horizon, label, args.statistic,
                     args.draws, "proposal")
    _report(proposals, args.statistic)

    phase("VALIDATIONS, on every purchase including the disqualified ones")
    log("  These are already applied in production. A positive number here means")
    log("  the rule earns its place; a negative one means it is costing money.")
    validations = _run(wide, DISQUALIFIERS, args.horizon, label, args.statistic,
                       args.draws, "validation")
    _report(validations, args.statistic)

    rows = proposals + validations
    if rows:
        _append(rows)
        log(f"\n  appended {len(rows)} rows to {RESULTS}")


if __name__ == "__main__":
    main()
