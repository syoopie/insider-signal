"""
Backtest engine: validates signal quality on historical data.

Methodology (bias-controlled):
  - Signal date = filing date + 1 day (point-in-time, no look-ahead)
  - Execution date = signal date + 3 calendar days (realistic fill lag)
  - Benchmark: SPY return over same window
  - Delisted stocks: yfinance returns empty / last price → treated as -50% loss
  - Parameters (threshold=65, cluster_window=14d) set from literature, not tuned

Enhanced metrics (stored in backtest_runs.metrics JSONB):
  - Score-band stratification: 65–74, 75–84, 85+
  - Cap tier stratification: small, mid, large
  - Role stratification: cfo, director, ceo, officer, other
  - Return distribution: p25, median, p75, max_loss, max_gain
  - Cluster sub-analysis: CLUSTER_BUY 50–64 (normally excluded from BUY threshold)
  - Cluster composition: executive_cluster vs director-only
  - IWM benchmark: for small-cap signals (SPY is wrong benchmark)
  - Risk: % trades losing >20%, max consecutive losses, worst outcome
  - Rolling 90-day hit rate time series (detects alpha decay)

Run weekly via GitHub Actions (scripts/run_backtest.py).
"""

import json
import statistics
import time
from datetime import date, timedelta
from typing import List, Dict, Optional

from src.db.connection import get_conn
from src.market.prices import get_price_change_pct
from src.ingest.common import log, phase, fmt_elapsed


HORIZONS = [30, 60, 90, 180]
SPY_TICKER = "SPY"
IWM_TICKER = "IWM"  # Russell 2000 — correct benchmark for small-cap signals
EXEC_LAG_DAYS = 3   # realistic fill lag after signal date


# ── Metric helpers ──────────────────────────────────────────────────────────

def _percentile(sorted_vals: list, pct: float) -> Optional[float]:
    """pct in 0–100. Returns None if list is empty."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    idx = (pct / 100) * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _group_metrics(returns: list, key: str = "excess_return") -> Optional[dict]:
    """Compute hit_rate, avg, median, p25, p75, n for a subset of returns."""
    if not returns:
        return None
    vals = sorted(r[key] for r in returns)
    n = len(vals)
    hit_rate = sum(1 for v in vals if v > 0) / n * 100
    avg = sum(vals) / n
    return {
        "n": n,
        "hit_rate": round(hit_rate, 1),
        "avg_return": round(avg, 2),
        "median_return": round(_percentile(vals, 50), 2),
        "p25_return": round(_percentile(vals, 25), 2),
        "p75_return": round(_percentile(vals, 75), 2),
        "max_loss": round(min(vals), 2),
        "max_gain": round(max(vals), 2),
    }


def _rolling_hit_rate(returns: list, window_days: int = 90) -> list:
    """
    Compute hit rate on a rolling window of `window_days` calendar days.
    Returns [{date, hit_rate, n}, ...] sorted by date.
    Requires returns to have exec_date field.
    """
    if not returns:
        return []
    sorted_r = sorted(returns, key=lambda r: r.get("exec_date", ""))
    results = []
    for i, r in enumerate(sorted_r):
        anchor = r.get("exec_date", "")
        if not anchor:
            continue
        anchor_d = date.fromisoformat(anchor[:10])
        cutoff = anchor_d - timedelta(days=window_days)
        window = [
            x for x in sorted_r
            if x.get("exec_date") and date.fromisoformat(x["exec_date"][:10]) >= cutoff
            and date.fromisoformat(x["exec_date"][:10]) <= anchor_d
        ]
        if len(window) < 5:
            continue  # too few points to be meaningful
        hr = sum(1 for x in window if x["excess_return"] > 0) / len(window) * 100
        results.append({"date": anchor[:10], "hit_rate": round(hr, 1), "n": len(window)})
    # Deduplicate by date, keep last
    seen = {}
    for item in results:
        seen[item["date"]] = item
    return sorted(seen.values(), key=lambda x: x["date"])


def _max_consecutive_losses(returns: list) -> int:
    """Maximum number of consecutive negative excess returns."""
    if not returns:
        return 0
    sorted_r = sorted(returns, key=lambda r: r.get("exec_date", ""))
    max_run = cur_run = 0
    for r in sorted_r:
        if r["excess_return"] <= 0:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_run = 0
    return max_run


# ── Main backtest ────────────────────────────────────────────────────────────

def run_backtest(threshold: int = 65, lookback_days: int = 365) -> List[Dict]:
    """
    Evaluate all BUY/CLUSTER_BUY signals in the last `lookback_days` days.
    Returns one result dict per horizon (only horizons with completed exits).

    Also runs a separate evaluation of CLUSTER_BUY signals with score 50–64
    (normally excluded by the BUY threshold but valid cluster signals).
    """
    t_start = time.time()
    today = date.today()
    since = today - timedelta(days=lookback_days)

    phase("SIGNAL FETCH")
    log(f"Config: threshold={threshold}, lookback={lookback_days}d, horizons={HORIZONS}")
    log(f"Signal window: {since} → {today}")

    signals = _get_historical_signals(since, threshold)
    # Also fetch CLUSTER_BUY signals with score 50–64 for separate analysis
    cluster_weak = _get_cluster_weak_signals(since)
    log(f"Signals in DB: {len(signals)} (BUY/CLUSTER_BUY ≥{threshold}) + "
        f"{len(cluster_weak)} weak clusters (score 50–64)")

    if not signals:
        log("  No BUY/CLUSTER_BUY signals found for this window.")
        log("  Signals are generated by daily ingest — run it first, then wait")
        log(f"  {min(HORIZONS) + EXEC_LAG_DAYS}+ days for the shortest ({min(HORIZONS)}d) horizon.")
        return []

    by_type: Dict[str, int] = {}
    for s in signals:
        by_type[s["signal_type"]] = by_type.get(s["signal_type"], 0) + 1
    log("  " + "  ".join(f"{t}={n}" for t, n in sorted(by_type.items())))

    sig_dates = [_parse_date(s["signal_date"]) for s in signals if _parse_date(s["signal_date"])]
    oldest = min(sig_dates)
    newest = max(sig_dates)
    log(f"  Date range: {oldest} → {newest}")

    min_age = min(HORIZONS) + EXEC_LAG_DAYS
    first_completable_cutoff = today - timedelta(days=min_age)
    if not any(_parse_date(s["signal_date"]) <= first_completable_cutoff for s in signals):
        first_results_date = oldest + timedelta(days=min_age + 1)
        log(f"  All signals too recent — first results after {first_results_date}")
        return []

    results = []

    for horizon in HORIZONS:
        phase(f"HORIZON {horizon}d")
        t0 = time.time()

        cutoff = today - timedelta(days=horizon + EXEC_LAG_DAYS)
        eligible = [s for s in signals if _parse_date(s["signal_date"]) <= cutoff]
        n_skipped = len(signals) - len(eligible)
        log(f"  {len(eligible)} eligible  ({n_skipped} too recent — need signal_date ≤ {cutoff})")

        if not eligible:
            log("  Skipping horizon — no completed exits yet.")
            continue

        returns = []
        iwm_returns_small = []  # IWM-excess returns for small-cap signals only
        n_no_spy = 0

        for sig in eligible:
            sig_date  = _parse_date(sig["signal_date"])
            exec_date = sig_date + timedelta(days=EXEC_LAG_DAYS)
            exit_date = exec_date + timedelta(days=horizon)
            ticker    = sig["ticker"]

            ticker_ret = get_price_change_pct(ticker, exec_date, exit_date)
            spy_ret    = get_price_change_pct(SPY_TICKER, exec_date, exit_date)

            if spy_ret is None:
                n_no_spy += 1
                log(f"    {ticker:<6}  SPY unavailable {exec_date}→{exit_date} — skipped")
                continue
            if ticker_ret is None:
                ticker_ret = -50.0  # delisted → survivorship bias correction

            excess = ticker_ret - spy_ret
            icon = "✓" if excess > 0 else "✗"
            cap = sig.get("cap_tier") or "?"
            log(f"    {icon} {ticker:<6}  {sig['signal_type']:<12}  score={sig['score']:>3}"
                f"  cap={cap:<5}  tkr={ticker_ret:>+6.1f}%  spy={spy_ret:>+6.1f}%"
                f"  excess={excess:>+6.1f}%")

            row = {
                "ticker": ticker,
                "signal_type": sig["signal_type"],
                "score": sig["score"],
                "cap_tier": sig.get("cap_tier") or "unknown",
                "exec_date": exec_date.isoformat(),
                "ticker_return": round(ticker_ret, 2),
                "spy_return": round(spy_ret, 2),
                "excess_return": round(excess, 2),
            }
            returns.append(row)

            # IWM benchmark for small-cap signals
            if sig.get("cap_tier") == "small":
                iwm_ret = get_price_change_pct(IWM_TICKER, exec_date, exit_date)
                if iwm_ret is not None:
                    iwm_returns_small.append(ticker_ret - iwm_ret)

        if not returns:
            log("  No valid returns — skipping horizon.")
            continue

        n = len(returns)
        excess_vals = sorted(r["excess_return"] for r in returns)
        hit_rate    = sum(1 for v in excess_vals if v > 0) / n * 100
        avg_return  = sum(excess_vals) / n
        median_ret  = _percentile(excess_vals, 50)
        p25_ret     = _percentile(excess_vals, 25)
        p75_ret     = _percentile(excess_vals, 75)

        stdev = statistics.stdev(excess_vals) if len(excess_vals) > 1 else None
        sharpe = (avg_return / stdev) * (252 / horizon) ** 0.5 if stdev and stdev > 0 else None

        elapsed_h = time.time() - t0
        log(f"  ── {horizon}d summary ({fmt_elapsed(elapsed_h)}) ──")
        log(f"  n={n}  hit={hit_rate:.0f}%  avg={avg_return:+.1f}%  "
            f"median={median_ret:+.1f}%  p25={p25_ret:+.1f}%  p75={p75_ret:+.1f}%  "
            f"sharpe={f'{sharpe:.2f}' if sharpe else 'N/A'}")

        # ── Stratification ──────────────────────────────────────────────────

        by_score_band = {
            "35-49": _group_metrics([r for r in returns if 35 <= r["score"] < 50]),
            "50-64": _group_metrics([r for r in returns if 50 <= r["score"] < 65]),
            "65-74": _group_metrics([r for r in returns if 65 <= r["score"] < 75]),
            "75-84": _group_metrics([r for r in returns if 75 <= r["score"] < 85]),
            "85+":   _group_metrics([r for r in returns if r["score"] >= 85]),
        }
        by_cap_tier = {
            t: _group_metrics([r for r in returns if r["cap_tier"] == t])
            for t in ("small", "mid", "large", "unknown")
        }
        by_signal_type = {
            t: _group_metrics([r for r in returns if r["signal_type"] == t])
            for t in ("CLUSTER_BUY", "BUY")
        }

        # Risk metrics
        pct_loss_gt20 = sum(1 for r in returns if r["excess_return"] < -20) / n * 100
        max_consec_losses = _max_consecutive_losses(returns)

        # IWM benchmark (small-cap only)
        iwm_avg_return = round(sum(iwm_returns_small) / len(iwm_returns_small), 2) if iwm_returns_small else None
        if iwm_avg_return is not None:
            iwm_hit = sum(1 for v in iwm_returns_small if v > 0) / len(iwm_returns_small) * 100
            log(f"  Small-cap vs IWM: n={len(iwm_returns_small)}  avg={iwm_avg_return:+.1f}%  hit={iwm_hit:.0f}%")

        # Cluster 50-64 evaluation (signals excluded from the main threshold)
        cluster_weak_eligible = [
            s for s in cluster_weak if _parse_date(s["signal_date"]) <= cutoff
        ]
        cluster_weak_returns = []
        for sig in cluster_weak_eligible:
            sig_date  = _parse_date(sig["signal_date"])
            exec_date = sig_date + timedelta(days=EXEC_LAG_DAYS)
            exit_date = exec_date + timedelta(days=horizon)
            ticker    = sig["ticker"]
            ticker_ret = get_price_change_pct(ticker, exec_date, exit_date)
            spy_ret    = get_price_change_pct(SPY_TICKER, exec_date, exit_date)
            if spy_ret is None or ticker_ret is None:
                continue
            cluster_weak_returns.append({
                "ticker": ticker,
                "score": sig["score"],
                "excess_return": round(ticker_ret - spy_ret, 2),
                "exec_date": exec_date.isoformat(),
            })
        cluster_5064_metrics = _group_metrics(cluster_weak_returns) if cluster_weak_returns else None
        if cluster_5064_metrics:
            log(f"  CLUSTER 50-64: n={cluster_5064_metrics['n']}  "
                f"hit={cluster_5064_metrics['hit_rate']:.0f}%  "
                f"avg={cluster_5064_metrics['avg_return']:+.1f}%")

        rolling_hr = _rolling_hit_rate(returns, window_days=90)

        best  = max(returns, key=lambda r: r["excess_return"])
        worst = min(returns, key=lambda r: r["excess_return"])
        log(f"  Best:  {best['ticker']:<6} {best['excess_return']:>+.1f}%  "
            f"Worst: {worst['ticker']:<6} {worst['excess_return']:>+.1f}%")

        metrics_blob = {
            "distribution": {
                "p25": round(p25_ret, 2),
                "median": round(median_ret, 2),
                "p75": round(p75_ret, 2),
                "max_loss": round(min(excess_vals), 2),
                "max_gain": round(max(excess_vals), 2),
            },
            "by_score_band": by_score_band,
            "by_cap_tier": by_cap_tier,
            "by_signal_type": by_signal_type,
            "risk": {
                "pct_loss_gt20": round(pct_loss_gt20, 1),
                "max_consecutive_losses": max_consec_losses,
                "worst_outcome": round(min(excess_vals), 2),
                "n_no_spy_data": n_no_spy,
            },
            "iwm_small_cap": {
                "n": len(iwm_returns_small),
                "avg_return": iwm_avg_return,
                "hit_rate": round(iwm_hit, 1) if iwm_returns_small else None,
            } if iwm_returns_small else None,
            "cluster_5064": cluster_5064_metrics,
            "rolling_hit_rate_90d": rolling_hr,
            "detail": returns,  # full per-signal return list
        }

        results.append({
            "horizon_days": horizon,
            "n_trades": n,
            "hit_rate": round(hit_rate, 1),
            "avg_return": round(avg_return, 2),
            "median_return": round(median_ret, 2),
            "p25_return": round(p25_ret, 2),
            "p75_return": round(p75_ret, 2),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "iwm_avg_return": iwm_avg_return,
            "metrics": metrics_blob,
        })

    phase("RESULTS SUMMARY")
    if not results:
        log("No horizons produced results — all exits still in the future.")
    else:
        log(f"{'Horizon':>7}  {'Trades':>6}  {'Hit%':>5}  {'AvgExc':>7}  {'Median':>7}  {'Sharpe':>6}")
        log("─" * 50)
        for r in results:
            sharpe_str = f"{r['sharpe']:>6.2f}" if r["sharpe"] is not None else "   N/A"
            log(f"  {r['horizon_days']:>4}d  {r['n_trades']:>6}  {r['hit_rate']:>4.0f}%"
                f"  {r['avg_return']:>+6.1f}%  {r['median_return']:>+6.1f}%  {sharpe_str}")

    log(f"Backtest complete in {fmt_elapsed(time.time() - t_start)}")
    return results


def save_backtest_results(results: List[Dict], threshold: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Replace any existing rows for today + this threshold so re-runs don't accumulate duplicates.
            cur.execute(
                "DELETE FROM backtest_runs WHERE run_date = NOW()::DATE AND threshold = %s",
                (threshold,),
            )
            for r in results:
                cur.execute(
                    """
                    INSERT INTO backtest_runs
                        (run_date, threshold, horizon_days, n_trades,
                         hit_rate, avg_return, median_return, p25_return, p75_return,
                         sharpe, iwm_avg_return, metrics)
                    VALUES (NOW()::DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        threshold,
                        r["horizon_days"],
                        r["n_trades"],
                        r["hit_rate"],
                        r["avg_return"],
                        r.get("median_return"),
                        r.get("p25_return"),
                        r.get("p75_return"),
                        r["sharpe"],
                        r.get("iwm_avg_return"),
                        json.dumps(r.get("metrics", {})),
                    ),
                )
    log(f"Saved {len(results)} horizon result(s) to backtest_runs.")


def _get_historical_signals(since: date, threshold: int) -> List[Dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker, s.signal_date, s.score, s.signal_type,
                       s.cluster_flag, c.cap_tier
                FROM signals s
                LEFT JOIN companies c ON c.ticker = s.ticker
                WHERE s.signal_date >= %s
                  AND (s.score >= %s OR s.cluster_flag = TRUE)
                  AND s.signal_type IN ('BUY', 'CLUSTER_BUY')
                ORDER BY s.signal_date
                """,
                (since, threshold),
            )
            rows = cur.fetchall()
    return [
        dict(zip(["ticker", "signal_date", "score", "signal_type", "cluster_flag", "cap_tier"], r))
        for r in rows
    ]


def _get_cluster_weak_signals(since: date) -> List[Dict]:
    """CLUSTER_BUY signals with score 50–64 (excluded from the main threshold)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker, s.signal_date, s.score, s.signal_type,
                       s.cluster_flag, c.cap_tier
                FROM signals s
                LEFT JOIN companies c ON c.ticker = s.ticker
                WHERE s.signal_date >= %s
                  AND s.signal_type = 'CLUSTER_BUY'
                  AND s.score BETWEEN 50 AND 64
                ORDER BY s.signal_date
                """,
                (since,),
            )
            rows = cur.fetchall()
    return [
        dict(zip(["ticker", "signal_date", "score", "signal_type", "cluster_flag", "cap_tier"], r))
        for r in rows
    ]


def _parse_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None
