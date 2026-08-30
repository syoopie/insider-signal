"""
Build the labelled dataset that scoring research is fitted on.

One row per insider purchase-day, with point-in-time price context as of the
trade and forward excess returns at every horizon. This is the thing the current
model has never had. Weights today are fitted on ~350 priced signals that the
model itself selected; this produces every eligible purchase, including the ones
that classify LOW and never reach the signals table.

Reads the purchase rollup and the local price panel. Writes parquet. No network,
no writes to the database, and it runs in seconds, which is the point — an
experiment you can repeat cheaply is an experiment you will actually repeat.

Sector-relative and size-matched benchmark legs are not here yet; SPY and IWM
are. They arrive with the Tier 2 features in the improvement plan.

Usage:
  python3 scripts/build_research_dataset.py
  python3 scripts/build_research_dataset.py --days 730
  python3 scripts/build_research_dataset.py --out data/prices/dataset.parquet
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from collections import defaultdict

from src.backtest.engine import EXEC_LAG_DAYS, HORIZONS
from src.db.connection import get_conn
from src.db.purchases import purchase_rollup
from src.db.store import get_discount_reference, get_history_start
from src.ingest.common import setup_log_tee, log, phase, fmt_elapsed
from src.market.features import price_context, price_on, window_return
from src.market.panel import PANEL_PATH, load_panel
from src.research.protocol import PRIMARY_HORIZON
from src.research.tier1 import (
    averaging_down,
    cluster_intensity,
    insider_track_record,
    net_insider_demand,
    value_vs_own_history,
)
from src.signals.batch import priors_before_window, score_purchase

setup_log_tee("build_research_dataset")

DEFAULT_OUT = PANEL_PATH.parent / "research_dataset.parquet"
DEFAULT_DAYS = 730

SPY, IWM = "SPY", "IWM"

# Mirrors the scorer's hard floors so `eligible` means what the scorer means.
MIN_VALUE = 2_000
MAX_VALUE = 1_000_000_000


def _load_purchases() -> list[dict]:
    """
    Every P purchase ever stored, at the rollup's grain. No eligibility filter.

    Deliberately unwindowed. The timing factors look back a full year from the
    trade, so a purchase near the start of the output window needs the year
    before it to be visible or `prior_purchase_31_365d` cannot fire. Loading
    only the output window silently stripped 15 points from 82 signals and made
    the dataset disagree with the pipeline. The backfill loads a ticker's whole
    history for the same reason; this has to match it.
    """
    sql = purchase_rollup()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _load_sales() -> pd.DataFrame:
    """
    Open-market sales, for the net-demand feature.

    91,296 of these are stored and the scorer has never read one, so the system
    uses half of a buy-minus-sell result. No rollup here: only the issuer, the
    disclosure date and the dollar amount matter, and summing raw fills is the
    right aggregation for a firm-level balance.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.cik, f.filed_date, t.insider_name,
                       COALESCE(t.total_value, 0) AS total_value
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                WHERE t.transaction_code = 'S'
                  AND t.is_10b51 IS NOT TRUE
            """)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)


def _eligible(row: dict) -> bool:
    value = row.get("total_value")
    return (
        not row.get("is_10b51")
        and not row.get("is_routine")
        and value is not None
        and MIN_VALUE <= float(value) <= MAX_VALUE
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    args = parser.parse_args()

    t0 = time.time()

    phase("LOAD")
    panel = load_panel(args.panel)
    log(f"Price panel: {len(panel):,} symbols from {args.panel}")
    for bench in (SPY, IWM):
        if bench not in panel:
            raise SystemExit(f"Panel is missing {bench}; excess returns cannot be computed.")

    purchases = _load_purchases()
    all_purchases = purchases
    log(f"Purchases: {len(purchases):,} insider-days stored")

    # `first_purchase_12mo` is only meaningful where the database covers the
    # whole year before the trade, so the scorer needs to know where coverage
    # starts. Omitting it charged the penalty for the ingest start date.
    history_start = get_history_start()
    log(f"History starts {history_start}")

    # Keyed off every stored purchase, not just the window, so timing factors
    # can see a full year behind a trade at the window's leading edge.
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for p in all_purchases:
        by_ticker[p["ticker"]].append(p)

    phase("SCORE AND LABEL")
    spy_series, iwm_series = panel[SPY], panel[IWM]
    rows = []
    n_no_panel = 0

    for p in purchases:
        ticker = p["ticker"]
        tx_date = p["transaction_date"]
        filed = p["filed_date"]
        series = panel.get(ticker)
        if series is None:
            n_no_panel += 1

        exec_date = filed + timedelta(days=1 + EXEC_LAG_DAYS)

        row = {
            "cik": p["cik"],
            "ticker": ticker,
            "company_name": p.get("company_name"),
            "insider_name": p["insider_name"],
            "insider_role": p.get("insider_role"),
            "role_category": p.get("role_category"),
            "cap_tier": p.get("cap_tier"),
            "transaction_date": tx_date,
            "filed_date": filed,
            "exec_date": exec_date,
            "filing_lag_days": (filed - tx_date).days,
            "is_direct": p.get("is_direct"),
            "is_10b51": p.get("is_10b51"),
            "is_routine": p.get("is_routine"),
            "shares": _f(p.get("shares")),
            "shares_after": _f(p.get("shares_after")),
            "total_value": _f(p.get("total_value")),
            "price_per_share": _f(p.get("price_per_share")),
            "eligible": _eligible(p),
            "in_panel": series is not None,
        }

        shares = row["shares"]
        after = row["shares_after"]
        if shares and after and after > shares:
            row["pct_holdings_increase"] = shares / (after - shares) * 100.0
        else:
            row["pct_holdings_increase"] = None

        # Score every purchase, including the ones that classify LOW. The
        # backfill discards those before writing, which is why the model has
        # never been fitted against its own negative class.
        priors = priors_before_window(by_ticker[ticker], p["insider_name"], filed)
        # The same trailing reference the backfill ranks against. Without it this
        # falls back to the fixed table and every score here quietly disagrees
        # with the stored signal, which is precisely what
        # verify_scoring_parity.py exists to catch.
        result = score_purchase(p, priors, history_start,
                                get_discount_reference(filed))
        if result is None:
            row["score"] = None
            row["scorer_disqualified"] = None
            row["disqualify_reason"] = "not_a_purchase"
            row["breakdown"] = {}
        else:
            row["score"] = result["score"]
            row["scorer_disqualified"] = bool(result["disqualified"])
            breakdown = result["breakdown"]
            row["disqualify_reason"] = (
                next(iter(breakdown), None) if result["disqualified"] else None
            )
            row["breakdown"] = breakdown if not result["disqualified"] else {}

        ctx = price_context(series, tx_date)
        row.update({f"tx_{k}": v for k, v in ctx.items()})

        # What the stock did between the trade and its disclosure. The insider
        # bought at price_per_share; the market saw the filing days later.
        px_at_filing = price_on(series, filed)
        row["px_close_at_filed"] = px_at_filing
        px_paid = row["price_per_share"]
        row["price_deviation_pct"] = (
            (px_at_filing - px_paid) / px_paid * 100.0
            if px_at_filing and px_paid else None
        )

        for h in HORIZONS:
            exit_date = exec_date + timedelta(days=h)
            tkr = window_return(series, exec_date, exit_date)
            spy = window_return(spy_series, exec_date, exit_date)
            iwm = window_return(iwm_series, exec_date, exit_date)

            row[f"status_{h}d"] = tkr.status
            row[f"ret_{h}d"] = tkr.pct
            row[f"spy_{h}d"] = spy.pct
            row[f"iwm_{h}d"] = iwm.pct
            row[f"excess_spy_{h}d"] = (
                tkr.pct - spy.pct if tkr.ok and spy.ok else None
            )
            row[f"excess_iwm_{h}d"] = (
                tkr.pct - iwm.pct if tkr.ok and iwm.ok else None
            )
            row[f"exit_in_future_{h}d"] = exit_date > date.today()

        rows.append(row)

    frame = _explode_breakdown(pd.DataFrame(rows))

    phase("TIER 1 FEATURES")
    sales = _load_sales()
    log(f"Sale rows for net-demand: {len(sales):,} across {sales['cik'].nunique():,} issuers")
    label = f"excess_spy_{PRIMARY_HORIZON}d"
    for name, block in (
        ("net insider demand", net_insider_demand(sales, frame)),
        ("insider track record", insider_track_record(frame, label)),
        ("averaging down", averaging_down(frame)),
        ("cluster intensity", cluster_intensity(frame)),
        ("value vs own history", value_vs_own_history(frame)),
    ):
        frame = pd.concat([frame, block], axis=1)
        log(f"  {name:<24} {len(block.columns)} columns")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out, index=False, compression="zstd")

    phase("SUMMARY")
    log(f"Rows: {len(frame):,}   file: {args.out} "
        f"({args.out.stat().st_size / 1e6:.1f} MB)")
    log(f"  eligible: {int(frame['eligible'].sum()):,}   "
        f"ticker missing from panel: {n_no_panel:,}")
    log(f"  distinct tickers: {frame['ticker'].nunique():,}   "
        f"distinct insiders: {frame['insider_name'].nunique():,}")
    log(f"  transaction_date range: {frame['transaction_date'].min()} → "
        f"{frame['transaction_date'].max()}")

    log("\n  labelled rows per horizon (eligible, exit in the past):")
    elig = frame[frame["eligible"]]
    for h in HORIZONS:
        done = elig[~elig[f"exit_in_future_{h}d"]]
        labelled = done[f"excess_spy_{h}d"].notna().sum()
        log(f"    {h:>4}d  labelled={labelled:>6,}  of {len(done):>6,} completed  "
            f"mean_excess={done[f'excess_spy_{h}d'].mean():+7.2f}%  "
            f"median={done[f'excess_spy_{h}d'].median():+7.2f}%")

    log("\n  return status breakdown (eligible, 90d):")
    done90 = elig[~elig["exit_in_future_90d"]]
    for status, n in done90["status_90d"].value_counts().items():
        log(f"    {status:<12} {n:>6,}")

    log(f"\n  price context coverage (eligible): "
        f"{int(elig['tx_px_close'].notna().sum()):,} of {len(elig):,} have a close at the trade date")
    log(f"  with a full year of bars behind the trade: "
        f"{int((elig['tx_n_bars_before'] >= 252).sum()):,}")

    scored = frame[frame["score"].notna() & ~frame["scorer_disqualified"].fillna(True)]
    log(f"\n  scored purchases: {len(scored):,}   "
        f"disqualified by the scorer: {int(frame['scorer_disqualified'].fillna(False).sum()):,}")
    if len(scored):
        log(f"  score: min={scored['score'].min():.0f}  median={scored['score'].median():.0f}  "
            f"max={scored['score'].max():.0f}  mean={scored['score'].mean():.1f}")
        log("  score decile vs 90d excess return (the direct test of whether score ranks):")
        done = scored[~scored["exit_in_future_90d"] & scored["excess_spy_90d"].notna()]
        if len(done) > 50:
            decile = pd.qcut(done["score"].rank(method="first"), 10, labels=False)
            for d, grp in done.groupby(decile):
                log(f"    d{int(d) + 1:>2}  n={len(grp):>5}  score {grp['score'].min():>3.0f}-"
                    f"{grp['score'].max():>3.0f}  mean={grp['excess_spy_90d'].mean():+7.2f}%  "
                    f"median={grp['excess_spy_90d'].median():+7.2f}%")

    log("\n  disqualification reasons:")
    for reason, n in frame["disqualify_reason"].value_counts().items():
        log(f"    {reason:<24} {n:>6,}")

    factor_cols = sorted(c for c in frame.columns if c.startswith("f_"))
    log(f"\n  factor columns: {len(factor_cols)}  ({', '.join(c[2:] for c in factor_cols)})")

    log(f"\nCompleted in {fmt_elapsed(time.time() - t0)}")


def _explode_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    """
    One `f_<factor>` indicator column per scoring factor: 1 if it fired, else 0.

    Indicators, not the signed point values. A factor worth -10 points takes the
    value -10 when it fires and 0 when it does not, so "higher" means "did not
    fire" and every coefficient on a penalty reads backwards. That inverts the
    interpretation of `indirect_purchase`, `role_ceo` and `first_purchase_12mo`
    at once. The points are already in the scorer's weight table; what the
    regression needs is whether the factor applied.

    The factor set is discovered from the data rather than hard-coded, which is
    how analyze_factors.py's ALL_FACTORS list went stale.
    """
    names = sorted({k for bd in frame["breakdown"] for k in bd})
    for name in names:
        frame[f"f_{name}"] = [1.0 if name in bd else 0.0 for bd in frame["breakdown"]]
    return frame.drop(columns=["breakdown"])


def _f(v):
    return float(v) if v is not None else None


if __name__ == "__main__":
    main()
