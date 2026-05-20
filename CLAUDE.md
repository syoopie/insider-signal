# Insider Signal System — Claude Reference

This document is for Claude Code. It captures every non-obvious design decision,
known data quirk, and operational detail so you can work on this codebase without
re-deriving context.

---

## What This System Does

Ingests SEC Form 4 insider purchase disclosures daily, scores them with a
research-backed model, and surfaces actionable buy signals via a Streamlit
dashboard and Telegram alerts. Runs at zero cost indefinitely.

**Research basis (non-negotiable):**
- Lakonishok & Lee (2001): small-cap insider buys → +7.4% abnormal at 12 months
- Cohen, Malloy & Pomorski (2012): opportunistic trades → 82 bps/month alpha; routine ≈ 0
- Jeng, Metrick & Zeckhauser (2003): purchase portfolio → ~6% annualized alpha
- TipRanks CFO study: CFO (21.5%) > Director (20.7%) > Officer (19.8%) > CEO (19.3%)
- Cluster research: 3+ insiders buying ≈ 2× alpha of single insider buy

---

## Stack

| Layer | Service | Notes |
|---|---|---|
| Scheduler | GitHub Actions | Daily ingest weekdays 11am UTC; weekly backtest Sundays 12pm UTC |
| Database | Neon PostgreSQL (free tier) | 0.5 GB limit; direct URL for Actions, pooled URL for Streamlit |
| Dashboard | Streamlit Community Cloud | `dashboard/app.py`; uses pooled connection string |
| Alerts | Telegram Bot API | BUY and CLUSTER_BUY signals only |
| Data | SEC EDGAR (free, public) | 10 req/sec hard limit; we use 8 for ingest, 3 for bootstrap |

**All credentials live in GitHub Actions Secrets and Streamlit Secrets — never in code.**
The repo is public. `.env` is gitignored and local-only.

---

## Project Layout

```
src/
  db/
    connection.py       # get_conn() context manager (yields psycopg2 conn)
    schema.sql          # CREATE TABLE + idempotent ALTER TABLE migrations
  ingest/
    edgar.py            # EDGAR API client — rate-limited, User-Agent required
    parser.py           # Form 4 XML → normalized dict; role classifier
    store.py            # write_filing(), backfill_routine_flags()
    common.py           # log(), phase(), setup_log_tee(), fmt_elapsed()
  signals/
    scorer.py           # score_transaction(), classify_signal()
    cluster.py          # detect_clusters_for_ticker()
    formatter.py        # build_evidence() — assembles the JSONB evidence blob
  market/
    prices.py           # get_market_data() — YF chart API; get_price_change_pct()
  backtest/
    engine.py           # run_backtest(), save_backtest_results()

scripts/
  bootstrap.py          # One-time: load historical Form 4s. Supports --start/--end/--force
  backfill_signals.py   # Rescore all stored transactions into signals table. --days/--force
  refresh_market_caps.py# Batch update companies.market_cap via EDGAR frames + YF price
  run_ingest.py         # Daily ingest entry point (called by GitHub Actions)
  run_backtest.py       # Weekly backtest entry point
  update_tickers.py     # Refresh S&P500 + Russell2000 ticker universe

dashboard/
  app.py                # Streamlit dashboard — 4 sections (signals, positions, backtest, history)

.github/workflows/
  daily_ingest.yml      # Weekdays 11am UTC + workflow_dispatch
  weekly_backtest.yml   # Sundays 12pm UTC — runs refresh_market_caps then backtest
  keep_alive.yml        # 2x/day pings Streamlit to prevent cold-start lag
  bootstrap.yml         # Manual only — runs bootstrap.py via workflow_dispatch
```

---

## Database Schema (key points)

### `transactions`
- `transaction_code`: only `P` (open-market purchase) is ever scored for buy signals
- `is_10b51`: hard disqualifier — pre-arranged 10b5-1 plan trades have zero alpha
- `is_direct`: `FALSE` means bought through LLC/trust/family entity — less conviction,
  penalized −8 pts in scoring, excluded from cluster participant count
- `is_routine`: pre-computed at ingest time. `TRUE` = routine trader (same calendar month
  ≥2 of 3 prior years) → disqualified. `NULL` = legacy row not yet computed (falls back
  to live calculation). Stored on the row so the 2-year data pruning doesn't corrupt it.

### `signals`
- `(ticker, signal_date)` is UNIQUE — one signal row per ticker per day
- `score_breakdown`: JSONB map of factor_name → points
- `evidence`: JSONB with full transaction detail, cluster info, company context
- `cluster_flag`: TRUE if this signal triggered a cluster (≥3 eligible insiders in 14d)
- `signal_type`: `BUY` (score ≥65), `WATCH` (45–64), `CLUSTER_BUY`, `LOW`

### `backtest_runs`
- One row per (run_date, horizon_days, threshold)
- `save_backtest_results()` **deletes existing rows for (run_date, threshold) before
  inserting** — prevents accumulation of duplicates when backtest re-runs on same day
- `metrics` JSONB: `distribution`, `by_score_band`, `by_cap_tier`, `by_signal_type`,
  `risk`, `iwm_small_cap`, `cluster_5064`, `rolling_hit_rate_90d`, `detail`
- Old rows (before the enhanced engine) stored metrics as a JSON array. The dashboard
  `_parse_metrics()` helper handles both formats gracefully.

### `companies`
- `cap_tier`: `'small'` (<$2B), `'mid'` ($2B–$10B), `'large'` (>$10B), `'unknown'`
- Refreshed weekly by `refresh_market_caps.py` (EDGAR bulk frames + YF price)
- 1,271/1,897 companies currently have real market cap; 631 remain unknown
- **Unknown-cap is scored as small-cap (+15)** — safe default given Russell 2000 universe

---

## Scoring Logic (`src/signals/scorer.py`)

Only transaction_code `P` is ever scored. Disqualifiers (return immediately, score=0):
1. `is_10b51 = TRUE` → pre-arranged plan
2. `total_value < $2,000` → trivially small (DRIP/401k noise)
3. `is_routine = TRUE` → same calendar month in ≥2 of 3 preceding years (Cohen et al.)

**Score factors (0–100):**

| Factor | Points | Notes |
|---|---|---|
| `indirect_purchase` | −8 | `is_direct=FALSE` |
| `role_cfo` | +20 | Highest research return |
| `role_director` | +16 | |
| `role_chairman` | +14 | |
| `role_coo` | +12 | |
| `role_officer` | +12 | |
| `role_ceo` | +10 | Counterintuitively lowest |
| `role_other` | +6 | |
| `cap_small` | +15 | <$2B |
| `cap_unknown` | +5 | Conservative default — backtesting showed unknown-cap includes large-caps (FI, KO, BDX) missed by market cap refresh; lower score reduces false BUY promotions |
| `cap_mid` | +8 | $2B–$10B |
| `cap_large` | +0 | >$10B |
| `value_500k_plus` | +12 | total_value ≥ $500K |
| `value_100k_plus` | +8 | total_value ≥ $100K |
| `holdings_increase_30pct` | +15 | shares bought / shares before ≥ 30% |
| `holdings_increase_15pct` | +10 | 15–30% |
| `holdings_increase_5pct` | +5 | 5–15% |
| `first_purchase_12mo` | +10 | No prior P in last 365 days |
| `sequenced_buying_30d` | +8 | Second purchase within 30 days |
| `near_52wk_low_5pct` | +12 | Price within 5% of 52wk low |
| `near_52wk_low_10pct` | +7 | Price within 10% of 52wk low |

`first_purchase_12mo` and `sequenced_buying_30d` are mutually exclusive by definition.

**Signal classification (`classify_signal()`):**
- `cluster_flag=True` + `avg(participant_scores) ≥ 35` + (`tight_cluster OR max_score ≥ 50`) + cap_tier ≠ `large` → `CLUSTER_BUY`
- `cluster_flag=True` + cap_tier = `large` → `WATCH` regardless of score (0% hit rate at 90d, -16% avg excess)
- `cluster_flag=True` + avg ≥ 35 + loose cluster + max_score < 50 → `WATCH` (weak loose cluster)
- `cluster_flag=True` + avg < 35 → `WATCH` (very weak cluster, surfaced but no alert)
- `score ≥ 65` → `BUY`
- `score ≥ 45` → `WATCH`
- otherwise → `LOW`

The cluster uses the **average** of all participant scores, not the max. Three directors
each scoring 42 → avg=42 ≥ 35 → qualifies, but also needs tight_cluster OR max_score ≥ 50.
Empirical testing: loose clusters with individual score <50 averaged -5% excess at 90d.

---

## Cluster Detection (`src/signals/cluster.py`)

**Eligibility filters (all must pass to count toward the 3-insider threshold):**
1. `transaction_code = 'P'`
2. `is_10b51 = FALSE`
3. `is_direct = TRUE` — indirect purchases excluded (fund partners filing separately)
4. `total_value ≥ $25,000` — filters DRIP/401k automated contributions
5. **Identical-block filter**: if ≥3 buyers share the same (shares, price, date), the
   entire block is removed. IPO/PIPE allocations (e.g. MBX: 12 insiders × 500,000 shares
   at $16.00 exactly) are not independent buying decisions.
6. **Same-price offering filter**: if ≥3 buyers share the same (price, date) but different
   share amounts, the block is also removed. This catches IPO/secondary offerings where
   insiders receive different allocations at the same fixed offer price (BKV at $18.00,
   COSO at $21.50, BETA at $34.00 — all confirmed underperformers in backtesting).

**Window:** 14 calendar days rolling.
**Sub-flags stored in evidence.cluster:**
- `executive_cluster`: True if CFO/CEO/COO/Chairman is among the participants
- `tight_cluster`: True if ≥3 distinct insiders bought within a 5-day window

**`backfill_signals.py` mirrors all these filters** in its in-memory `_detect_cluster()`.
If you change cluster.py, update backfill_signals.py to match.

---

## Known Data Quirks

**DRIP/401k contamination (mostly filtered now):**
- WERN fires every quarter with 5–7 "insiders" buying identical fractional shares
  (e.g. 139.873 shares × 4 times/year). The $25K minimum and identical-block filter
  catch most of these.
- EPAM fires every April 30 and October 31 — same pattern.
- GABC fires monthly.

**CMPO-style fund partnerships:**
- A single fund (e.g. "Resolute Compo Holdings LLC") files separately for each partner.
  They all buy identical shares at the same price on the same day. The `is_direct=FALSE`
  exclusion and identical-block filter handle this.

**is_routine NULL rows:**
- ~2,917 P transactions have `is_routine=NULL` (pre-schema rows). They fall back to live
  routine calculation from `prior_purchases` in scorer.py. This is correct behavior.
- The `backfill_routine_flags()` function in store.py can recompute these in bulk.

**April 2024 gap:**
- Coverage starts 2024-04-03. The first few weeks have thin data (~643 filings in April
  2024 vs 3,712 in May 2024). Normal — bootstrap started mid-month.

**October 2025 gap (filled):**
- Was nearly empty due to a broken `forms=4,4/A` query. Fixed by running bootstrap
  `--start 2025-10-01 --end 2025-10-31 --force`. Now has 2,489 filings.

---

## Backtest Methodology

- **Signal date** = `filed_date + 1` (never transaction_date — that's look-ahead bias)
- **Execution date** = signal_date + 3 calendar days (realistic fill lag)
- **Benchmark**: SPY for all signals; IWM for small-cap signals (SPY is wrong benchmark
  for Russell 2000 names)
- **Delisted stocks**: treated as −50% excess return (survivorship bias correction)
- **Horizons**: 30, 60, 90, 180 days
- **Signals included**: `score ≥ 65` OR `cluster_flag = TRUE`
- `CLUSTER_BUY` signals with score 50–64 are tracked separately in `cluster_5064`
- The excess-return chart on the dashboard shows a bar chart until ≥3 run_dates exist,
  then switches to a time-series line chart automatically

---

## Operational Scripts

**After any scoring logic change:**
```bash
python3 scripts/backfill_signals.py --days 730 --force
```
This rescores all 2 years of stored transactions. Takes ~8 minutes.

**After market cap data goes stale:**
```bash
python3 scripts/refresh_market_caps.py
```
Uses SEC EDGAR bulk frames (one HTTP call → 4,238 companies) + YF chart per ticker.
Takes ~30 minutes for ~1,900 companies. `--force` re-fetches even populated rows.
Also runs automatically every Sunday before the backtest.

**To fill a historical gap:**
```bash
python3 scripts/bootstrap.py --start YYYY-MM-DD --end YYYY-MM-DD --force
```
Use `--force` to re-fetch XML for filings already stored (fixes corrupted windows).

**To re-run the backtest locally:**
```bash
python3 scripts/run_backtest.py
```
`save_backtest_results()` deletes the current day's rows before inserting, so re-runs
are safe.

---

## Current DB State (as of 2026-05-20)

- **Filings**: 153,602 (2024-04-03 → 2026-05-19)
- **P transactions (non-10b5-1)**: 12,676
- **Signals**: ~2,574 total (70 BUY, 349 CLUSTER_BUY, 2,155 WATCH + LOW)
- **Companies with market_cap**: 1,488 / 2,119 (631 still unknown → scored as small)
- **is_routine breakdown**: 406 routine / 10,788 opportunistic / 2,917 NULL (legacy)

---

## GitHub Actions Gotchas

- Workflows **disable after 60 days of no repo activity**. The daily ingest job commits
  `last_run.txt` with a UTC timestamp to keep the repo active.
- Neon **scales to zero** when idle. Never rely on pg_cron; GitHub Actions is the only
  scheduler.
- Streamlit **sleeps after ~12 hours**. `keep_alive.yml` pings it 2x/day.
- The backtest workflow runs `refresh_market_caps.py` first, then `run_backtest.py`.

---

## Dashboard Sections

1. **Active Signals** — current BUY/CLUSTER_BUY/WATCH with full evidence cards
2. **Open Positions** — signals within the hold horizon that haven't expired
3. **Backtest Performance** — hit rate metrics, box plot distribution, stratification tabs
   (score band, cap tier, signal type), rolling 90d hit rate, risk panel, cluster 50–64
4. **Insider History** — per-ticker transaction history with is_routine labels

`_parse_metrics(raw)` normalizes the metrics JSONB column from both old (list) and new
(dict) formats. Apply it before any `.get()` call on metrics data.

---

## What Not To Do

- **Never** commit `.env`, `secrets.toml`, or any credential files. Repo is public.
- **Never** use `get_conn()` outside a `with` block — the context manager handles commit/rollback/close.
- **Never** change the cluster threshold (14d, 3 insiders) or BUY threshold (65) without
  re-running the full backfill — every signal in the DB would be stale.
- **Never** add `ORDER BY RANDOM()` or non-deterministic queries to backfill — idempotency depends on deterministic processing order.
- **Don't** call `get_market_data()` in the backfill script. It fetches live prices which
  don't represent historical cap tiers. Use `tx.get("cap_tier")` from the companies join.
