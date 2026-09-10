# Insider Signal System — Claude Reference

This document is the authoritative reference for AI agents working on this codebase.
Read it in full before touching any code.

---

## The dashboard is a Next.js app in `web/` (migrated 2026-08-29)

The Streamlit app is gone. `web/` is a Next.js 16 app on Vercel, read-only against
the same Neon database.

- Read **`web/AGENTS.md` before editing anything in `web/`** — Next.js 16 has
  breaking changes and ships its own docs in `node_modules/next/dist/docs/`.
- `web/README.md` covers local dev, env vars and deployment.
- `docs/web-migration.md` records how the migration was done and why.
- The Python pipeline (`src/`, `scripts/`, `.github/workflows/`, the Neon schema,
  Telegram alerts) was **unchanged** by the migration, apart from one additive
  addition: `companies.sic_description` and `scripts/backfill_sic.py`.

**`web/lib/db.ts` imports `server-only`.** A client component that imports a
*value* from anything under `web/lib/queries/` pulls the database client and
every SQL string into the browser bundle. That happened twice during the
migration; it is now a build error. Import types from query modules freely —
`import type` is erased — but put shared runtime helpers somewhere neutral
(`lib/confidence.ts`, `lib/transaction-codes.ts`, `lib/signal-filters.ts` all
exist for exactly this reason).

---

## Auto-Commit and Push Policy

**Always automatically commit and push every change you make.**

- Stage only the specific files you changed (never `git add -A` or `git add .`)
- Write a concise commit message describing what changed and why
- Push to `origin main` immediately after committing
- Do not ask for confirmation — just commit and push

Example:
```bash
uv run ruff check src scripts tests   # CI lints before it tests; a stray import fails the run
uv run pytest -q
git add scripts/run_backtest.py
git commit -m "Increase LOOKBACK_DAYS from 365 to 730 for 2-year backtest window"
git push
```

`tests.yml` runs `ruff check src scripts tests` and *then* `pytest`, so an unused
import fails the workflow with the suite never having run. `.git/hooks/pre-push`
runs both locally for the same reason. It is not version controlled, so a fresh
clone needs it reinstalled or the two commands run by hand.

---

## Quick Orientation

**The three files that govern system behavior end-to-end:**
1. `src/signals/scorer.py` — scoring model (what makes a signal good/bad)
2. `src/signals/cluster.py` — cluster detection (3+ insiders = CLUSTER_BUY)
3. `src/backtest/engine.py` — backtest engine (how signal quality is measured)

**The golden rule — any change to `src/signals/` must be followed by:**
```bash
uv run pytest -q                                             # < 1 sec — catches obvious breaks
uv run python scripts/backfill_signals.py --days 730 --force  # ~8 min
uv run python scripts/run_backtest.py                         # ~30 min
git add src/signals/ scripts/backfill_signals.py
git commit -m "..."
git push
```
Skipping the backfill or backtest leaves the DB stale and the backtest chart
misleading. `cluster.py`'s `cluster_from_transactions` is shared by the live path
and `backfill_signals.py` — there is no second copy to keep in sync.

**Where to find things:**

| What | Where |
|---|---|
| The scoring model | `src/signals/discount.py` → `KNOTS`, `discount_score()` |
| Scoring eligibility / disqualifiers | `src/signals/scorer.py` → `score_transaction()` |
| Point-in-time price context | `src/market/context.py`; stored on `transactions` at ingest |
| Signal classification thresholds | `src/signals/scorer.py` → `classify_signal()` |
| Cluster eligibility filters | `src/signals/cluster.py` → `detect_clusters_for_ticker()` |
| Evidence blob structure | `src/signals/formatter.py` → `build_evidence()` |
| Backtest signal query | `src/backtest/engine.py` → `_get_historical_signals()` |
| Backtest metrics structure | `src/backtest/engine.py` → `metrics_blob` dict near bottom of `run_backtest()` |
| DB connection | `src/db/connection.py` → `get_conn()` (always use as context manager) |
| Schema / migrations | `src/db/schema.sql` |
| Write a filing to DB | `src/db/store.py` → `write_filing()` |
| Write a signal to DB | `src/db/store.py` → `save_signal()` or `batch_save_signals()` |
| EDGAR API client | `src/ingest/edgar.py` → `_get()`, rate-limited to 8 req/sec |
| Form 4 XML parser | `src/ingest/parser.py` → `parse_form4()` |
| Role classification | `src/ingest/parser.py` → `classify_role()` |
| Dashboard pages | `web/app/*/page.tsx`; data in `web/lib/queries/`, charts in `web/components/charts.tsx` |
| GitHub Actions config | `.github/workflows/` — 4 workflow files |
| Backtest lookback window | `scripts/run_backtest.py` → `LOOKBACK_DAYS = 730` |
| The ruler for rankings | `scripts/hillclimb.py`; hypotheses in `src/research/candidates.py` |
| The ruler for exclusions | `scripts/gates.py`; hypotheses in `src/research/gates.py` |
| What the ruler can resolve | `walkforward.minimum_detectable_effect`; `hillclimb.py --mde` |
| Benchmark legs a purchase is charged against | `src/research/protocol.py` → `LABEL_FAMILIES`; `--label` on both rulers |
| SIC code to sector fund | `src/research/sectors.py` → `SIC_RANGES` |
| Form 4 history beyond Neon retention | `scripts/build_form4_archive.py` + `src/ingest/dera.py` → `data/form4/` |
| Purchases out of the archive | `src/research/archive.py` → `connect()`, `purchases()`; DuckDB over the parquet |
| Price context for archive rows | `archive.with_price_context()`; calls `market.context.context_from_series`, the ingest path's own function |
| Proof the archive rolls up like the DB | `scripts/verify_archive_rollup.py` |

**Key thresholds (do not change without re-running full backfill + backtest):**
- The score is `pct_below_52wk_high` as a percentile of the last 30 days of filings
  (`src/signals/discount.py` + `store.get_discount_reference`); a *fixed* cutoff was
  tried first and gave away more than half the effect
- BUY: score ≥ 90, meaning the top decile of discount, where the whole measured effect sits
- WATCH: score 70–89 OR a cluster that missed its bar
- CLUSTER_BUY: ≥3 direct insiders, 14d window, avg score ≥80, tight OR max_score ≥85;
  large-cap downgraded to WATCH by the callers
- A purchase with no 52-week high (under 200 bars of history) scores 0 and is never alerted
- Backtest lookback: 730 days

---

## What This System Does

Ingests SEC Form 4 insider purchase disclosures daily, scores them with a
research-backed model, and surfaces actionable buy signals via a Next.js
dashboard and Telegram alerts. Runs at zero cost indefinitely.

**What the eligibility rules rest on:**
- Cohen, Malloy & Pomorski (2012): opportunistic trades → 82 bps/month alpha; routine ≈ 0.
  This is why 10b5-1 and routine same-month buyers are disqualified outright.
- Jeng, Metrick & Zeckhauser (2003): purchase portfolio → ~6% annualized alpha. Purchases
  are the only transaction type scored.

**What the ranking rests on** is this system's own walk-forward test, not the literature.
See section 7b of `docs/scoring-improvement-plan.md`. Lakonishok & Lee's small-cap cut and
the cluster literature were both measured here and both *lower* the result, so neither
carries weight in the score any more. The TipRanks role ordering is indistinguishable from
zero on this data.

---

## Stack

| Layer | Service | Notes |
|---|---|---|
| Scheduler | GitHub Actions | Daily ingest weekdays 11am UTC; weekly backtest Sundays 12pm UTC |
| Database | Neon PostgreSQL (free tier) | 0.5 GB limit; direct URL for Actions, HTTP driver for the web app |
| Dashboard | Next.js 16 on Vercel | `web/`; Root Directory must be `web`. Read-only |
| Alerts | Telegram Bot API | BUY and CLUSTER_BUY only (BUY alerts were silently never sent until 2026-08-29). Fans out to `telegram_subscribers`, maintained by the webhook in `web/` |
| Data | SEC EDGAR (free, public) | 10 req/sec hard limit; we use 8 for ingest, 3 for bootstrap |

**All credentials live in GitHub Actions Secrets and Vercel environment variables — never in code.**
The repo is public. `.env` is gitignored and local-only.

---

## Data Flow (End to End)

```
SEC EDGAR XML
  → scripts/run_ingest.py                    daily, GitHub Actions
  → edgar.fetch_form4_index / fetch_filing_xml
  → parser.parse_form4                       {issuer, owner, transactions[]}
  → market.context                           pct_below_52wk_high, stored on the row
  → store.write_filing                       upserts companies, form4_filings, transactions
                                             and precomputes is_routine
  → signals.batch.score_window               shared with backfill_signals.py
  → signals.cluster.cluster_from_transactions
  → signals.formatter.build_evidence         the JSONB evidence blob
  → store.batch_save_signals                 upserts signals, dedupes within the cooldown
  → alerts.telegram                          BUY and CLUSTER_BUY only

weekly, Sundays:
  → scripts/refresh_market_caps.py  then  scripts/run_backtest.py
  → backtest.engine.run_backtest             excess returns vs SPY and IWM
  → save_backtest_results                    replaces today's rows for this run_label only

web/ (Next.js on Vercel)                     reads every table, writes none
  except web/app/api/telegram/webhook/route.ts, which writes telegram_subscribers
```

**`web/` never writes to the database, with one deliberate exception.** The pages and
everything under `lib/` stay read-only. The Telegram webhook route is the second writer,
isolated on purpose, and owns its own client that must not be reused.

**Two paths score purchases and they share every definition.** `run_ingest.py` and
`backfill_signals.py` both go through `signals/batch.py` for scoring,
`cluster_from_transactions` for clusters and `db/purchases.py` for the rollup. There is no
second copy of any of them to keep in sync, and each was extracted after a drifted copy
caused a real bug.

---

## Project Layout

```
src/
  config.py             # loads .env once; database_url() / telegram_credentials()
  db/
    connection.py       # get_conn() — psycopg2 context manager; handles commit/rollback/close
    store.py            # write_filing(), save_signal(), batch_save_signals(), prune_old_data()
    schema.sql          # CREATE TABLE + idempotent ALTER TABLE migrations (run this to init DB)
  ingest/
    edgar.py            # EDGAR API client — rate-limited, User-Agent required, tenacity retries
    parser.py           # Form 4 XML → normalized dict; classify_role() keyword matcher
    dera.py             # SEC quarterly Form 3/4/5 datasets → archive rows. Research only,
                        #   the daily pipeline never imports it.
    common.py           # log(), phase(), setup_log_tee(), fmt_elapsed() — shared logging utils
  research/             # Offline only. The pipeline never imports this package.
    archive.py          # data/form4/ parquet presented as the tables PURCHASE_ROLLUP_SQL
                        #   reads, so the archive and the DB share one rollup definition
    walkforward.py      # The frozen ruler: folds, rank_ic, selection_alpha, class_alpha,
                        #   permutation_alpha, minimum_detectable_effect
    candidates.py       # Ranking hypotheses
    gates.py            # Exclusion hypotheses
    protocol.py         # Splits, LABEL_FAMILIES, drift guard
    sectors.py          # SIC code to SPDR sector fund
  signals/
    constants.py        # BUY_SCORE / WATCH_SCORE / cluster cutoffs — the classification thresholds
    scorer.py           # score_transaction(), classify_signal() — the scoring model
    cluster.py          # cluster_from_transactions() + detect_clusters_for_ticker() — cluster detection
    formatter.py        # build_evidence() — assembles the JSONB evidence blob stored in signals
  market/
    prices.py           # get_price_change_pct() — YF chart API; get_market_data() for cap/52wk
  backtest/
    engine.py           # run_backtest(), save_backtest_results(), _get_historical_signals()
  alerts/
    telegram.py         # send_signal_alert(), send_error() — Telegram Bot API

scripts/                # see scripts/README.md for the full when-to-run table
  run_ingest.py         # Daily ingest entry point (GitHub Actions)
  run_backtest.py       # Weekly backtest entry point. LOOKBACK_DAYS = 730
  bootstrap.py          # One-time: load historical Form 4s. Args: --start, --end, --force
  backfill_signals.py   # Rescore all stored P transactions → signals table. Args: --days, --force
  refresh_market_caps.py# 3-pass cap refresh: EDGAR us-gaap → DEI → per-company API → YF price
  update_tickers.py     # Refresh S&P500 + Russell2000 ticker universe in companies table
  backfill_sic.py       # Fill companies.sic_code/sic_description from EDGAR submissions API
  analyze_factors.py    # Factor-return correlation report (read-only)
  hillclimb.py          # The ruler for rankings. --mde prints what it can resolve
  gates.py              # The ruler for exclusions. Spends every row, not a decile
  build_form4_archive.py# Form 4 history into data/form4/ parquet, from DERA quarterly zips
  verify_form4_archive.py# Prove the archive matches the DB on the shared window
  verify_archive_rollup.py# Prove DuckDB rolls purchases up the way Postgres does
  dev/start.{ps1,sh,bat}# Launch the Next.js dashboard in web/ locally

tests/                  # pytest, no DB — scorer, cluster, parser, formatter

pyproject.toml          # package + deps (uv); uv.lock is the lockfile

web/                    # Next.js 16 dashboard (Vercel). See web/README.md and web/AGENTS.md.
  app/                  # Routes: / /backtest /clusters /sectors /ticker /how-it-works
  app/api/telegram/webhook/route.ts  # The one write path — see "Key constraint" above
  components/           # UI. Anything under components/ may be bundled for the browser.
  lib/db.ts             # Neon HTTP client, read-only, `server-only`
  lib/queries/          # One typed module per concern; JSONB parsed at this boundary
  lib/types.ts          # zod schemas for evidence / score_breakdown / backtest metrics

.github/workflows/
  daily_ingest.yml      # Weekdays 11am UTC + workflow_dispatch; busts the web cache after
  weekly_backtest.yml   # Sundays 12pm UTC — refresh_market_caps then run_backtest
  bootstrap.yml         # Manual only — workflow_dispatch triggers bootstrap.py
  tests.yml             # Every push to main and every PR — ruff, then pytest
```

---

## Database Schema

### `companies`
```
cik         TEXT PRIMARY KEY     — zero-stripped CIK from EDGAR
ticker      TEXT                 — exchange ticker (may be NULL for foreign filers)
name        TEXT                 — company name from EDGAR
sic_code    TEXT                 — SIC industry code; filled by scripts/backfill_sic.py, not by ingest
sic_description TEXT             — EDGAR's readable industry label; drives /sectors
market_cap  BIGINT               — shares_outstanding × current_price (refreshed weekly)
cap_tier    TEXT                 — 'small' (<$2B), 'mid' ($2B–$10B), 'large' (>$10B), 'unknown'
updated_at  TIMESTAMPTZ
```

### `form4_filings`
```
id               SERIAL PRIMARY KEY
accession_number TEXT UNIQUE     — EDGAR accession number (e.g. 0001234567-24-000123)
cik              TEXT → companies.cik
filed_date       DATE            — date EDGAR received the filing; copied into evidence.filed_date,
                                   which is what the backtest keys exec_date off
period_date      DATE            — date the transaction occurred
fetched_at       TIMESTAMPTZ
```

### `transactions`
```
id               SERIAL PRIMARY KEY
filing_id        INT → form4_filings.id ON DELETE CASCADE
insider_name     TEXT
insider_role     TEXT            — raw title string from XML (e.g. "Chief Financial Officer")
role_category    TEXT            — normalized: 'cfo','ceo','coo','chairman','director','officer','other'
transaction_date DATE            — date of the transaction (from Form 4 Table I)
transaction_code TEXT            — P=open-market buy, S=sale, A=award, M=option exercise, etc.
shares           NUMERIC
price_per_share  NUMERIC
total_value      NUMERIC         — shares × price_per_share
shares_after     NUMERIC         — total holdings after transaction (used for pct_increase calc)
is_10b51         BOOLEAN         — pre-arranged 10b5-1 plan trade → hard disqualifier
is_direct        BOOLEAN         — FALSE = bought through LLC/trust/family entity
is_routine       BOOLEAN/NULL    — pre-computed at ingest; NULL = legacy row (falls back to live calc)
```
Only `transaction_code = 'P'` (open-market purchase) is ever scored for signals.
Non-P transactions are stored but ignored by scorer, backfill, and backtest.

**Never read `transactions` directly for scoring — use `src/db/purchases.py`.**
One row is one broker fill, not one decision: a single $3.75M purchase in the
stored data arrived as ~40 rows at ~40 prices. Separately, a 4/A amendment
restates transactions under a new accession number, so the same purchase can
exist twice. `purchase_rollup()` picks the newest filing per (issuer, insider,
date, code, ownership form) and totals that filing's rows, giving a
value-weighted `price_per_share`. All three call sites (`run_ingest.py`,
`cluster.detect_clusters_for_ticker`, `backfill_signals._bulk_load_transactions`)
go through it so they cannot drift. The old `DISTINCT ON (insider_name,
transaction_date, transaction_code)` kept one arbitrary fill and hid $4.1B of
purchase value, 33% of the total. Direct and indirect stay separate rows —
different holdings, not tranches of one order.

**Scoring windows key off `filed_date`, not `transaction_date`.** A Form 4 may
disclose a trade made months earlier; windowing on the trade date meant such
filings were stored and then never scored by anything. 4.8% of purchases landed
in that hole, 178 of them direct and over $25k. `filed_date` is also what the
backtest already keys `exec_date` off, so this is the point-in-time consistent
choice. `signal_date` remains the purchase date (see "Signal dating").

### `signals`
```
id              SERIAL PRIMARY KEY
ticker          TEXT
signal_date     DATE            — the purchase date (tx_rows[0].transaction_date), NOT filed_date+1.
                                  Look-ahead is avoided in the backtest, not here: exec_date is
                                  derived from evidence.filed_date. See "Signal dating" below.
score           INT             — 0–100
signal_type     TEXT            — 'BUY' (≥90), 'WATCH' (70–89 or a weak cluster), 'CLUSTER_BUY', 'LOW'
cluster_flag    BOOLEAN         — TRUE if ≥3 direct insiders bought in 14d window
score_breakdown JSONB           — {factor_name: points} e.g. {"role_cfo": 20, "cap_small": 15}
evidence        JSONB           — full detail: insiders[], cluster{}, company context, filed_date
alerted         BOOLEAN         — TRUE once Telegram alert sent (prevents re-alerting on upsert)
created_at      TIMESTAMPTZ

UNIQUE: (ticker, signal_date)   — one signal row per ticker per day
```

**Signal dating — read this before touching dates anywhere.**

`signal_date` is the **purchase date**, set from `tx_rows[0]["transaction_date"]`
in both `run_ingest.py` and `backfill_signals.py`. It is *not* `filed_date + 1`.
Roughly 80% of stored signals therefore have `signal_date != filed_date + 1`, and
about half sit *before* their filing date, because a Form 4 is filed up to two
business days after the trade (sometimes far later).

That is not look-ahead bias, because nothing trades on `signal_date`.
`engine.py` derives `exec_date` from `evidence.filed_date + 1 + EXEC_LAG_DAYS`
and only falls back to `signal_date` when `filed_date` is absent — which no
stored row currently is. **If you ever make the backtest key off `signal_date`,
you introduce look-ahead bias.** The dashboard groups by `signal_date` on
purpose: "when did the insider buy" is the useful axis for triage.

**`evidence` JSONB structure** (key fields referenced in dashboard):
```json
{
  "filed_date": "2025-01-15",
  "signal_date": "2025-01-16",
  "company_name": "Acme Corp",
  "insiders": [
    {"name": "...", "role_raw": "...", "transaction_date": "...",
     "shares_bought": 10000, "price": 12.50, "total_value": 125000,
     "pct_increase": 22.5}
  ],
  "cluster": {
    "is_cluster": true,
    "insider_count": 3,
    "tight_cluster": true,
    "executive_cluster": false
  },
  "near_52wk_low": true,
  "pct_above_52wk_low": 3.2,
  "price_52wk_low": 11.80,
  "research_basis": ["CFO purchase: highest research return (TipRanks)", ...]
}
```

### `backtest_runs`
```
id             SERIAL PRIMARY KEY
run_date       DATE            — date the backtest script ran (NOT the signal dates evaluated)
run_label      TEXT            — 'scheduled' for the weekly job; anything else is a research
                                 run. Only 'scheduled' rows reach the dashboard.
threshold      INT             — score threshold used (always 65)
horizon_days   INT             — hold horizon: 30, 60, 90, or 180
n_trades       INT             — number of signals evaluated for this horizon
hit_rate       NUMERIC         — % of signals with positive excess return
avg_return     NUMERIC         — mean excess return vs SPY (%)
median_return  NUMERIC         — median excess return (more robust than mean)
p25_return     NUMERIC         — 25th percentile (downside floor)
p75_return     NUMERIC         — 75th percentile (upside)
sharpe         NUMERIC         — information ratio vs SPY, annualized on calendar days
                                  (name is legacy; there is no risk-free leg)
iwm_avg_return NUMERIC         — avg excess return vs IWM for small-cap signals only
metrics        JSONB           — full stratified breakdown (see below)
created_at     TIMESTAMPTZ
```

**`metrics` JSONB structure** (key fields used in dashboard):
```json
{
  "distribution": {"p25": -3.1, "median": 2.4, "p75": 11.2, "max_loss": -63.1, "max_gain": 88.4},
  "by_score_band": {"65-74": {...}, "75-84": {...}, "85+": {...}},
  "by_cap_tier":   {"small": {...}, "mid": {...}, "large": {...}, "unknown": {...}},
  "by_signal_type":{"BUY": {...}, "CLUSTER_BUY": {...}},
  "risk": {"pct_loss_gt20": 12.3, "max_consecutive_losses": 5, "worst_outcome": -63.1},
  "iwm_small_cap": {"n": 45, "avg_return": 3.2, "hit_rate": 58.0},
  "cluster_5064":  {"n": 12, "hit_rate": 55.0, "avg_return": 4.1, "median_return": 2.8},
  "rolling_hit_rate_90d": [{"date": "2025-01-01", "hit_rate": 54.2, "n": 23}, ...],
  "detail": [
    {"ticker": "XYZ", "signal_type": "BUY", "score": 72, "cap_tier": "small",
     "exec_date": "2024-06-15", "ticker_return": 18.3, "spy_return": 5.1, "excess_return": 13.2},
    ...
  ]
}
```
`detail` is the per-signal return list — the dashboard avg-return chart is built from
`exec_date` in this field, NOT from `run_date`. This gives the full 730-day coverage.

**`save_backtest_results()` behavior**: deletes all rows where
`run_date = TODAY AND threshold = 65 AND run_label = <label>` before inserting. Safe to
re-run on the same day. Historical runs accumulate indefinitely.

**`run_label` exists so a second run on the same day cannot destroy the first.** The delete
used to key on `(run_date, threshold)` alone, which kept the weekly job idempotent but meant
a research re-run silently overwrote the baseline it was supposed to be compared against.
`scripts/run_backtest.py --label <name>` writes its own rows. **The dashboard and the
freshness bar read `run_label = 'scheduled'` only**, so anything that queries `backtest_runs`
for display must filter on it or an experiment will leak onto the site.

### `telegram_subscribers`
```
chat_id     BIGINT PRIMARY KEY   — Telegram chat id: a person's DM or a group
chat_type   TEXT                 — 'private' | 'group' | 'supergroup'
title       TEXT                 — group title, or the person's name/username
active      BOOLEAN              — FALSE on /unsubscribe or the bot being removed
joined_at   TIMESTAMPTZ
updated_at  TIMESTAMPTZ
```
Written only by `web/app/api/telegram/webhook/route.ts` (`/subscribe`, `/start`,
`/unsubscribe`, `/stop`, and Telegram's `my_chat_member` update for the bot being
added to or removed from a group). Read only by
`src/db/store.get_active_telegram_subscribers()`, which `src/alerts/telegram._send()`
fans every alert out to. `scripts/seed_telegram_subscriber.py` inserts a
`TELEGRAM_CHAT_ID` once, so a fresh deploy does not go dark before anyone has
subscribed. That variable is local-only as of 2026-09-08 and is no longer a
GitHub secret; the workflows pass `TELEGRAM_BOT_TOKEN` and nothing else, because
`_send()` reads the token and gets every recipient from the table.

---

## Scoring Logic

[`docs/scoring.md`](docs/scoring.md) is the account of the model and why it has one
factor. This section is the rules an agent must not break.

**Three rules govern any scoring work, and each was learned by breaking it.**

1. **Ask what the ruler can resolve before believing a result.** `hillclimb.py --mde`
   prints it, currently 3.4 to 3.9pp of selection alpha for a ranking. Every insider
   feature ever tested landed between +0.3 and +3.5pp, so those runs could not tell a real
   effect from zero. A candidate under the resolution reports `BELOW RESOLUTION`, not a
   failure. In the same run `noise` scored +2.19 at t=+2.15, which is why that distinction
   is not pedantry.
2. **Ask about exclusions before rankings.** `scripts/gates.py` measures whether dropping a
   class raises what is left. It spends every row instead of a 37-row decile, so it
   resolves 0.2 to 0.8pp. That is also the shape the evidence says the Form 4 has: the
   placebo control says the filing supplies the median while insider attributes fail to
   order the discounted set.
3. **Do not add a factor only one path can compute.** Store its input at ingest instead.
   The deleted 52-week-low factors fired only in the live path, so the same purchase scored
   up to 12 points apart depending on which entry point saw it, and they compared against
   *today's* low rather than the low as of the trade.

Hypotheses go in `src/research/candidates.py` and `src/research/gates.py`, never in the
harness. Changing `src/research/walkforward.py` invalidates every number it has printed.
Do not change a weight without re-running `scripts/build_price_panel.py`,
`build_research_dataset.py`, then `scripts/hillclimb.py`.

### Hard disqualifiers (checked in order, early-exit with score 0)

1. `transaction_code != 'P'` — not an open-market purchase
2. `is_10b51 = TRUE` — pre-arranged plan, zero alpha (Cohen et al.)
3. `total_value < $2,000` — DRIP, fractional and payroll noise
4. `is_routine = TRUE`, or a live calc showing ≥2 same-month purchases in 3 prior years

### The score is one factor

`score = discount_score(transactions.pct_below_52wk_high, reference)` — how far below its
52-week high the stock sat on the purchase date, as a percentile among purchases disclosed
in the preceding 30 days (`store.get_discount_reference`). 0 to 100, monotone, no other
term.

- Below 120 reference purchases the purchase is **unranked and scores 0**. It does *not*
  fall back to the fixed table: over 18 months the four picks that came from the fallback
  averaged −34.07pp against the ranked picks' +15.59pp.
- The reference must be *relative*. A fixed cutoff does not select a fixed fraction, and
  the first version fired on 2.0% of one month's purchases and 23.7% of another's.
- **`backfill_signals.py --days 730 --force` zeroes the oldest week of signals.**
  `prune_old_data` deletes the filings a purchase would be ranked against, so
  `get_discount_reference` cannot reach 120 rows at the leading edge. Correct, and still a
  surprise. `verify_scoring_parity.py` excludes that edge for the same reason.
- Every other factor (`role_*`, `cap_*`, `holdings_increase_5pct`, `indirect_purchase`,
  `sequenced_buying_30d`, `prior_purchase_31_365d`, `first_purchase_12mo`,
  `first_purchase_unverifiable`) is still emitted into `score_breakdown` **at 0 points**,
  because the dashboard displays them. They do not rank. Timing factors remain mutually
  exclusive, one per signal.
- **Scores are a pure function of stored data.** Nothing in `score_transaction` reads a
  live price. `pct_below_52wk_high` is fetched once at ingest by `src/market/context.py`.
  The trailing reference is point-in-time: it holds only filings dated on or before the
  purchase being scored, so a future filing cannot change a past score.

### Signal classification (`classify_signal()`)

```
cluster_flag=True:
    avg(participant_scores) >= 80 AND (tight_cluster OR max_score >= 85) → CLUSTER_BUY
    otherwise                                                            → WATCH
no cluster:
    score >= 90                  → BUY
    score >= 70                  → WATCH
    score <  70                  → LOW
```

The cluster bar uses the **average** of participant scores, so it asks whether the group as
a whole was buying weakness. Cluster size alone promotes nothing: inside the most
discounted third, the number of cluster buyers points the wrong way at −4.53pp, t=−1.85.

**The large-cap downgrade is not in `classify_signal()`.** Both callers (`run_ingest.py`
and `backfill_signals.py`) apply it right after the call: `CLUSTER_BUY` + `cap_tier ==
'large'` → `WATCH`, on a measured 0% hit rate at 90d. Anything that classifies signals must
do the same or it will disagree with the stored data.

---

## Cluster Detection

**File**: `src/signals/cluster.py`. `cluster_from_transactions(txs, as_of_date)` is
the rule; `detect_clusters_for_ticker(ticker, as_of_date)` loads the rows and calls it.

### Eligibility filters (ALL must pass to count toward the 3-insider threshold):
1. `transaction_code = 'P'`
2. `is_10b51 = FALSE`
3. `is_direct = TRUE` — indirect purchases excluded
4. `total_value >= $25,000` — filters DRIP/401k automated contributions
5. **Identical-block filter**: if ≥3 buyers share the exact same (shares, price, date), the entire block is removed. IPO/PIPE allocations are not independent decisions.
6. **Same-price offering filter**: if ≥3 buyers share the same (price, date) with different share counts, also removed. Catches secondary offerings (BKV at $18.00, COSO at $21.50, BETA at $34.00 — confirmed underperformers).

**Window**: 14 calendar days rolling.

**Sub-flags** (stored in `evidence.cluster`):
- `executive_cluster`: True if CFO/CEO/COO/Chairman is among participants
- `tight_cluster`: True if ≥3 distinct insiders bought within a 5-day sub-window

`backfill_signals.py` bulk-loads each ticker's P transactions and passes them to
the same `cluster_from_transactions()` — there is no second implementation to keep
in sync. `test_cluster.py` covers every filter above.

---

## Ingest Pipeline Detail

### EDGAR API (`src/ingest/edgar.py`)
- Rate limit: 8 req/sec (EDGAR allows 10, we use 8 for headroom)
- All requests require `User-Agent: InsiderSignal sunyupei19992@gmail.com`
- `_throttle()` is a global thread-safe rate limiter shared across all concurrent fetches
- `_get()` uses tenacity for exponential backoff (5 retries, up to 60s wait)
- HTTP 429 → `EdgarRateLimitError`, HTTP 403 → `EdgarBlockedError`, 5xx → `EdgarServerError`
- `_submissions_cache` caches filer CIK → document paths to avoid redundant API calls

### Form 4 Parser (`src/ingest/parser.py`)
- Only Table I (non-derivative) transactions are parsed; Table II (derivatives/options) is ignored
- `classify_role(raw_title)` uses regex patterns on the raw title string — order matters (CFO checked before Officer)
- 10b5-1 detection reads `<aff10b5One>`, the filing-wide checkbox the SEC added
  when it amended Rule 10b5-1 (effective Feb 2023), then narrows to the
  individual transaction via that transaction's own `<footnoteId>` references.
  There is no `isSubjectToRule10b51` element; earlier docs claimed one. The
  parser previously read `transactionFormType` (whose value is "4") and
  `transactionTimeliness` (a code like "E"), so both checks were dead, and the
  only working mechanism was a substring scan of the whole document — which
  disqualified an open-market buy that merely shared a filing with a plan sale.
- Table I can carry **debt**. A filer reporting notes puts the principal amount
  in both `transactionShares` and `transactionPricePerShare`, so shares × price
  is meaningless. Those filings use `<valueOwnedFollowingTransaction>` instead
  of `<sharesOwnedFollowingTransaction>`; the parser skips them on that basis.
- Only the **first** `<reportingOwner>` is recorded. Joint Form 4s report one
  decision under several names, so collapsing them is what cluster counting
  wants, but the stored `insider_name` is whichever owner EDGAR listed first.

### Store Layer (`src/db/store.py`)
- `write_filing(cur, filing_meta, parsed, ticker)` — must be called with an open cursor (not its own connection); the caller manages the transaction
- `_compute_is_routine()` looks back up to 3 years for same-month P transactions. Returns `None` if the DB doesn't have enough history (avoids false positives)
- `batch_save_signals(signals)` — preferred over `save_signal()` for bulk operations; handles within-batch deduplication by processing in date order
- Signal cooldown: 7-day window; a follow-up signal is suppressed unless score increased ≥10 pts OR signal_type upgraded

### is_routine Pre-computation
`is_routine` is stored on the transaction row at ingest time so the routine check
survives data pruning (`prune_old_data()`). Without it, old transactions
that prove someone is routine would be deleted before the check runs.

**Retention is `store.RETENTION_MONTHS`, raised from 24 to 48 on 2026-09-08.** The
routine check looks back 3 years and returns `None` rather than guess when the
database does not reach that far, so under a 24-month window it structurally could
not decide: 3,603 of 13,355 stored P transactions carried NULL and only 517 were
ever True. 48 months is the smallest window that contains the check's own lookback.
At 24 months the database sat at 103MB of Neon's 500MB, so expect roughly 206MB.
Re-check that headroom before raising it again. This does not recover history
already deleted; `scripts/build_form4_archive.py` is the only thing that does.
- `NULL` = legacy row (pre-schema); falls back to live calc from `prior_purchases`
- `TRUE` / `FALSE` = definitive; never re-computed

---

## Backtest Engine

**File**: `src/backtest/engine.py`

### `run_backtest(threshold=65, lookback_days=730)`
1. Fetches BUY/CLUSTER_BUY signals from `signals` table with `signal_date >= today - 730d`
2. Also fetches CLUSTER_BUY signals with score 50–64 separately for `cluster_5064` analysis
3. For each horizon (30, 60, 90, 180 days):
   - Filters to signals where `signal_date <= today - (horizon + 3)` (completed exits only)
   - `exec_date = filed_date + 1 + 3` (filed_date + 4, realistic fill lag)
   - `exit_date = exec_date + horizon_days`
   - Fetches `ticker_return = get_price_change_pct(ticker, exec_date, exit_date)`
   - Fetches `spy_return = get_price_change_pct("SPY", exec_date, exit_date)`
   - `excess_return = ticker_return - spy_return`
   - A symbol with **no prices** for the window → `ticker_return = -50.0` (delisting; survivorship bias correction)
   - A **failed request** → signal dropped from the sample, counted in `metrics.risk.n_no_spy_data`. `prices.get_price_change` distinguishes the two; the old `None` return conflated them, so a network blip was scored as a total loss
   - Exit date in the future → excluded, counted in `metrics.risk.n_exit_in_future`
   - All of the above via `_excess_return`, shared by the headline and cluster 50–64 analyses
4. Computes stratified metrics: by score band, cap tier, signal type
5. Computes IWM benchmark separately for small-cap signals
6. Computes rolling 90-day hit rate time series (every 14 days)
7. Stores everything in `metrics` JSONB including full `detail` list

### `save_backtest_results(results, threshold, label="scheduled")`
- Deletes `WHERE run_date = NOW()::DATE AND threshold = %s AND run_label = %s` before inserting
- Safe to re-run; does NOT delete historical runs from prior weeks, and does not touch
  rows written under a different label

### Market Price Fetching (`src/market/prices.py`)
- `get_price_change_pct(ticker, start_date, end_date)` → uses Yahoo Finance chart API
- Returns `None` if no data (delisted, ticker not found, API error)
- Cached via `@st.cache_data(ttl=300)` in the dashboard context only

---

## Market Cap Refresh (`scripts/refresh_market_caps.py`)

Three-pass approach (all free, no API keys):
1. **EDGAR bulk XBRL frames** — `us-gaap/CommonStockSharesOutstanding` → ~4,238 companies in one HTTP call
2. **EDGAR DEI frames** — `dei/EntityCommonStockSharesOutstanding` → +850 companies (LLY, WMT, IT, LUV, etc. that use DEI taxonomy instead of us-gaap)
3. **EDGAR per-company concept API** — fallback for community banks and newer filers not in bulk frames

Then: `shares_outstanding × current_price (Yahoo Finance) → market_cap → cap_tier`.
Takes ~30 minutes for ~1,900 companies. `--force` re-fetches even populated rows.

Cap tier boundaries:
- `small`: < $2B
- `mid`: $2B – $10B
- `large`: > $10B
- `unknown`: not resolvable (scored conservatively at +5 pts, NOT +15)

---

## Dashboard Routes (`web/`)

| Route | What it does | Query module |
|---|---|---|
| `/` | Signal triage. URL-backed filters (lookback, min score, type, cap tier, search, day pin), a per-day volume strip, Top Picks, and expandable signal cards carrying the whole evidence blob. "New since last visit" is per-browser localStorage. | `lib/queries/signals.ts` |
| `/backtest` | Hit rate per horizon, excess return by signal month, distribution, stratified breakdowns, risk, cluster 50–64, rolling 90-day hit rate. | `lib/queries/backtest.ts` |
| `/clusters` | Every 14-day window with 3+ insiders, each as a timeline. Read from `signals`, never recomputed. | `lib/queries/clusters.ts` |
| `/sectors` | Signals grouped by SIC division. Needs `backfill_sic.py` to have run. | `lib/queries/sectors.ts` |
| `/ticker` and `/ticker/[symbol]` | Ticker search; per-ticker transactions, purchase scatter, signal history, live quote. | `lib/queries/ticker.ts` |
| `/how-it-works` | Pipeline diagram, disqualifiers, interactive scoring explainer, thresholds, research, limitations. | `lib/queries/pipeline.ts` |
| `/preview` | Dev-only component gallery. | — |

**Conventions that matter:**

- Every query fn is wrapped in `unstable_cache` with a tag (`pipeline`, `signals`,
  `backtest`) and a 15-minute revalidate. Pages set `export const revalidate`.
- Cache keys must stay bounded. Free-text search is applied in memory, not in SQL,
  so typing cannot explode the key space.
- JSONB (`evidence`, `score_breakdown`, `metrics`) is parsed once by the zod
  schemas in `lib/types.ts` at the query boundary. Components receive typed data.
- Joins to `companies` use `LEFT JOIN LATERAL ... LIMIT 1`. `companies` is keyed
  by CIK and ticker is not unique, so a plain join duplicates rows.
- `POST /api/revalidate` (bearer token) busts all three tags; `daily_ingest.yml`
  calls it after a successful run.

## Operational Scripts

### After any scoring or cluster logic change:
```bash
uv run python scripts/backfill_signals.py --days 730 --force
# Takes ~8 minutes. Rescores all 2 years of P transactions, rebuilds signals table.
uv run python scripts/run_backtest.py
# Takes ~30 minutes. Re-evaluates signal quality against historical prices.
git add src/signals/ scripts/backfill_signals.py scripts/run_backtest.py
git commit -m "..."
git push
```

### To fill a historical gap:
```bash
uv run python scripts/bootstrap.py --start YYYY-MM-DD --end YYYY-MM-DD --force
# --force re-fetches XML for filings already stored (fixes corrupted/missing data)
git add .  # bootstrap updates last_run.txt
git commit -m "Bootstrap gap fill YYYY-MM-DD to YYYY-MM-DD"
git push
```

### To refresh market caps:
```bash
uv run python scripts/refresh_market_caps.py
# ~30 min; --force re-fetches populated rows too
git commit -m "Refresh market caps" last_run.txt  # if it touches last_run.txt
git push
```

### To re-run the backtest locally:
```bash
uv run python scripts/run_backtest.py
# Safe to re-run — replaces today's 'scheduled' rows before inserting

uv run python scripts/run_backtest.py --label adjclose-check
# Same evaluation, stored separately. Does not touch the dashboard's rows.
```

### To trigger a backtest immediately (without waiting for Sunday):
- Go to GitHub → Actions → Weekly Backtest → Run workflow → Run workflow
- Or: `gh workflow run weekly_backtest.yml`

---

## GitHub Actions Workflows

| Workflow | Schedule | What it does |
|---|---|---|
| `daily_ingest.yml` | Weekdays 11am UTC | Runs `run_ingest.py`; commits `last_run.txt` to keep repo active |
| `weekly_backtest.yml` | Sundays 12pm UTC | `refresh_market_caps.py` then `run_backtest.py` |
| `bootstrap.yml` | Manual only | `workflow_dispatch` triggers `bootstrap.py` with configurable date range |
| `tests.yml` | Every push to `main`, every PR | `ruff check src scripts tests`, then `pytest -q`. Lint runs first, so a lint error means the suite never ran |

**GitHub Actions gotchas:**
- Workflows **disable after 60 days of no repo activity**. `run_ingest.py` commits `last_run.txt` each day to keep the repo live. If disabled, re-enable from the Actions tab on GitHub.
- Neon **scales to zero when idle** (~5 min). First query of the day may be slow. Never rely on pg_cron — GitHub Actions is the only scheduler.
- Vercel does not sleep, so there is no keep-alive workflow. Neon still scales to zero when idle, so the first query after a quiet period is slow — that is inherent and a ping would not fix it (Neon idles after ~5 minutes).

---

## Where the numbers live

**This file is reference. It says what is true, not how it was found.** Measured
results, dated, with the runs behind them, live in
[`docs/scoring-improvement-plan.md`](docs/scoring-improvement-plan.md) sections 7a and 7b
and [`docs/beyond-price.md`](docs/beyond-price.md) section 0a. A finding belongs there and
a rule belongs here. Counts of rows in Neon belong in neither: they are stale the day
after they are written, and `scripts/audit_data.py` prints them on demand.

The old-versus-shipped backtest comparison used to be copied out here in full. It is in
`scoring-improvement-plan.md` section 7b, once.

---

## Known Data Quirks

**DRIP/401k contamination (mostly filtered):**
- WERN: quarterly fractional shares by 5–7 insiders (139.873 shares × 4/yr). Caught by $25K min + identical-block filter.
- EPAM: fires April 30 and October 31.
- GABC: monthly.

**CMPO-style fund partnerships:**
- Single fund (e.g. "Resolute Compo Holdings LLC") files separately for each partner. All buy identical shares same day. Caught by `is_direct=FALSE` exclusion + identical-block filter.

**is_routine NULL rows:**
- ~2,917 P transactions have `is_routine=NULL` (pre-schema legacy rows). These fall back to live routine calculation. Correct behavior — run `backfill_routine_flags()` in store.py to pre-populate in bulk.

**Large-cap CLUSTER_BUY:**
- Automatically downgraded to WATCH. Empirical: 0% hit rate at 90d, −16% avg excess return.

**Unknown cap_tier scoring:**
- Scored at +5 (not +15). Backtesting showed unknown-cap includes large-caps (FI/Fiserv, KO/Coca-Cola, BDX) that EDGAR's bulk frames miss. Scoring them as small-cap pushed them over the BUY threshold undeservedly.

---

## Debugging Common Issues

**Signals missing from dashboard:**
- Check `signals` table for the ticker and date. `(ticker, signal_date)` is UNIQUE.
- Confirm `signal_type IN ('BUY','CLUSTER_BUY')` and score meets the filter threshold.
- Signal cooldown: a signal within 7 days of a prior one for the same ticker is suppressed unless score jumped ≥10 or type upgraded (see `_is_suppressed()` in store.py).
- Check `evidence->>'filed_date'` is populated — signals before 2024-04-03 won't exist.

**Cluster signal missing or wrong signal type:**
- Live and backfill share `cluster_from_transactions()`, so a drifted-copy bug is no longer possible; check the filters themselves (direct-only, ≥$25K, no identical-block, no same-price-offering) and `test_cluster.py`.
- Re-run: `uv run python scripts/backfill_signals.py --days 730 --force`

**Backtest chart shows only a short date range:**
- The chart uses `exec_date` from `detail` in the latest `backtest_runs.metrics`. The date range = LOOKBACK_DAYS (730 days). If it's short, a prior run used a smaller value.
- Fix: trigger workflow_dispatch on `weekly_backtest.yml` to re-run with the updated LOOKBACK_DAYS=730.

**Backtest n_trades is very low:**
- Signals may be too recent (exits not completed yet). Each horizon needs `signal_date <= today - (horizon + 3)`.
- For 180d horizon, signals need to be ≥183 days old. Run `\d backtest_runs` to check latest n_trades.

**Market cap showing as 'unknown' after refresh:**
- Three passes still couldn't find shares outstanding in EDGAR. True unknowns exist; scored at +5.
- Can manually look up and set: `UPDATE companies SET market_cap=X, cap_tier='small' WHERE ticker='XYZ'`

**Telegram alerts not sending:**
- Check `TELEGRAM_BOT_TOKEN` in GitHub Actions Secrets.
- Check `telegram_subscribers` has at least one `active = TRUE` row — `_send()` skips
  entirely with "No active Telegram subscribers" if the table is empty, which it will be
  on a fresh deploy until `scripts/seed_telegram_subscriber.py` runs.
- `alerted=TRUE` on a signal means it already sent — no re-alert.
- Only `BUY` and `CLUSTER_BUY` signal types trigger alerts (not WATCH or LOW).

**`/subscribe` gets no reply:**
- The webhook must be registered before Telegram will deliver anything to
  `web/app/api/telegram/webhook/route.ts` — see `scripts/register_telegram_webhook.py`
  in `scripts/README.md`.
- Check Vercel's Deployment Protection is off for the production domain; if it's on,
  Telegram's request 401s before the route runs.
- Check `TELEGRAM_WEBHOOK_SECRET` matches between Vercel and whatever was passed to
  `register_telegram_webhook.py` — a mismatch makes the route 401 every update.

**GitHub Actions disabled:**
- Re-enable from Actions tab on GitHub. Then verify `last_run.txt` is being committed by daily ingest.

**Neon connection timeout in Actions:**
- Direct URL must be used (not pooled) in GitHub Actions. Check `DATABASE_URL` secret doesn't include `-pooler`.
- The web app uses Neon's HTTP driver (`@neondatabase/serverless`), which needs no pooler.

**Dashboard not updating:**
- Query results are cached 15 minutes (`unstable_cache`) and pages have their own `revalidate`. Wait it out, redeploy, or `POST /api/revalidate` with the bearer token.
- Prices are fetched client-side per request and cached 5 minutes at the CDN; they are never stored.

---

## What Not To Do

- **Never** commit `.env`, `secrets.toml`, or any credential file. The repo is public.
- **Never** use `get_conn()` outside a `with` block — the context manager handles commit/rollback/close.
- **Never** change the cluster threshold (14d, 3 insiders), the BUY threshold (90), the cluster average (80) or the cluster max_score (85) without re-running the full backfill — every signal in the DB would be stale. These live in `src/signals/constants.py`; read them there rather than trusting a number written in prose, including this one.
- **Never** add `ORDER BY RANDOM()` or non-deterministic queries to backfill — idempotency depends on deterministic processing order.
- **Never** call `get_market_data()` in the backfill script — it fetches live prices which don't represent historical cap tiers. Use `tx.get("cap_tier")` from the companies join instead.
- **Never** write to the DB from `web/` pages or `lib/` — read-only, `lib/db.ts` exposes reads only. The lone exception is `app/api/telegram/webhook/route.ts`, which owns its own write client and must not be reused elsewhere.
- **Never** use the pooled Neon URL (`-pooler.neon.tech`) in GitHub Actions — the direct URL is correct there.
- **Never** import a *value* from `web/lib/queries/` into a client component. `import type` is fine; a value import drags the DB client into the browser bundle and `server-only` will fail the build.
- **Don't** change `LOOKBACK_DAYS` in `run_backtest.py` without understanding that it affects the date range of the backtest chart (via `detail.exec_date`).
- **Don't** add error handling for scenarios that can't happen. Trust framework guarantees.
- **Don't** add comments explaining what code does. Only comment the non-obvious *why*.
