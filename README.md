# Insider Signal

Automatically tracks when company executives and directors buy stock in their own companies — and alerts you before the market moves.

**Runs 100% automatically. Costs $0/month. Sends alerts to your phone.**

---

## What This Is

When a CFO buys $500,000 of their own company's stock out of personal savings, that's a meaningful signal. They know the company better than anyone. They're betting their own money. And by law, they must disclose that purchase within two business days by filing a [Form 4](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&dateb=&owner=include&count=40) with the SEC.

This system:
1. Checks the SEC every weekday morning for new Form 4 filings
2. Filters out pre-arranged trades, routine seasonal buyers, and non-purchases
3. Scores each qualifying buy based on research-backed factors (role, company size, position sizing, price context)
4. Sends a Telegram alert with full reasoning when the score is high enough
5. Shows all signals on a dashboard you can browse anytime

After the one-time setup, it runs itself permanently.

---

## Why Insider Buying Works

Decades of academic research confirm that insider purchases — specifically opportunistic, non-routine, open-market buys — are one of the few legal edges in public equity markets:

- CFO purchases: **21.5% avg annual return** (TipRanks/ResearchGate study)
- Small-cap insider buys: **+7.4% abnormal return** at 12 months (Lakonishok & Lee 2001)
- Opportunistic (non-routine) trades: **82 bps/month (~9.8%/yr)** vs. ~0% for routine trades (Cohen, Malloy & Pomorski 2012)
- Cluster buys (3+ insiders same company, same window): **~2× the alpha** of a single buy

The key is filtering. Not all insider buys carry signal — pre-arranged plans, routine seasonal trades, and option exercises have near-zero predictive value. This system filters those out first, then scores what remains.

---

## Documentation

| Document | What's In It |
|---|---|
| [docs/setup.md](docs/setup.md) | Step-by-step setup guide (~10 minutes), bootstrap instructions, verification steps |
| [docs/scoring.md](docs/scoring.md) | Full scoring algorithm: disqualifiers, all factors, signal thresholds, example alert |
| [docs/architecture.md](docs/architecture.md) | System diagram, data flow, project structure, database schema, cost breakdown, key terms |
| [docs/research.md](docs/research.md) | Academic references for every scoring factor, backtest methodology, factors not implemented and why |
| [docs/faq.md](docs/faq.md) | Common questions about the system, the research, and day-to-day operation |

---

## Quick Start

Prerequisites: Python 3.9+, free accounts at [github.com](https://github.com), [neon.tech](https://neon.tech), [share.streamlit.io](https://share.streamlit.io), and Telegram.

See [docs/setup.md](docs/setup.md) for the full guide. At a high level:

1. Create a public GitHub repo and push this code
2. Add three GitHub Secrets: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. Deploy `dashboard/app.py` to Streamlit Community Cloud with the pooled `DATABASE_URL`
4. Run the bootstrap locally to seed historical data
5. GitHub Actions runs the rest — daily at 6 AM ET, forever

---

## Stack

| Layer | Service | Cost |
|---|---|---|
| Compute + scheduler | GitHub Actions (public repo) | Free |
| Database | Neon PostgreSQL (0.5 GB free tier) | Free |
| Dashboard | Streamlit Community Cloud | Free |
| Alerts | Telegram Bot API | Free |
| Filing data | SEC EDGAR API (public) | Free |
| Market data | Yahoo Finance via yfinance | Free |

---

## Disclaimer

This system surfaces publicly disclosed SEC Form 4 filings as informational research signals. It is not financial advice and does not constitute a recommendation to buy or sell any security. Past performance of insider buying signals does not guarantee future results. Always conduct your own research before making investment decisions.
