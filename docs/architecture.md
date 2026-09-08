# Architecture

The plain-language overview, for a reader who has not opened the code.
**`CLAUDE.md` owns the data flow, the project layout and the database schema.** They used
to be repeated here as well, which meant two copies drifting apart, and this file was the
one that went stale.

## System diagram

```
SEC EDGAR (government website, free public data)
        │
        │  Every weekday at 11:00 UTC
        ▼
GitHub Actions (free scheduled compute)
  ├── Fetch new Form 4 filings from EDGAR
  ├── Filter to the S&P 500 + Russell 2000 universe
  ├── Parse XML → insider, role, shares, price, 10b5-1 flag
  ├── Score each open-market purchase (0–100)
  ├── Detect cluster signals (3+ buyers, 14-day window)
  └── Send Telegram alerts for BUY / CLUSTER_BUY
        │
        ▼
Neon PostgreSQL (free cloud database)
        │
        ▼
Next.js dashboard on Vercel (read-only)   +   Telegram bot
```

**Total monthly cost: $0.**

## Free tier limits

| Component | Service | Free limit | Actual usage |
|---|---|---|---|
| Compute + scheduler | GitHub Actions | Unlimited (public repo) | ~150 min/month |
| Database | Neon PostgreSQL | 0.5 GB | ~200 MB at 48-month retention |
| Dashboard | Vercel Hobby | 100 GB bandwidth/month | Well under |
| Alerts | Telegram Bot API | Unlimited | 1–5 messages/day |
| Market data | Yahoo Finance | Informal, unlimited | ~50–100 tickers/day |
| Filing data | SEC EDGAR | Public, unlimited | ~500 requests/day |

`prune_old_data` runs inside the daily ingest and deletes filings older than
`store.RETENTION_MONTHS`, which is 48. That window is set by the routine check's own
three-year lookback, not by taste, and it roughly doubled storage from the 24-month era.
Re-check the headroom above before raising it again.

Research history reaches further back than the database does. `data/form4/` holds a local
parquet archive built from SEC's quarterly datasets, and it is deliberately not in Neon.

## Key terms

**SEC** — the US agency that requires company insiders to disclose their stock trades.

**Form 4** — the disclosure an insider files within two business days of a transaction. Who
traded, what, how many shares, at what price, and whether it was a pre-arranged plan.

**EDGAR** — the SEC's public filing database. Every Form 4 is free at sec.gov.

**Open-market purchase** — an insider buying through a broker at the market price with
their own cash. Transaction code `P`, and the only type scored.

**10b5-1 plan** — a legal arrangement scheduling trades months ahead. Measured at zero
predictive alpha, so these are disqualified before scoring.

**Routine vs opportunistic** — routine is an insider who buys the same calendar month year
after year, which is mechanical. Opportunistic is a buy on a specific view. Cohen, Malloy &
Pomorski (2012) find roughly 9.8%/yr for opportunistic and about 0% for routine.

**Cluster signal** — three or more insiders at one company buying within 14 days. Long
treated as the strongest signal here; measured on this data it does not order returns, and
inside the most discounted third of purchases it points the wrong way. It changes how a
signal is classified and adds no points.

**Alpha** — return above what the broad market earned over the same period.

**Basis points** — hundredths of a percent. 82 bps = 0.82%.

**Market cap tiers** — small under $2B, mid $2B–$10B, large over $10B.

**Neon** — free hosted PostgreSQL that scales to zero when idle, which is why all
scheduling is GitHub Actions and never in-database cron.
