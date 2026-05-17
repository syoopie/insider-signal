"""
Backtest engine: validates signal quality on historical data.

Methodology (bias-controlled):
  - Signal date = filing date + 1 day (point-in-time, no look-ahead)
  - Execution date = signal date + 3 calendar days (realistic fill lag)
  - Benchmark: SPY return over same window
  - Delisted stocks: yfinance returns empty / last price → treated as loss
  - Parameters (threshold=65, cluster_window=14d) set from literature, not tuned

Run weekly via GitHub Actions.
"""

from datetime import date, timedelta
from typing import Optional, List, Dict
import json

from src.db.connection import get_conn
from src.market.prices import get_price_change_pct


HORIZONS = [30, 60, 90, 180]
SPY_TICKER = "SPY"


def run_backtest(threshold: int = 65, lookback_days: int = 365) -> List[Dict]:
    """
    Run backtest over all BUY/CLUSTER_BUY signals in the last `lookback_days` days.
    Returns list of result dicts, one per horizon.
    """
    since = date.today() - timedelta(days=lookback_days)
    signals = _get_historical_signals(since, threshold)

    if not signals:
        print("No historical signals found for backtest.")
        return []

    results = []
    for horizon in HORIZONS:
        returns = []
        for sig in signals:
            sig_date = sig["signal_date"]
            if isinstance(sig_date, str):
                try:
                    sig_date = date.fromisoformat(sig_date)
                except ValueError:
                    continue

            exec_date = sig_date + timedelta(days=3)
            exit_date = exec_date + timedelta(days=horizon)

            if exit_date > date.today() - timedelta(days=1):
                continue  # Signal too recent; exit hasn't happened yet

            ticker_return = get_price_change_pct(sig["ticker"], exec_date, exit_date)
            spy_return = get_price_change_pct(SPY_TICKER, exec_date, exit_date)

            if ticker_return is None:
                # Delisted or no data — treat as large loss (survivorship bias correction)
                ticker_return = -50.0
            if spy_return is None:
                continue

            excess = ticker_return - spy_return
            returns.append({
                "ticker": sig["ticker"],
                "signal_type": sig["signal_type"],
                "exec_date": exec_date.isoformat(),
                "ticker_return": ticker_return,
                "spy_return": spy_return,
                "excess_return": excess,
            })

        if not returns:
            continue

        n = len(returns)
        hit_rate = sum(1 for r in returns if r["excess_return"] > 0) / n * 100
        avg_return = sum(r["excess_return"] for r in returns) / n

        # Annualized Sharpe (simplified)
        import statistics
        excess_vals = [r["excess_return"] for r in returns]
        if len(excess_vals) > 1 and statistics.stdev(excess_vals) > 0:
            sharpe = (avg_return / statistics.stdev(excess_vals)) * (252 / horizon) ** 0.5
        else:
            sharpe = None

        results.append({
            "horizon_days": horizon,
            "n_trades": n,
            "hit_rate": round(hit_rate, 1),
            "avg_return": round(avg_return, 2),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "detail": returns[:50],  # Store first 50 for inspection
        })

        print(f"  {horizon}d: {n} trades | hit rate {hit_rate:.0f}% | avg excess {avg_return:+.1f}%")

    return results


def save_backtest_results(results: List[Dict], threshold: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in results:
                cur.execute(
                    """
                    INSERT INTO backtest_runs
                        (run_date, threshold, horizon_days, n_trades, hit_rate, avg_return, sharpe, metrics)
                    VALUES (NOW()::DATE, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        threshold,
                        r["horizon_days"],
                        r["n_trades"],
                        r["hit_rate"],
                        r["avg_return"],
                        r["sharpe"],
                        json.dumps(r.get("detail", [])),
                    ),
                )


def _get_historical_signals(since: date, threshold: int) -> List[Dict]:
    with get_conn() as conn:
        with conn.cursor() as conn_cur:
            conn_cur.execute(
                """
                SELECT ticker, signal_date, score, signal_type, cluster_flag
                FROM signals
                WHERE signal_date >= %s
                  AND (score >= %s OR cluster_flag = TRUE)
                  AND signal_type IN ('BUY', 'CLUSTER_BUY')
                ORDER BY signal_date
                """,
                (since, threshold),
            )
            rows = conn_cur.fetchall()
    return [dict(zip(["ticker", "signal_date", "score", "signal_type", "cluster_flag"], r)) for r in rows]


if __name__ == "__main__":
    print("Running backtest...")
    results = run_backtest(threshold=65, lookback_days=365)
    if results:
        save_backtest_results(results, threshold=65)
        print(f"Saved {len(results)} horizon results.")
    else:
        print("No results to save.")
