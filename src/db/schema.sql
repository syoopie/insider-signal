-- Insider Signal System — Database Schema

CREATE TABLE IF NOT EXISTS companies (
    cik         TEXT PRIMARY KEY,
    ticker      TEXT,
    name        TEXT,
    sic_code    TEXT,
    market_cap  BIGINT,
    cap_tier    TEXT,           -- 'small' (<2B), 'mid' (2B-10B), 'large' (>10B)
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);

CREATE TABLE IF NOT EXISTS form4_filings (
    id               SERIAL PRIMARY KEY,
    accession_number TEXT UNIQUE NOT NULL,
    cik              TEXT REFERENCES companies(cik),
    filed_date       DATE NOT NULL,
    period_date      DATE,
    fetched_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_filings_filed_date ON form4_filings(filed_date);
CREATE INDEX IF NOT EXISTS idx_filings_cik ON form4_filings(cik);

CREATE TABLE IF NOT EXISTS transactions (
    id               SERIAL PRIMARY KEY,
    filing_id        INT REFERENCES form4_filings(id) ON DELETE CASCADE,
    insider_name     TEXT,
    insider_role     TEXT,
    role_category    TEXT,       -- 'cfo','ceo','director','officer','other'
    transaction_date DATE NOT NULL,
    transaction_code TEXT NOT NULL,  -- P, S, A, D, V, X, M, etc.
    shares           NUMERIC,
    price_per_share  NUMERIC,
    total_value      NUMERIC,
    shares_after     NUMERIC,
    is_10b51         BOOLEAN DEFAULT FALSE,
    is_direct        BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_tx_transaction_date ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_tx_filing_id ON transactions(filing_id);
CREATE INDEX IF NOT EXISTS idx_tx_code ON transactions(transaction_code);

CREATE TABLE IF NOT EXISTS signals (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    signal_date     DATE NOT NULL,
    score           INT NOT NULL,
    signal_type     TEXT NOT NULL,  -- 'BUY','WATCH','CLUSTER_BUY','LOW'
    cluster_flag    BOOLEAN DEFAULT FALSE,
    score_breakdown JSONB,
    evidence        JSONB,
    alerted         BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_ticker_date ON signals(ticker, signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id           SERIAL PRIMARY KEY,
    run_date     DATE NOT NULL,
    threshold    INT NOT NULL,
    horizon_days INT NOT NULL,
    n_trades     INT,
    hit_rate     NUMERIC,
    avg_return   NUMERIC,
    sharpe       NUMERIC,
    metrics      JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);
