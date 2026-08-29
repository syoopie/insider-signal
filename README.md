# Insider Signal

Tracks when company executives and directors buy stock in their own companies, scores
each purchase against a research-backed model, and alerts on the ones worth looking at.

**Runs unattended on free tiers. Sends Telegram alerts. Read-only dashboard on Vercel.**

---

## What This Is

When a CFO buys $500,000 of their own company's stock out of personal savings, that is a
meaningful signal. They know the company better than anyone, they are betting their own
money, and by law they must disclose the purchase within two business days on a
[Form 4](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&dateb=&owner=include&count=40).

The pipeline:

1. Pulls new Form 4 filings from SEC EDGAR every weekday morning.
2. Discards anything that is not an open-market purchase, plus pre-arranged 10b5-1 plan
   trades, routine seasonal buyers, and trivial amounts.
3. Rolls each insider's broker fills up into one purchase, then scores it on role,
   company size, position sizing, and prior-purchase history.
4. Detects clusters: three or more independent insiders buying the same company inside a
   14-day window.
5. Sends a Telegram alert for BUY and CLUSTER_BUY, and publishes everything to the
   dashboard.

A weekly backtest re-evaluates every historical signal against realised prices, measured
as excess return over SPY.

---

## Why Insider Buying Works

Opportunistic, non-routine, open-market insider purchases are one of the few documented
legal edges in public equity:

- Small-cap insider buys: **+7.4% abnormal return** at 12 months (Lakonishok & Lee 2001)
- Opportunistic trades: **82 bps/month** versus roughly zero for routine trades
  (Cohen, Malloy & Pomorski 2012)
- Purchase portfolios: **~6% annualised alpha** (Jeng, Metrick & Zeckhauser 2003)
- Cluster buys: roughly **2x the alpha** of a single insider buy

Filtering is most of the work. Pre-arranged plans, routine seasonal trades, option
exercises, and awards carry near-zero predictive value, so they are excluded before
anything is scored.

---

## Repository Layout

| Path | What lives there |
|---|---|
| `src/ingest/` | EDGAR client and Form 4 XML parser |
| `src/db/` | Connection, schema, writes, and the purchase-rollup query |
| `src/signals/` | Scoring model, cluster detection, evidence blob |
| `src/backtest/` | Backtest engine and metric computation |
| `src/market/` | Yahoo Finance price and market-cap lookups |
| `src/alerts/` | Telegram Bot API client |
| `scripts/` | Entrypoints and operational tooling; see [scripts/README.md](scripts/README.md) |
| `tests/` | pytest suite, no database required |
| `web/` | Next.js dashboard deployed to Vercel, read-only |
| `docs/` | Long-form documentation |
| `data/` | The ticker universe |
| `.github/workflows/` | The three scheduled workflows |

---

## Documentation

| Document | What's In It |
|---|---|
| [docs/setup.md](docs/setup.md) | Setup guide, bootstrap instructions, verification steps |
| [docs/scoring.md](docs/scoring.md) | Disqualifiers, every scoring factor, signal thresholds |
| [docs/architecture.md](docs/architecture.md) | System diagram, data flow, database schema, cost breakdown |
| [docs/research.md](docs/research.md) | Academic basis for each factor, backtest methodology, factors deliberately not implemented |
| [docs/faq.md](docs/faq.md) | Common questions about the system and its day-to-day operation |
| [docs/web-migration.md](docs/web-migration.md) | How the dashboard moved from Streamlit to Next.js, and why |
| [docs/scoring-improvement-plan.md](docs/scoring-improvement-plan.md) | Why the current weights cannot be trusted, and the staged plan to re-derive them |
| [scripts/README.md](scripts/README.md) | When to run each script |
| [web/README.md](web/README.md) | Local dev, environment variables, deployment |
| [CLAUDE.md](CLAUDE.md) | Authoritative reference for AI agents working on this codebase |

---

## Quick Start

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, pnpm, and free
accounts at GitHub, [neon.tech](https://neon.tech), [vercel.com](https://vercel.com),
and Telegram.

```bash
uv sync                                   # install the Python environment
uv run pytest -q                          # 76 tests, no database needed
uv run python scripts/bootstrap.py --days 730   # seed historical filings
```

See [docs/setup.md](docs/setup.md) for the full guide. At a high level:

1. Push this code to a public GitHub repo.
2. Add three GitHub Secrets: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   Use Neon's **direct** URL, not the pooled one.
3. Deploy `web/` to Vercel with **Root Directory = `web`** and its own `DATABASE_URL`.
4. Bootstrap locally to seed history.
5. GitHub Actions takes it from there.

Credentials live only in GitHub Actions Secrets and Vercel environment variables. The
repo is public; `.env` is gitignored and local-only.

---

## Scheduled Jobs

| Workflow | When | What it does |
|---|---|---|
| `daily_ingest.yml` | Weekdays 11:00 UTC | Fetch, score, alert, then bust the dashboard cache |
| `weekly_backtest.yml` | Sundays 12:00 UTC | Refresh market caps, then re-run the backtest |
| `bootstrap.yml` | Manual | Historical load over a configurable date range |

GitHub Actions disables scheduled workflows after 60 days of repository inactivity, so
the daily ingest commits `last_run.txt` to keep the repo live.

---

## After Changing the Scoring Model

Any edit under `src/signals/` leaves every stored signal stale. The full sequence:

```bash
uv run pytest -q
uv run python scripts/backfill_signals.py --days 730 --force   # ~8 min
uv run python scripts/run_backtest.py                          # ~30 min
uv run python scripts/audit_data.py                            # data-quality check
```

---

## Stack

| Layer | Service | Cost |
|---|---|---|
| Compute and scheduler | GitHub Actions (public repo) | Free |
| Database | Neon PostgreSQL, 0.5 GB free tier | Free |
| Dashboard | Next.js 16 on Vercel | Free |
| Alerts | Telegram Bot API | Free |
| Filing data | SEC EDGAR API | Free |
| Prices and market caps | Yahoo Finance chart API, EDGAR XBRL frames | Free |

---

## Disclaimer

This system surfaces publicly disclosed SEC Form 4 filings as informational research
signals. It is not financial advice and does not constitute a recommendation to buy or
sell any security. Past performance of insider buying signals does not guarantee future
results. Always conduct your own research before making investment decisions.
