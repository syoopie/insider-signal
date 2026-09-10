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

Four label families come out of it: excess over SPY, over IWM, over the SPDR
fund for the issuer's own SIC division, and the SPY excess divided by realised
volatility at the trade date. The last two exist because the raw label is
heteroscedastic and because industry is the confound the placebo control could
not remove. `src/research/protocol.LABEL_FAMILIES` names them.

Neon retains 48 months, which is less history than the research needs, so the
same dataset builds from `data/form4/` — the DERA archive, back to 2016 — under
`--source archive`. Only the four database-coupled inputs differ; the scoring
loop, the labels and the features are one implementation either way.

Usage:
  python3 scripts/build_research_dataset.py
  python3 scripts/build_research_dataset.py --source archive
  python3 scripts/build_research_dataset.py --out data/prices/dataset.parquet
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Callable, Optional, Sequence

import pandas as pd

from collections import defaultdict

from src.backtest.engine import EXEC_LAG_DAYS, HORIZONS
from src.db.connection import get_conn
from src.db.purchases import purchase_rollup
from src.db.store import get_discount_reference, get_history_start
from src.ingest.common import setup_log_tee, log, phase, fmt_elapsed
from src.market.features import price_context, price_on, window_return
from src.market.panel import PANEL_PATH, load_panel
from src.research import archive
from src.research.protocol import PRIMARY_HORIZON
from src.research.sectors import sector_etf
from src.research.tier1 import (
    averaging_down,
    cluster_intensity,
    insider_roster,
    insider_track_record,
    net_insider_demand,
    value_vs_own_history,
)
from src.signals.batch import priors_before_window, score_purchase
from src.signals.discount import reference_window

setup_log_tee("build_research_dataset")

DEFAULT_OUT = PANEL_PATH.parent / "research_dataset.parquet"
ARCHIVE_OUT = PANEL_PATH.parent / "research_dataset_archive.parquet"

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


def _load_sic() -> dict[str, str]:
    """CIK to SIC code, for the sector-relative label."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cik, sic_code FROM companies WHERE sic_code IS NOT NULL")
            return dict(cur.fetchall())


@dataclass(frozen=True)
class Inputs:
    """
    Everything the build needs that depends on where the rows came from.

    Four things are coupled to the database and nothing else is. Naming them as
    one structure keeps `main()` identical under both sources, which is the
    point: a `if source == "archive"` threaded through the scoring loop would be
    a second implementation of how a purchase is scored, and the archive exists
    to be compared against the database, not to disagree with it.
    """
    purchases: list[dict]
    sales: pd.DataFrame
    sic: dict[str, str]
    history_start: Optional[date]
    discount_reference: Callable[[date], Sequence[float]]


def _from_db() -> Inputs:
    return Inputs(
        purchases=_load_purchases(),
        sales=_load_sales(),
        sic=_load_sic(),
        history_start=get_history_start(),
        discount_reference=get_discount_reference,
    )


def _from_archive(panel: dict) -> Inputs:
    """
    The same five inputs out of `data/form4/`, with `companies` still supplying SIC.

    SIC is not in the archive and the `companies` table is the only source of
    it, so it stays a database read in both modes. It is a property of the
    issuer rather than of the filing, so reading today's value for a 2016
    purchase is the same approximation the database build already makes.
    """
    conn = archive.connect()
    priced = archive.priced_purchases(conn, panel)
    return Inputs(
        purchases=_records(priced),
        sales=archive.sales(conn),
        sic=_load_sic(),
        history_start=archive.history_start(conn),
        discount_reference=partial(reference_window, archive.discount_series(priced)),
    )


def _records(frame: pd.DataFrame) -> list[dict]:
    """
    Rollup rows as the dicts the scoring loop expects, with two archive-only
    hazards removed at the boundary.

    DuckDB returns dates as datetime64, and `pd.Timestamp > datetime.date`
    raises instead of comparing, which is what the exit-in-future check asks of
    every row. And `is_10b51` and `is_routine` are nullable booleans out of the
    archive, so `_eligible`'s `not` would raise on pd.NA rather than read as
    "undetermined, so not disqualifying".
    """
    frame = frame.copy()
    for column in ("transaction_date", "filed_date"):
        frame[column] = pd.to_datetime(frame[column]).dt.date

    records = frame.to_dict("records")
    for row in records:
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
    return records


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
    parser.add_argument("--source", choices=("db", "archive"), default="db")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    args = parser.parse_args()

    from_archive = args.source == "archive"
    if args.out is None:
        args.out = ARCHIVE_OUT if from_archive else DEFAULT_OUT
    if from_archive and args.out.resolve() == DEFAULT_OUT.resolve():
        raise SystemExit(
            f"Refusing to write {DEFAULT_OUT} from the archive: every published "
            f"number rests on that file being the database's build. Leave --out "
            f"unset for {ARCHIVE_OUT}."
        )

    t0 = time.time()

    phase("LOAD")
    panel = load_panel(args.panel)
    log(f"Price panel: {len(panel):,} symbols from {args.panel}")
    for bench in (SPY, IWM):
        if bench not in panel:
            raise SystemExit(f"Panel is missing {bench}; excess returns cannot be computed.")

    inputs = _from_archive(panel) if from_archive else _from_db()
    purchases = inputs.purchases
    log(f"Purchases: {len(purchases):,} insider-days from the {args.source}")

    # `first_purchase_12mo` is only meaningful where the source covers the whole
    # year before the trade, so the scorer needs to know where coverage starts.
    # Omitting it charged the penalty for the ingest start date.
    history_start = inputs.history_start
    log(f"History starts {history_start}")

    sic_by_cik = inputs.sic
    log(f"SIC codes: {len(sic_by_cik):,} companies")

    # Keyed off every stored purchase, not just the window, so timing factors
    # can see a full year behind a trade at the window's leading edge.
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for p in purchases:
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
                                inputs.discount_reference(filed))
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

        row["sic_code"] = sic_by_cik.get(p["cik"])
        etf = sector_etf(row["sic_code"])
        row["sector_etf"] = etf
        etf_series = panel.get(etf) if etf else None
        vol = row.get("tx_vol_21d")

        for h in HORIZONS:
            exit_date = exec_date + timedelta(days=h)
            tkr = window_return(series, exec_date, exit_date)
            spy = window_return(spy_series, exec_date, exit_date)
            iwm = window_return(iwm_series, exec_date, exit_date)
            sector = window_return(etf_series, exec_date, exit_date)

            row[f"status_{h}d"] = tkr.status
            row[f"ret_{h}d"] = tkr.pct
            row[f"spy_{h}d"] = spy.pct
            row[f"iwm_{h}d"] = iwm.pct
            excess = tkr.pct - spy.pct if tkr.ok and spy.ok else None
            row[f"excess_spy_{h}d"] = excess
            row[f"excess_sector_{h}d"] = (
                tkr.pct - sector.pct if tkr.ok and sector.ok else None
            )
            # Realised volatility is annualised and in percent, as the excess
            # return is, so the ratio is unitless and comparable across quintiles.
            row[f"excess_vol_{h}d"] = (
                excess / vol if excess is not None and vol and vol > 0 else None
            )
            row[f"excess_iwm_{h}d"] = (
                tkr.pct - iwm.pct if tkr.ok and iwm.ok else None
            )
            row[f"exit_in_future_{h}d"] = exit_date > date.today()

        rows.append(row)

    frame = _explode_breakdown(pd.DataFrame(rows))

    phase("TIER 1 FEATURES")
    sales = inputs.sales
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

    # Needs cluster_n_buyers, so it cannot join the loop above.
    roster = insider_roster(sales, frame)
    frame = pd.concat([frame, roster], axis=1)
    log(f"  {'insider roster':<24} {len(roster.columns)} columns")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out, index=False, compression="zstd")

    phase("SUMMARY")
    log(f"Rows: {len(frame):,}   file: {args.out} "
        f"({args.out.stat().st_size / 1e6:.1f} MB)")
    log(f"  eligible: {int(frame['eligible'].sum()):,}   "
        f"ticker missing from panel: {n_no_panel:,}")
    log(f"  distinct tickers: {frame['ticker'].nunique():,}   "
        f"distinct insiders: {frame['insider_name'].nunique():,}")
    n_sic = int(frame["sic_code"].notna().sum())
    log(f"  with a SIC code: {n_sic:,} of {len(frame):,} rows "
        f"({n_sic / len(frame) * 100:.1f}%)")
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
