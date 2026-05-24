# Insider Signal System — Claude Reference

This document is the authoritative reference for AI agents working on this codebase.
Read it in full before touching any code.

---

## Auto-Commit and Push Policy

**Always automatically commit and push every change you make.**

- Stage only the specific files you changed (never `git add -A` or `git add .`)
- Write a concise commit message describing what changed and why
- Push to `origin main` immediately after committing
- Do not ask for confirmation — just commit and push

Example:
```bash
git add scripts/run_backtest.py
git commit -m "Increase LOOKBACK_DAYS from 365 to 730 for 2-year backtest window"
git push
```

---

## Quick Orientation

**The three files that govern system behavior end-to-end:**
1. `src/signals/scorer.py` — scoring model (what makes a signal good/bad)
2. `src/signals/cluster.py` — cluster detection (3+ insiders = CLUSTER_BUY)
3. `src/backtest/engine.py` — backtest engine (how signal quality is measured)

**The golden rule — any change to scorer.py or cluster.py must be followed by:**
```bash
python3 scripts/backfill_signals.py --days 730 --force  # ~8 min
python3 scripts/run_backtest.py                         # ~30 min
git add src/signals/ scripts/backfill_signals.py
git commit -m "..."
git push
```
Skipping either step leaves the DB stale and the backtest chart misleading.

**Where to find things:**

| What | Where |
|---|---|
| Scoring weights / factors | `src/signals/scorer.py` → `ROLE_SCORES`, `CAP_SCORES`, `score_transaction()` |
| Signal classification thresholds | `src/signals/scorer.py` → `classify_signal()` |
| Cluster eligibility filters | `src/signals/cluster.py` → `detect_clusters_for_ticker()` |
| Evidence blob structure | `src/signals/formatter.py` → `build_evidence()` |
| Backtest signal query | `src/backtest/engine.py` → `_get_historical_signals()` |
| Backtest metrics structure | `src/backtest/engine.py` → `metrics_blob` dict near bottom of `run_backtest()` |
| DB connection | `src/db/connection.py` → `get_conn()` (always use as context manager) |
| Schema / migrations | `src/db/schema.sql` |
| Write a filing to DB | `src/ingest/store.py` → `write_filing()` |
| Write a signal to DB | `src/ingest/store.py` → `save_signal()` or `batch_save_signals()` |
| EDGAR API client | `src/ingest/edgar.py` → `_get()`, rate-limited to 8 req/sec |
| Form 4 XML parser | `src/ingest/parser.py` → `parse_form4()` |
| Role classification | `src/ingest/parser.py` → `classify_role()` |
| Dashboard charts | `dashboard/app.py` — each tab is a clearly delimited `with tab_X:` block |
| GitHub Actions config | `.github/workflows/` — 4 workflow files |
| Backtest lookback window | `scripts/run_backtest.py` → `LOOKBACK_DAYS = 730` |

**Key thresholds (do not change without re-running full backfill + backtest):**
- BUY: score ≥ 60 (reduced from 65 on 2026-05-25 after empirical weight recalibration)
- CLUSTER_BUY: ≥3 direct insiders, 14d window, avg score ≥28 (was 35), tight OR max_score≥45 (was 50)
- WATCH: score 45–59 OR weak cluster
- Backtest lookback: 730 days

---

## What This System Does

Ingests SEC Form 4 insider purchase disclosures daily, scores them with a
research-backed model, and surfaces actionable buy signals via a Streamlit
dashboard and Telegram alerts. Runs at zero cost indefinitely.

**Research basis (non-negotiable — these are the foundation of every design decision):**
- Lakonishok & Lee (2001): small-cap insider buys → +7.4% abnormal return at 12 months
- Cohen, Malloy & Pomorski (2012): opportunistic trades → 82 bps/month alpha; routine ≈ 0
- Jeng, Metrick & Zeckhauser (2003): purchase portfolio → ~6% annualized alpha
- TipRanks CFO study: CFO (21.5%) > Director (20.7%) > Officer (19.8%) > CEO (19.3%)
- Cluster research: 3+ insiders buying together ≈ 2× alpha of single insider buy

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

## Data Flow (End to End)

```
SEC EDGAR XML
    ↓
scripts/run_ingest.py                       ← GitHub Actions daily entry point
    ↓
src/ingest/edgar.py                         ← fetch accession list + XML
    _get_filing_list(date)                  ← queries EDGAR full-text search for Form 4s
    fetch_form4_xml(accession_number)       ← fetches raw XML from EDGAR archives
    ↓
src/ingest/parser.py
    parse_form4(xml_str)                    ← returns {issuer, owner, transactions[]}
    classify_role(raw_title)                ← keyword-match → cfo/ceo/director/officer/etc.
    ↓
src/ingest/store.py
    write_filing(cur, filing_meta, parsed)  ← upserts companies, form4_filings, transactions
    _compute_is_routine(cur, name, cik)     ← checks if insider bought same month ≥2/3 prior yrs
    ↓  (daily ingest also runs scoring immediately after writing)
src/signals/scorer.py
    score_transaction(tx, owner, company,   ← returns {score, breakdown, disqualified}
                      market_data,
                      prior_purchases)
src/signals/cluster.py
    detect_clusters_for_ticker(ticker, cur) ← finds clusters in 14d window; returns cluster_info
src/signals/formatter.py
    build_evidence(tx, company, cluster)    ← assembles JSONB evidence blob
    ↓
src/ingest/store.py
    batch_save_signals(signals)             ← upserts signals table; deduplicates within cooldown
    ↓
[weekly — GitHub Actions Sunday 12pm UTC]
scripts/refresh_market_caps.py              ← 3-pass EDGAR + YF cap refresh (run before backtest)
scripts/run_backtest.py
    src/backtest/engine.py
        run_backtest(threshold=65,          ← queries signals, fetches historical prices from YF,
                     lookback_days=730)       computes excess returns vs SPY/IWM
        save_backtest_results(results)      ← upserts backtest_runs (deletes today's rows first)
    ↓
dashboard/app.py                            ← reads all tables, no writes; Streamlit read-only
```

**Key constraint**: `dashboard/app.py` never writes to the database. All writes
happen through the ingest and backtest scripts.

---

## Project Layout

```
src/
  db/
    connection.py       # get_conn() — psycopg2 context manager; handles commit/rollback/close
    schema.sql          # CREATE TABLE + idempotent ALTER TABLE migrations (run this to init DB)
  ingest/
    edgar.py            # EDGAR API client — rate-limited, User-Agent required, tenacity retries
    parser.py           # Form 4 XML → normalized dict; classify_role() keyword matcher
    store.py            # write_filing(), batch_save_signals(), backfill_routine_flags()
    common.py           # log(), phase(), setup_log_tee(), fmt_elapsed() — shared logging utils
  signals/
    scorer.py           # score_transaction(), classify_signal() — the scoring model
    cluster.py          # detect_clusters_for_ticker() — cluster detection
    formatter.py        # build_evidence() — assembles the JSONB evidence blob stored in signals
  market/
    prices.py           # get_price_change_pct() — YF chart API; get_market_data() for cap/52wk
  backtest/
    engine.py           # run_backtest(), save_backtest_results(), _get_historical_signals()
  alerts/
    telegram.py         # send_signal_alert(), send_error() — Telegram Bot API

scripts/
  bootstrap.py          # One-time: load historical Form 4s. Args: --start, --end, --force
  backfill_signals.py   # Rescore all stored P transactions → signals table. Args: --days, --force
  refresh_market_caps.py# 3-pass cap refresh: EDGAR us-gaap → DEI → per-company API → YF price
  run_ingest.py         # Daily ingest entry point (called by GitHub Actions)
  run_backtest.py       # Weekly backtest entry point. LOOKBACK_DAYS = 730
  update_tickers.py     # Refresh S&P500 + Russell2000 ticker universe in companies table

dashboard/
  app.py                # All 5 dashboard tabs in one file; read-only DB access

.github/workflows/
  daily_ingest.yml      # Weekdays 11am UTC + workflow_dispatch
  weekly_backtest.yml   # Sundays 12pm UTC — refresh_market_caps then run_backtest
  keep_alive.yml        # 2x/day pings Streamlit to prevent cold-start lag
  bootstrap.yml         # Manual only — workflow_dispatch triggers bootstrap.py
```

---

## Database Schema

### `companies`
```
cik         TEXT PRIMARY KEY     — zero-stripped CIK from EDGAR
ticker      TEXT                 — exchange ticker (may be NULL for foreign filers)
name        TEXT                 — company name from EDGAR
sic_code    TEXT                 — SIC industry code (not currently used in scoring)
market_cap  BIGINT               — shares_outstanding × current_price (refreshed weekly)
cap_tier    TEXT                 — 'small' (<$2B), 'mid' ($2B–$10B), 'large' (>$10B), 'unknown'
updated_at  TIMESTAMPTZ
```

### `form4_filings`
```
id               SERIAL PRIMARY KEY
accession_number TEXT UNIQUE     — EDGAR accession number (e.g. 0001234567-24-000123)
cik              TEXT → companies.cik
filed_date       DATE            — date EDGAR received the filing (used for signal_date = filed_date+1)
period_date      DATE            — date the transaction occurred (NOT used for signal_date)
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

### `signals`
```
id              SERIAL PRIMARY KEY
ticker          TEXT
signal_date     DATE            — filed_date + 1 day (NOT transaction_date — avoids look-ahead bias)
score           INT             — 0–100
signal_type     TEXT            — 'BUY' (≥65), 'WATCH' (45–64), 'CLUSTER_BUY', 'LOW'
cluster_flag    BOOLEAN         — TRUE if ≥3 direct insiders bought in 14d window
score_breakdown JSONB           — {factor_name: points} e.g. {"role_cfo": 20, "cap_small": 15}
evidence        JSONB           — full detail: insiders[], cluster{}, company context, filed_date
alerted         BOOLEAN         — TRUE once Telegram alert sent (prevents re-alerting on upsert)
created_at      TIMESTAMPTZ

UNIQUE: (ticker, signal_date)   — one signal row per ticker per day
```

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
threshold      INT             — score threshold used (always 65)
horizon_days   INT             — hold horizon: 30, 60, 90, or 180
n_trades       INT             — number of signals evaluated for this horizon
hit_rate       NUMERIC         — % of signals with positive excess return
avg_return     NUMERIC         — mean excess return vs SPY (%)
median_return  NUMERIC         — median excess return (more robust than mean)
p25_return     NUMERIC         — 25th percentile (downside floor)
p75_return     NUMERIC         — 75th percentile (upside)
sharpe         NUMERIC         — annualized Sharpe on excess returns
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

**`save_backtest_results()` behavior**: deletes all rows where `run_date = TODAY AND threshold = 65`
before inserting. Safe to re-run on the same day. Historical runs accumulate indefinitely.

---

## Scoring Logic

### Hard Disqualifiers (checked in order, early-exit with score=0)

1. `transaction_code != 'P'` → not an open-market purchase, skip entirely
2. `is_10b51 = TRUE` → pre-arranged 10b5-1 plan; zero alpha (Cohen et al.)
3. `total_value < $2,000` → trivial noise (DRIP/401k/fractional reinvestment)
4. `is_routine = TRUE` (or live calc shows ≥2 of 3 prior same-month purchases) → disqualified

### Score Factors (additive, capped at 100)

| Factor | Points | Condition |
|---|---|---|
| `indirect_purchase` | **−15** | `is_direct = FALSE` — was -8; empirical lift -16%/60d, -27%/90d |
| `role_cfo` | +15 | role_category = 'cfo' — was +20; negative 90d lift |
| `role_director` | +16 | role_category = 'director' |
| `role_chairman` | +14 | role_category = 'chairman' |
| `role_coo` | +12 | role_category = 'coo' |
| `role_officer` | +12 | role_category = 'officer' |
| `role_ceo` | +10 | role_category = 'ceo' |
| `role_other` | +0 | was +6; empirical lift -21% at both horizons — removed |
| `cap_small` | +15 | cap_tier = 'small' (<$2B) — strongest alpha per Lakonishok & Lee |
| `cap_mid` | +8 | cap_tier = 'mid' ($2B–$10B) |
| `cap_large` | +0 | cap_tier = 'large' (>$10B) |
| `cap_unknown` | +0 | was +5; -4.8%/60d, -1.2%/90d empirical lift |
| `value_500k_plus` | +9 | total_value ≥ $500K — was +12; dollar size alone doesn't predict returns |
| `value_100k_plus` | +5 | total_value ≥ $100K — was +8; negative empirical lift |
| `holdings_increase_30pct` | +15 | (shares_bought / shares_before) ≥ 30% — strong 90d lift +7.7% |
| `holdings_increase_15pct` | +5 | 15–30% — was +10; negative empirical lift |
| `holdings_increase_5pct` | +5 | 5–15% — positive 60d lift +19.6% |
| `prior_purchase_31_365d` | +12 | **New (2026-05-25)**: prior buy 31-364d ago (sustained conviction) — +9.3%/60d, +13.4%/90d |
| `sequenced_buying_30d` | +8 | Prior buy within 30 days (rapid sequence) |
| `first_purchase_12mo` | +3 | No prior buy in 365d — was +10; negative empirical lift; kept at +3 for novelty |
| `near_52wk_low_5pct` | +12 | Price within 5% of 52-week low (only fires in daily ingest, not backfill) |
| `near_52wk_low_10pct` | +7 | Price within 10% of 52-week low (same caveat) |

**Timing factors are mutually exclusive** — exactly one of `sequenced_buying_30d`, `prior_purchase_31_365d`, or `first_purchase_12mo` fires per signal.

`first_purchase_12mo` and `sequenced_buying_30d` are mutually exclusive by definition.

### Signal Classification (`classify_signal()`)

```
cluster_flag=True:
    avg(participant_scores) >= 28 AND (tight_cluster OR max_score >= 45)
        AND cap_tier != 'large'  → CLUSTER_BUY
    cap_tier == 'large'          → WATCH  (0% hit rate at 90d, −16% avg excess)
    avg >= 28 but loose + weak   → WATCH
    avg < 28                     → WATCH  (very weak cluster — surfaced but no alert)
no cluster:
    score >= 60                  → BUY  (was 65; reduced after 2026-05-25 recalibration)
    score >= 45                  → WATCH
    score < 45                   → LOW
```

The cluster uses **average** of all participant scores, not the max. Three directors
each scoring 42 → avg=42 ≥ 35 qualifies. Empirical: loose clusters with max_score<50
averaged −5% excess at 90d; tight/high-score clusters averaged +3–5%.

---

## Cluster Detection

**File**: `src/signals/cluster.py` → `detect_clusters_for_ticker(ticker, cur)`

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

**CRITICAL**: `backfill_signals.py` has its own in-memory `_detect_cluster()` that
mirrors these exact filters. If you change `cluster.py`, you **must** update
`backfill_signals.py` to match, then re-run the backfill.

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
- 10b5-1 detection uses the `isSubjectToRule10b51` checkbox in the XML

### Store Layer (`src/ingest/store.py`)
- `write_filing(cur, filing_meta, parsed, ticker)` — must be called with an open cursor (not its own connection); the caller manages the transaction
- `_compute_is_routine()` looks back up to 3 years for same-month P transactions. Returns `None` if the DB doesn't have enough history (avoids false positives)
- `batch_save_signals(signals)` — preferred over `save_signal()` for bulk operations; handles within-batch deduplication by processing in date order
- Signal cooldown: 7-day window; a follow-up signal is suppressed unless score increased ≥10 pts OR signal_type upgraded

### is_routine Pre-computation
`is_routine` is stored on the transaction row at ingest time so the routine check
survives the 2-year data pruning (`prune_old_data()`). Without it, old transactions
that prove someone is routine would be deleted before the check runs.
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
   - Delisted stocks (yfinance returns None) → `ticker_return = -50.0` (survivorship bias correction)
4. Computes stratified metrics: by score band, cap tier, signal type
5. Computes IWM benchmark separately for small-cap signals
6. Computes rolling 90-day hit rate time series (every 14 days)
7. Stores everything in `metrics` JSONB including full `detail` list

### `save_backtest_results(results, threshold)`
- Deletes `WHERE run_date = NOW()::DATE AND threshold = %s` before inserting
- Safe to re-run; does NOT delete historical runs from prior weeks

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

## Dashboard Sections (`dashboard/app.py`)

### Tab 1 — Signals
- Filters: lookback days (slider), min score (slider), signal types (checkboxes), cap tier (checkboxes)
- Top Picks section: top 3 CLUSTER_BUY (or BUY) signals with conviction badges (PRIME/STRONG/CLUSTER/HIGH/BUY)
- Conviction logic: PRIME = tight + exec cluster, STRONG = tight OR exec, CLUSTER = neither
- Each signal expands to show: who bought (table), score breakdown (bar chart), cluster/52wk-low badges

### Tab 2 — Positions
- Shows signals within `HOLD_HORIZON_DAYS = 90` that haven't expired
- Fetches live prices via `_fetch_current_price()` (Yahoo Finance, 5-min cache)
- Return = (current − avg_insider_entry) / entry × 100 (raw, not vs SPY)

### Tab 3 — Backtest
- Hit rate metrics: one metric card per horizon, delta vs 50% baseline
- **Avg excess return chart**: built from `detail[].exec_date` in latest run's metrics JSONB, binned by month, colored by horizon. Falls back to bar chart if no detail data yet.
- Distribution tab: box plot (p25–p75 box, median line, min/max whiskers) per horizon
- Score band / cap tier / signal type tabs: pivot tables with N, hit rate, avg, median
- Risk tab: % losses >20%, max consecutive losses, worst outcome
- Cluster 50–64 tab: separate analysis of CLUSTER_BUY signals scored below BUY threshold
- Rolling hit rate: 90-day rolling hit rate sampled every 14 days — useful for detecting alpha decay

### Tab 4 — History
- Per-ticker transaction history (up to 100 most recent)
- Scatter plot of open-market purchases: green = opportunistic, grey = routine
- Signal history for the ticker at the bottom

### Tab 5 — About
- Data sources, scoring table, signal type explanations, backtest methodology, limitations, research papers

**Dashboard DB connection**: `get_db()` uses `@st.cache_resource` (one connection per session).
The pooled URL (`-pooler.neon.tech`) is auto-inserted if missing — never use the direct URL in
Streamlit (Neon requires connection pooling for serverless deployments).

`_parse_metrics(raw)` normalizes metrics JSONB from both old (list) and new (dict) formats.
Call it before any `.get()` on metrics data.

---

## Operational Scripts

### After any scoring or cluster logic change:
```bash
python3 scripts/backfill_signals.py --days 730 --force
# Takes ~8 minutes. Rescores all 2 years of P transactions, rebuilds signals table.
python3 scripts/run_backtest.py
# Takes ~30 minutes. Re-evaluates signal quality against historical prices.
git add src/signals/ scripts/backfill_signals.py scripts/run_backtest.py
git commit -m "..."
git push
```

### To fill a historical gap:
```bash
python3 scripts/bootstrap.py --start YYYY-MM-DD --end YYYY-MM-DD --force
# --force re-fetches XML for filings already stored (fixes corrupted/missing data)
git add .  # bootstrap updates last_run.txt
git commit -m "Bootstrap gap fill YYYY-MM-DD to YYYY-MM-DD"
git push
```

### To refresh market caps:
```bash
python3 scripts/refresh_market_caps.py
# ~30 min; --force re-fetches populated rows too
git commit -m "Refresh market caps" last_run.txt  # if it touches last_run.txt
git push
```

### To re-run the backtest locally:
```bash
python3 scripts/run_backtest.py
# Safe to re-run — deletes today's rows before inserting
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
| `keep_alive.yml` | 2x/day | HTTP ping to Streamlit URL to prevent cold-start lag |
| `bootstrap.yml` | Manual only | `workflow_dispatch` triggers `bootstrap.py` with configurable date range |

**GitHub Actions gotchas:**
- Workflows **disable after 60 days of no repo activity**. `run_ingest.py` commits `last_run.txt` each day to keep the repo live. If disabled, re-enable from the Actions tab on GitHub.
- Neon **scales to zero when idle** (~5 min). First query of the day may be slow. Never rely on pg_cron — GitHub Actions is the only scheduler.
- Streamlit **sleeps after ~12 hours of inactivity**. `keep_alive.yml` pings it 2x/day.

---

## Current DB State (as of 2026-05-25)

- **Filings**: ~153,602 (2024-04-03 → present)
- **P transactions (non-10b5-1)**: ~12,676
- **Signals**: ~2,574 total (70 BUY, 349 CLUSTER_BUY, 2,155 WATCH + LOW)
- **Companies with market_cap**: ~1,488 / 2,119 (631 still unknown → scored at +5)
- **is_routine**: 406 routine / 10,788 opportunistic / 2,917 NULL (legacy, falls back to live calc)
- **Coverage gap**: April 2024 start is thin (~643 filings vs 3,712+ in May 2024); October 2025 gap was filled by bootstrap re-run

**Key empirical backtest findings (2026-05-20):**
| Horizon | Avg Excess Return | Hit Rate |
|---------|------------------|----------|
| 30d | +2.1% | ~55% |
| 60d | +4.0% | ~55% |
| 90d | +2.4% | ~55% |
| 180d | +15.9% | ~60% |

Best bucket: CLUSTER_BUY score 50–64 at 180d → +31.2% avg, 62% hit rate.
Worst: LGF (Lions Gate) — insiders averaging down, −63.1% at 180d. No filter implemented.

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
- `cluster.py` and `backfill_signals.py` must have identical eligibility filters. Drift = stale clusters in DB.
- Verify the cluster filters: direct-only, ≥$25K, no identical-block, no same-price-offering.
- Re-run: `python3 scripts/backfill_signals.py --days 730 --force`

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
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in GitHub Actions Secrets.
- `alerted=TRUE` on a signal means it already sent — no re-alert.
- Only `BUY` and `CLUSTER_BUY` signal types trigger alerts (not WATCH or LOW).

**GitHub Actions disabled:**
- Re-enable from Actions tab on GitHub. Then verify `last_run.txt` is being committed by daily ingest.

**Neon connection timeout in Actions:**
- Direct URL must be used (not pooled) in GitHub Actions. Check `DATABASE_URL` secret doesn't include `-pooler`.
- Dashboard uses pooled URL — `get_db()` auto-inserts `-pooler` if missing.

**Streamlit dashboard not updating:**
- `@st.cache_data(ttl=300)` means prices cached 5 min. `@st.cache_resource` means DB connection cached for session.
- Click the "rerun" button or clear cache via the Streamlit menu.

---

## What Not To Do

- **Never** commit `.env`, `secrets.toml`, or any credential file. The repo is public.
- **Never** use `get_conn()` outside a `with` block — the context manager handles commit/rollback/close.
- **Never** change the cluster threshold (14d, 3 insiders), BUY threshold (60), cluster avg (28), or cluster max_score (45) without re-running the full backfill — every signal in the DB would be stale.
- **Never** add `ORDER BY RANDOM()` or non-deterministic queries to backfill — idempotency depends on deterministic processing order.
- **Never** call `get_market_data()` in the backfill script — it fetches live prices which don't represent historical cap tiers. Use `tx.get("cap_tier")` from the companies join instead.
- **Never** write to the DB from `dashboard/app.py` — the dashboard is strictly read-only.
- **Never** use the pooled Neon URL (`-pooler.neon.tech`) in GitHub Actions — only in Streamlit.
- **Never** use the direct Neon URL in Streamlit — it exceeds Neon's serverless connection limit.
- **Don't** change `LOOKBACK_DAYS` in `run_backtest.py` without understanding that it affects the date range of the backtest chart (via `detail.exec_date`).
- **Don't** add error handling for scenarios that can't happen. Trust framework guarantees.
- **Don't** add comments explaining what code does. Only comment the non-obvious *why*.
