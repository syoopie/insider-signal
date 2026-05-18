# Architecture

## System Diagram

```
SEC EDGAR (government website, free public data)
        │
        │  Every weekday at 6 AM ET
        ▼
GitHub Actions (free scheduled compute)
  ├── Fetch new Form 4 filings from EDGAR API
  ├── Filter to S&P 500 + Russell 2000 universe
  ├── Parse XML → insider, role, shares, price, 10b5-1 flag
  ├── Score each open-market purchase (0–100)
  ├── Detect cluster signals (3+ buyers, 14-day window)
  └── Send Telegram alerts for BUY / CLUSTER_BUY signals
        │
        ▼
Neon PostgreSQL (free cloud database)
        │
        ▼
Streamlit Dashboard (free hosted web app)
Telegram Bot (free, sends alerts to phone)
```

**Total monthly cost: $0.**

---

## Data Flow Detail

### Daily Ingest (weekdays, 6 AM ET)

1. Connect to Neon; find the most recent `filed_date` already stored
2. Fetch all Form 4s filed since that date from EDGAR full-text search
3. Filter to tickers in the tracked universe (`data/tickers.txt`)
4. Fetch and parse each filing XML in parallel (ThreadPoolExecutor)
5. Write new companies, filings, and transactions to Neon sequentially (psycopg2 is not thread-safe)
6. For each ticker with recent purchases: fetch market cap + 52-week low via Yahoo Finance
7. Score each open-market purchase; detect cluster signals per ticker
8. Write signals to the `signals` table
9. Send Telegram alerts for BUY and CLUSTER_BUY signals
10. Commit `last_run.txt` to the repo — prevents GitHub from disabling the workflow after 60 days of no code activity
11. On any crash: error handler sends a Telegram message immediately — failures are never silent

### Weekly Backtest (Sundays, noon UTC)

1. Pull all BUY / CLUSTER_BUY signals with `signal_date ≥ 1 year ago`
2. For each signal: fetch stock price at `signal_date + 3 days` (execution lag) and at +30, +60, +90, +180 days
3. Fetch SPY return over the same windows as benchmark
4. Compute hit rate, avg excess return, and Sharpe per horizon
5. Write results to `backtest_runs`; displayed in the dashboard backtest chart

### Keep-Alive (twice daily, 8 AM + 8 PM UTC)

Pings the Streamlit app URL to prevent the 12-hour inactivity sleep. Cold-start takes ~30 seconds; keeping it warm avoids that delay.

---

## Project Structure

```
insider-signal/
├── .github/
│   └── workflows/
│       ├── daily_ingest.yml        # Weekdays 6 AM ET
│       ├── weekly_backtest.yml     # Sundays noon UTC
│       └── keep_alive.yml          # 8 AM + 8 PM UTC daily
├── src/
│   ├── db/
│   │   ├── connection.py           # Neon connection (direct + pooled)
│   │   └── schema.sql              # Table definitions
│   ├── ingest/
│   │   ├── common.py               # Shared logging utilities
│   │   ├── edgar.py                # EDGAR API client (rate-limited, 8 req/sec)
│   │   ├── parser.py               # Form 4 XML parser + role classifier
│   │   └── store.py                # Database write logic (upserts)
│   ├── signals/
│   │   ├── scorer.py               # Scores each transaction 0–100
│   │   ├── cluster.py              # Detects 3+ insider buys in 14-day window
│   │   └── formatter.py            # Builds human-readable evidence text
│   ├── alerts/
│   │   └── telegram.py             # Formats and sends Telegram messages
│   ├── market/
│   │   └── prices.py               # Yahoo Finance: market cap + 52-week low
│   └── backtest/
│       └── engine.py               # Historical signal accuracy validation
├── dashboard/
│   └── app.py                      # Streamlit web dashboard (read-only)
├── scripts/
│   ├── bootstrap.py                # One-time historical data loader
│   ├── run_ingest.py               # Daily ingest entrypoint
│   ├── run_backtest.py             # Weekly backtest entrypoint
│   └── update_tickers.py           # Refreshes S&P 500 + Russell 2000 universe
├── docs/
│   ├── scoring.md                  # Scoring algorithm and factor table
│   ├── setup.md                    # One-time setup guide
│   ├── architecture.md             # This file
│   ├── faq.md                      # Common questions
│   └── research.md                 # Academic references
├── data/
│   └── tickers.txt                 # Tracked ticker universe (~3,500)
├── requirements.txt                # Dashboard deps (Streamlit Cloud)
└── requirements-ingest.txt         # Full pipeline deps (GitHub Actions + local)
```

---

## Database Schema

```sql
CREATE TABLE companies (
    cik         TEXT PRIMARY KEY,
    ticker      TEXT,
    name        TEXT,
    sic_code    TEXT,
    market_cap  BIGINT,         -- refreshed on ingest when new purchases appear
    cap_tier    TEXT            -- 'small', 'mid', 'large'
);

CREATE TABLE form4_filings (
    id               SERIAL PRIMARY KEY,
    accession_number TEXT UNIQUE,
    cik              TEXT REFERENCES companies(cik),
    filed_date       DATE,
    period_date      DATE,
    fetched_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE transactions (
    id               SERIAL PRIMARY KEY,
    filing_id        INT REFERENCES form4_filings(id),
    insider_name     TEXT,
    insider_role     TEXT,       -- raw title from filing
    role_category    TEXT,       -- 'cfo','director','officer','ceo','other'
    transaction_date DATE,
    transaction_code TEXT,       -- P, S, A, D, V, etc.
    shares           NUMERIC,
    price_per_share  NUMERIC,
    total_value      NUMERIC,
    shares_after     NUMERIC,
    is_10b51         BOOLEAN DEFAULT FALSE,
    is_direct        BOOLEAN DEFAULT TRUE
);

CREATE TABLE signals (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT,
    signal_date     DATE,
    score           INT,
    signal_type     TEXT,       -- 'BUY','WATCH','CLUSTER_BUY','LOW'
    cluster_flag    BOOLEAN DEFAULT FALSE,
    score_breakdown JSONB,      -- factor → points breakdown
    evidence        JSONB,      -- full evidence for dashboard display
    alerted         BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE backtest_runs (
    id           SERIAL PRIMARY KEY,
    run_date     DATE,
    threshold    INT,
    horizon_days INT,
    n_trades     INT,
    hit_rate     NUMERIC,
    avg_return   NUMERIC,
    sharpe       NUMERIC,
    metrics      JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);
```

---

## Free Tier Limits

| Component | Service | Free Limit | Actual Usage |
|---|---|---|---|
| Compute + scheduler | GitHub Actions | Unlimited (public repo) | ~150 min/month |
| Database | Neon PostgreSQL | 0.5 GB | ~160 MB at steady state |
| Dashboard | Streamlit Community Cloud | Unlimited public apps | 1 app |
| Alerts | Telegram Bot API | Unlimited | 1–5 messages/day |
| Market data | Yahoo Finance (via yfinance) | Informal, unlimited | ~50–100 tickers/day |
| Filing data | SEC EDGAR API | Public, unlimited | ~500 requests/day |

**Storage estimate:** ~400 transactions/day × 2 years × ~500 bytes/row ≈ 150 MB. Signals + backtest + companies ≈ 10 MB. A monthly pruning job in the ingest workflow removes transactions older than 2 years.

---

## Key Terms

**SEC** — Securities and Exchange Commission. The US government agency that requires company insiders to disclose stock trades.

**Form 4** — The SEC disclosure form that insiders must file within 2 business days of any transaction. Contains: who traded, what, how many shares, price, and whether it was a pre-arranged plan.

**EDGAR** — The SEC's public filing database. All Form 4s are freely accessible at sec.gov.

**GitHub Actions** — Free scheduled compute included with every GitHub account. Runs the daily ingest and weekly backtest on a cron schedule.

**Neon** — Free cloud-hosted PostgreSQL. Scale-to-zero (spins down when idle) — all scheduling is handled by GitHub Actions, never by in-database cron.

**Streamlit** — Python library for data dashboards. Streamlit Community Cloud hosts apps for free; apps sleep after ~12 hours of inactivity.

**10b5-1 Plan** — A legal arrangement where an insider pre-schedules future trades months in advance. Research shows these have zero predictive alpha — they're disqualified before scoring.

**Open-market purchase** — An insider buying stock through a broker at the current market price, with their own cash. The only transaction type scored for buy signals. Transaction code `P` in Form 4.

**Cluster signal** — 3 or more insiders from the same company independently buying stock within a 14-day window. The strongest single classification signal in the research.

**Alpha** — Returns above what the broad market (S&P 500 / SPY) earns over the same period.

**Basis points** — Hundredths of a percent. 82 basis points = 0.82% per month.

**Market cap tiers** — Small-cap: under $2 billion. Mid-cap: $2B–$10B. Large-cap: over $10B.

**Routine vs. opportunistic trade** — Routine: an insider who buys in the same calendar month year after year (mechanical). Opportunistic: a buy triggered by a specific view of the company's value. Research (Cohen, Malloy & Pomorski 2012) shows opportunistic trades earn ~9.8%/yr; routine trades earn ~0%.
