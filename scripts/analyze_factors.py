"""
Factor-return correlation analysis for scoring optimization.

Queries the latest backtest run's per-signal detail, joins with signals.score_breakdown,
and prints:
  - Score monotonicity: does higher score → higher excess return?
  - Factor lift: avg excess return when each factor is present vs absent
  - Cap tier and signal type breakdown
  - Recommended weight adjustments

Run locally with DATABASE_URL in env or .env file.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import date as dt, timedelta
from statistics import mean, stdev

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.db.connection import get_conn

ALL_FACTORS = [
    "indirect_purchase",
    "role_cfo", "role_director", "role_chairman", "role_coo", "role_officer", "role_ceo", "role_other",
    "cap_small", "cap_mid", "cap_large", "cap_unknown",
    "value_500k_plus", "value_100k_plus",
    "holdings_increase_30pct", "holdings_increase_15pct", "holdings_increase_5pct",
    "first_purchase_12mo", "sequenced_buying_30d", "prior_purchase_31_365d",
    "near_52wk_low_5pct", "near_52wk_low_10pct",
    "cluster_size_4plus", "cluster_size_5plus", "cluster_size_6plus",
    "fast_filing_0_1d", "fast_filing_2d",
]

CURRENT_WEIGHTS = {
    "indirect_purchase":        -15,
    "role_cfo":                +15,
    "role_director":           +16,
    "role_chairman":             0,
    "role_coo":                +15,
    "role_officer":            +12,
    "role_ceo":                  -5,  # round 4: penalty added
    "role_other":                0,
    "cap_small":               +15,
    "cap_mid":                   0,
    "cap_large":                 0,
    "cap_unknown":              +5,
    "value_500k_plus":           0,   # round 4: removed (was +15; -4.7%/-6.5% lift)
    "value_100k_plus":           0,
    "holdings_increase_30pct":   0,
    "holdings_increase_15pct":   0,
    "holdings_increase_5pct":  +15,   # round 4: raised from +10 (best factor: +9.2%/+9.3%)
    "first_purchase_12mo":     -10,   # round 5: strengthened from -5 (-4.2%/-1.7% lift, n=174)
    "sequenced_buying_30d":    +10,
    "prior_purchase_31_365d":  +15,
    "near_52wk_low_5pct":      +12,
    "near_52wk_low_10pct":      +7,
    "cluster_size_4plus":        0,   # round 4: removed (-5%/-7.6% lift)
    "cluster_size_5plus":        0,   # round 5: removed (-1.5%/-0.3% lift)
    "cluster_size_6plus":        0,   # round 4: removed (-8%/-8.7% lift)
    "fast_filing_0_1d":          0,   # round 4: disabled (-2.5%/-1.1% lift, 61% fire rate)
    "fast_filing_2d":            0,   # round 4: disabled
}


def _parse(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


def _fmt(avg, n):
    if n == 0:
        return "        —"
    return f"{avg:+7.2f}%  n={n:3d}"


def analyze_horizon(horizon: int, detail: list, ticker_signals: dict):
    print(f"\n{'='*70}")
    print(f"  HORIZON: {horizon}d   total signals in detail: {len(detail)}")
    print(f"{'='*70}")

    # Match detail rows to signal score_breakdowns
    # exec_date = signal_date + ~4 days; search ±6 days around signal_date = exec_date-4
    factor_with = defaultdict(list)   # factor present → excess returns
    factor_without = defaultdict(list) # factor absent → excess returns
    score_returns = []                 # (score, excess_return)
    cap_returns = defaultdict(list)
    type_returns = defaultdict(list)
    unmatched = 0

    for d in detail:
        ticker = d.get("ticker")
        exec_date_str = (d.get("exec_date") or "")[:10]
        excess = d.get("excess_return")
        if not ticker or not exec_date_str or excess is None:
            continue

        exec_date = dt.fromisoformat(exec_date_str)
        cap_returns[d.get("cap_tier", "?")].append(excess)
        type_returns[d.get("signal_type", "?")].append(excess)

        # Find the best-matching signal (signal_date ≈ exec_date - 4)
        candidates = ticker_signals.get(ticker, [])
        best, best_diff = None, 20
        for sig in candidates:
            sd = sig["signal_date"]
            diff = abs((exec_date - sd).days - 4)
            if diff < best_diff:
                best_diff = diff
                best = sig
        if best is None or best_diff > 8:
            unmatched += 1
            continue

        score_returns.append((best["score"], excess))
        bd = best["breakdown"]
        present = set(bd.keys())

        for factor in ALL_FACTORS:
            if factor in present and isinstance(bd.get(factor), (int, float)) and bd[factor] != 0:
                factor_with[factor].append(excess)
            else:
                factor_without[factor].append(excess)

    print(f"  Matched: {len(score_returns)}   Unmatched: {unmatched}")

    # ── Score monotonicity ──────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  SCORE MONOTONICITY  (does score predict return?)")
    print(f"{'─'*70}")
    bands = [(0,35),(35,45),(45,50),(50,55),(55,60),(60,65),(65,70),(70,75),(75,80),(80,90),(90,101)]
    for lo, hi in bands:
        subset = [e for s, e in score_returns if lo <= s < hi]
        if subset:
            avg = mean(subset)
            sd = stdev(subset) if len(subset) > 1 else 0
            bar = "█" * max(0, int((avg + 15) / 2))
            print(f"  Score {lo:3d}-{hi:3d}: {_fmt(avg, len(subset))}  σ={sd:5.1f}  {bar}")

    # ── Factor lift table ───────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  FACTOR LIFT: with vs without (current weight → observed lift)")
    print(f"  {'Factor':<28} {'Weight':>6}  {'With':>16}  {'Without':>16}  {'Lift':>8}")
    print(f"{'─'*70}")

    lifts = []
    for factor in ALL_FACTORS:
        w_list = factor_with.get(factor, [])
        wo_list = factor_without.get(factor, [])
        w_avg  = mean(w_list)  if w_list  else None
        wo_avg = mean(wo_list) if wo_list else None
        lift = (w_avg - wo_avg) if (w_avg is not None and wo_avg is not None) else None
        cur_wt = CURRENT_WEIGHTS.get(factor, 0)
        lifts.append((factor, cur_wt, w_avg, len(w_list), wo_avg, len(wo_list), lift))

    lifts.sort(key=lambda x: (x[6] or -999), reverse=True)

    for factor, cur_wt, w_avg, w_n, wo_avg, wo_n, lift in lifts:
        w_str  = _fmt(w_avg, w_n)   if w_avg  is not None else "         —"
        wo_str = _fmt(wo_avg, wo_n) if wo_avg is not None else "         —"
        lift_str = f"{lift:+.2f}%" if lift is not None else "    —"
        flag = "  ◄" if (lift is not None and abs(lift) > 3) else ""
        print(f"  {factor:<28} {cur_wt:>+6}  {w_str}  {wo_str}  {lift_str:>8}{flag}")

    # ── Cap tier breakdown ──────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  CAP TIER BREAKDOWN")
    print(f"{'─'*70}")
    for cap in ["small", "mid", "large", "unknown"]:
        rets = cap_returns.get(cap, [])
        if rets:
            print(f"  {cap:<10}: {_fmt(mean(rets), len(rets))}")

    # ── Signal type breakdown ───────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  SIGNAL TYPE BREAKDOWN")
    print(f"{'─'*70}")
    for t in ["BUY", "CLUSTER_BUY"]:
        rets = type_returns.get(t, [])
        if rets:
            print(f"  {t:<15}: {_fmt(mean(rets), len(rets))}")

    return lifts, score_returns


def main():
    print("=== Factor-Return Correlation Analysis ===")
    print("Loading latest backtest run...\n")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT horizon_days, metrics
                FROM backtest_runs
                WHERE run_date = (SELECT MAX(run_date) FROM backtest_runs)
                  AND horizon_days IN (60, 90, 30, 180)
                ORDER BY horizon_days
            """)
            rows = cur.fetchall()

    if not rows:
        print("No backtest data found. Run 'python3 scripts/run_backtest.py' first.")
        return

    # Collect all tickers across all horizons to fetch signals in one shot
    all_tickers = set()
    horizon_details = {}
    for horizon, metrics_raw in rows:
        metrics = _parse(metrics_raw)
        detail = metrics.get("detail", [])
        horizon_details[horizon] = detail
        for d in detail:
            if d.get("ticker"):
                all_tickers.add(d["ticker"])

    print(f"Fetching score breakdowns for {len(all_tickers)} tickers...")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.ticker, s.signal_date, s.score, s.score_breakdown, s.signal_type
                FROM signals s
                WHERE s.ticker = ANY(%s)
                  AND s.signal_type IN ('BUY', 'CLUSTER_BUY')
                ORDER BY s.ticker, s.signal_date
            """, (list(all_tickers),))
            sig_rows = cur.fetchall()

    ticker_signals = defaultdict(list)
    for ticker, signal_date, score, breakdown_raw, sig_type in sig_rows:
        sd = signal_date if isinstance(signal_date, dt) else dt.fromisoformat(str(signal_date)[:10])
        ticker_signals[ticker].append({
            "signal_date": sd,
            "score": score,
            "breakdown": _parse(breakdown_raw),
            "signal_type": sig_type,
        })

    all_lifts = {}
    all_scores = {}
    for horizon in [60, 90]:
        detail = horizon_details.get(horizon, [])
        if not detail:
            print(f"No detail data for {horizon}d horizon.")
            continue
        lifts, score_returns = analyze_horizon(horizon, detail, ticker_signals)
        all_lifts[horizon] = lifts
        all_scores[horizon] = score_returns

    # ── Cross-horizon summary + weight recommendations ──────────────────────
    if len(all_lifts) == 2:
        print(f"\n{'='*70}")
        print(f"  WEIGHT RECOMMENDATIONS  (based on 60d + 90d avg lift)")
        print(f"  Current weights → Suggested weights")
        print(f"{'='*70}")

        lift_60 = {f: lift for f, _, _, _, _, _, lift in all_lifts[60]}
        lift_90 = {f: lift for f, _, _, _, _, _, lift in all_lifts[90]}

        for factor in ALL_FACTORS:
            l60 = lift_60.get(factor)
            l90 = lift_90.get(factor)
            if l60 is None and l90 is None:
                continue
            avg_lift = mean([x for x in [l60, l90] if x is not None])
            cur_wt = CURRENT_WEIGHTS.get(factor, 0)
            # Rough suggestion: if lift is much higher/lower than expected, nudge weight
            # Positive lift ≫ current weight → increase; negative lift → reduce or remove
            if avg_lift > 5:
                suggestion = "increase weight"
            elif avg_lift > 2:
                suggestion = "weight looks good"
            elif avg_lift > 0:
                suggestion = "slight positive, ok"
            elif avg_lift > -2:
                suggestion = "neutral/weak — consider reducing"
            else:
                suggestion = "NEGATIVE lift — consider removing or penalizing"
            l60_s = f"{l60:+.1f}%" if l60 is not None else "  —  "
            l90_s = f"{l90:+.1f}%" if l90 is not None else "  —  "
            print(f"  {factor:<28} wt={cur_wt:>+3}  60d={l60_s}  90d={l90_s}  → {suggestion}")

    print(f"\nDone.\n")


if __name__ == "__main__":
    main()
