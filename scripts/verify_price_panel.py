"""
Check the local price panel against the backtest that was measured over the network.

The panel is about to become the input to every scoring experiment, so it has to
be shown equivalent to the path it replaces before anything is fitted on it. This
recomputes each signal's excess return from the panel and compares it against the
value the last backtest stored, which came from live per-signal Yahoo requests.

A large disagreement means the panel is wrong and nothing downstream can be
trusted. Exits with status 1 if the agreement is worse than --tolerance.

Usage:
  python3 scripts/verify_price_panel.py
  python3 scripts/verify_price_panel.py --label adjclose-check --tolerance 0.5
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from src.backtest.engine import SCHEDULED_LABEL, SPY_TICKER
from src.db.connection import get_conn
from src.ingest.common import setup_log_tee, log, phase
from src.market.features import window_return
from src.market.panel import PANEL_PATH, load_panel

setup_log_tee("verify_price_panel")


def _latest_detail(label: str) -> dict[int, list[dict]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT horizon_days, metrics
                FROM backtest_runs
                WHERE run_label = %s
                  AND run_date = (SELECT max(run_date) FROM backtest_runs WHERE run_label = %s)
                ORDER BY horizon_days
            """, (label, label))
            rows = cur.fetchall()
    out = {}
    for horizon, metrics in rows:
        blob = json.loads(metrics) if isinstance(metrics, str) else (metrics or {})
        out[horizon] = blob.get("detail", [])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=SCHEDULED_LABEL)
    parser.add_argument("--tolerance", type=float, default=0.5,
                        help="Max acceptable mean absolute difference, in percentage points")
    args = parser.parse_args()

    phase("LOAD")
    panel = load_panel()
    log(f"Panel: {len(panel):,} symbols from {PANEL_PATH}")
    spy = panel.get(SPY_TICKER)
    if spy is None:
        raise SystemExit("Panel has no SPY; excess returns cannot be recomputed.")

    detail_by_horizon = _latest_detail(args.label)
    if not detail_by_horizon:
        raise SystemExit(f"No backtest run found under label '{args.label}'.")

    phase("COMPARE")
    worst = 0.0
    for horizon, detail in sorted(detail_by_horizon.items()):
        diffs, missing, big = [], 0, []
        for row in detail:
            stored = row.get("excess_return")
            ticker = row.get("ticker")
            exec_str = row.get("exec_date")
            if stored is None or not ticker or not exec_str:
                continue

            exec_date = date.fromisoformat(exec_str[:10])
            exit_date = exec_date + timedelta(days=horizon)
            tkr = window_return(panel.get(ticker), exec_date, exit_date)
            bench = window_return(spy, exec_date, exit_date)
            if not (tkr.ok and bench.ok):
                missing += 1
                continue

            diff = (tkr.pct - bench.pct) - stored
            diffs.append(abs(diff))
            if abs(diff) > 5.0:
                big.append((ticker, exec_str, stored, tkr.pct - bench.pct, diff))

        if not diffs:
            log(f"  {horizon:>4}d  no comparable rows")
            continue

        mean_abs = sum(diffs) / len(diffs)
        within = sum(1 for d in diffs if d <= args.tolerance) / len(diffs) * 100
        worst = max(worst, mean_abs)
        log(f"  {horizon:>4}d  n={len(diffs):>4}  mean|diff|={mean_abs:6.3f}pp  "
            f"max|diff|={max(diffs):6.2f}pp  within {args.tolerance}pp: {within:5.1f}%  "
            f"unmeasurable in panel: {missing}")
        for ticker, when, stored, panel_val, diff in big[:5]:
            log(f"      {ticker:<6} {when}  stored={stored:+7.2f}  "
                f"panel={panel_val:+7.2f}  diff={diff:+6.2f}")

    phase("VERDICT")
    if worst <= args.tolerance:
        log(f"PASS — worst mean absolute difference {worst:.3f}pp <= {args.tolerance}pp")
    else:
        log(f"FAIL — worst mean absolute difference {worst:.3f}pp > {args.tolerance}pp")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
