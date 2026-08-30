"""
Does the insider matter, or would any beaten-down stock have done as well?

The result in `docs/scoring-improvement-plan.md` section 7b says that among
insider purchases, the deeply discounted ones beat their month-and-risk-matched
peers by 11 percentage points. That is a better ranking of insider purchases,
which is the job the product does, and it is not on its own evidence that the
insider contributed anything. Distance below the 52-week high is a known equity
effect, and a screen that works equally well on stocks nobody bought would mean
the product is a value screen wearing an insider-signal costume.

This builds the missing control. For every real purchase it draws `--controls`
placebo observations: a different ticker from the same panel, on the same
transaction date, entered on the same exec date and held the same horizon. The
month structure, the calendar and the holding windows are therefore identical,
and the only thing that differs is that nobody filed a Form 4.

Then it runs the same decile table and the same walk-forward selection alpha on
both, and prints them side by side.

  uv run python scripts/insider_control.py
  uv run python scripts/insider_control.py --controls 5 --horizon 180
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.engine import EXEC_LAG_DAYS, HORIZONS
from src.ingest.common import log, phase, setup_log_tee
from src.market.features import price_context, window_return
from src.market.panel import PANEL_PATH, load_panel
from src.research.protocol import PRIMARY_HORIZON, evaluable, label_column
from src.research.walkforward import (
    feature_fitter, month_of, percentile_of, random_selection_alpha,
    selection_alpha, walk_forward,
)

setup_log_tee("insider_control")

DEFAULT_DATASET = PANEL_PATH.parent / "research_dataset.parquet"
CONTROL_OUT = PANEL_PATH.parent / "control_sample.parquet"

BENCHMARK = "SPY"
MIN_BARS = 252


def build(real: pd.DataFrame, panel: dict, controls: int, horizon: int,
          seed: int) -> pd.DataFrame:
    """
    One placebo per real purchase per draw, same date, different ticker.

    Symbols are sampled from the panel rather than from the purchase history so
    the control is "a stock that existed", not "a stock somebody else bought".
    Sampling the latter would inherit whatever selection insiders apply and
    quietly answer a different question.
    """
    rng = np.random.default_rng(seed)
    universe = np.array(sorted(s for s, series in panel.items()
                               if len(series) >= MIN_BARS and s != BENCHMARK))
    spy = panel.get(BENCHMARK)
    label = label_column(horizon)

    rows = []
    for source in real.itertuples():
        tx_date = pd.Timestamp(source.transaction_date).date()
        exec_date = pd.Timestamp(source.exec_date).date()
        exit_date = exec_date + timedelta(days=horizon)
        bench = window_return(spy, exec_date, exit_date)
        if not bench.ok:
            continue
        for symbol in rng.choice(universe, size=controls, replace=False):
            series = panel.get(str(symbol))
            context = price_context(series, tx_date)
            if context["pct_below_52wk_high"] is None \
                    or context["n_bars_before"] < MIN_BARS:
                continue
            moved = window_return(series, exec_date, exit_date)
            if not moved.ok:
                continue
            rows.append({
                "ticker": str(symbol),
                "exec_date": exec_date,
                "tx_pct_below_52wk_high": context["pct_below_52wk_high"],
                "tx_vol_21d": context["vol_21d"],
                label: moved.pct - bench.pct,
            })
    return pd.DataFrame(rows)


def decile_table(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    work = frame.copy()
    work["_m"] = month_of(work)
    work["_d"] = work.groupby("_m")["tx_pct_below_52wk_high"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False) + 1
        if s.notna().sum() >= 20 else np.nan)
    return work.groupby("_d").agg(
        n=(label, "size"), mean=(label, "mean"), median=(label, "median"),
        hit=(label, lambda s: (s > 0).mean() * 100)).round(2)


def measure(frame: pd.DataFrame, tag: str, horizon: int, rate: float,
            draws: int) -> None:
    scored = walk_forward(frame, feature_fitter("tx_pct_below_52wk_high"), horizon)
    if scored.empty:
        log(f"  {tag}: no fold produced a prediction")
        return
    mean = selection_alpha(scored, "oos", rate, horizon, risk_matched=True)
    median = selection_alpha(scored, "oos", rate, horizon, statistic="median",
                             risk_matched=True)
    null = random_selection_alpha(scored, draws, rate, horizon, risk_matched=True)
    log(f"  {tag:<24} months={mean.n_months:>3}  n={len(scored):>6}  "
        f"matched={mean.mean:+7.3f}  t={mean.t_stat:+5.2f}  "
        f"median={median.mean:+7.3f}  vs chance p{percentile_of(mean.mean, null):.0f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--horizon", type=int, default=PRIMARY_HORIZON, choices=HORIZONS)
    parser.add_argument("--controls", type=int, default=3)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--draws", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()

    label = label_column(args.horizon)

    phase("DATA")
    real = evaluable(pd.read_parquet(args.dataset), args.horizon)
    panel = load_panel()
    log(f"{len(real):,} real purchases, {len(panel):,} symbols in the panel")
    log(f"drawing {args.controls} placebo observations per purchase, same dates, "
        f"different tickers")
    log(f"entry is exec_date = filed_date + 1 + {EXEC_LAG_DAYS}, identical for both")

    control = build(real, panel, args.controls, args.horizon, args.seed)
    control.to_parquet(CONTROL_OUT, index=False)
    log(f"{len(control):,} control observations -> {CONTROL_OUT}")

    phase("DECILE OF DISCOUNT, REAL PURCHASES")
    log(decile_table(real, label).to_string())

    phase("DECILE OF DISCOUNT, CONTROL")
    log(decile_table(control, label).to_string())

    phase(f"WALK-FORWARD, top {args.rate:.0%} of each month, risk matched")
    measure(real, "insider purchases", args.horizon, args.rate, args.draws)
    measure(control, "placebo, same dates", args.horizon, args.rate, args.draws)

    phase("READING IT")
    log("  A control that matches the real number means the discount does the work")
    log("  and the Form 4 adds nothing. A control near zero means the filing is")
    log("  part of the effect. Anything between is a partial contribution, and the")
    log("  gap is what the insider is worth.")


if __name__ == "__main__":
    main()
