"""
Weekly backtest entrypoint. Called by GitHub Actions weekly_backtest.yml.

Evaluates historical BUY/CLUSTER_BUY signals against actual price returns
and stores results in backtest_runs for dashboard display.

Nothing to run until signals are at least 33 days old (30d horizon + 3d lag).
"""

import sys
import os
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingest.common import setup_log_tee, log, phase
from src.backtest.engine import run_backtest, save_backtest_results
from src.alerts.telegram import send_error

setup_log_tee("backtest")

THRESHOLD    = 65
LOOKBACK_DAYS = 730


def main():
    print(f"=== Weekly Backtest — {date.today()} (UTC {datetime.utcnow().strftime('%H:%M:%S')}) ===")

    results = run_backtest(threshold=THRESHOLD, lookback_days=LOOKBACK_DAYS)

    phase("WRAP UP")
    if results:
        save_backtest_results(results, threshold=THRESHOLD)
    else:
        log("No results to save.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"FATAL ERROR:\n{tb}")
        send_error(f"{str(e)}\n\n{tb[:500]}", context="weekly backtest")
        sys.exit(1)
