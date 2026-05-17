# Insider Signal

> Automatically tracks when company executives and directors buy stock in their own companies — and tells you about it before the market moves.

**Runs 100% automatically. Costs $0/month. Sends alerts to your phone.**

---

## Table of Contents

- [What This Is (Plain English)](#what-this-is-plain-english)
- [Why Insider Buying Matters](#why-insider-buying-matters)
- [How the Scoring Works](#how-the-scoring-works)
- [What an Alert Looks Like](#what-an-alert-looks-like)
- [Architecture Overview](#architecture-overview)
- [One-Time Setup (~10 minutes)](#one-time-setup-10-minutes)
- [Bootstrap: Load Historical Data](#bootstrap-load-historical-data)
- [How It Runs Daily (No Action Required)](#how-it-runs-daily-no-action-required)
- [The Dashboard](#the-dashboard)
- [Common Questions](#common-questions)
- [Cost Breakdown](#cost-breakdown)
- [Research References](#research-references)
- [Disclaimer](#disclaimer)

---

## What This Is (Plain English)

When a CEO buys $500,000 worth of their own company's stock out of their personal savings, that's a meaningful signal. They know the company better than anyone. They're betting their own money. And by law, they must publicly disclose that purchase within two business days by filing a form with the SEC (the US government's financial regulator) called a **Form 4**.

This system:
1. Checks the SEC website every weekday morning for new Form 4 filings
2. Reads each filing to see who bought what, and how much
3. Scores each purchase based on research into which insider buys actually predict future returns
4. Sends you a Telegram message when the score is high enough to be worth your attention
5. Shows everything on a dashboard you can browse anytime

You don't have to do anything after the initial setup. It runs itself forever.

---

## Why Insider Buying Matters

**Insider buying is one of the few legal edges in the stock market.** Here's why:

- Executives and directors cannot trade on *secret* information — that's illegal insider trading. But they *can* trade based on their general read of the company's prospects, even if they know things the public doesn't about industry trends, product pipelines, or competitive dynamics.
- When a CFO buys shares with personal money (not options, not RSUs, not grants — *actual cash out of their bank account*), they're making a deliberate bet that the stock is undervalued.
- Decades of academic research confirm this signal works. The average insider purchase portfolio earns roughly **6% excess returns per year** over the broad market.

**What the research says (briefly):**

| Finding | Source |
|---|---|
| Small-cap insider buys generate +7.4% abnormal returns over 12 months | Lakonishok & Lee (2001) |
| Purchase portfolios earn ~6% annualized alpha | Jeng, Metrick & Zeckhauser (2003) |
| "Opportunistic" insider buys (unplanned, non-routine) earn 82 basis points/month = ~9.8%/year | Cohen, Malloy & Pomorski (2012) |
| CFOs generate the highest returns when they buy their own stock (21.5%/yr avg) | TipRanks/ResearchGate study |
| 3+ insiders buying the same stock in a short window generates roughly 2× the alpha of a single buy | Multiple cluster studies |

**The catch:** Not all insider buys are equal. Many are pre-arranged, routine, or purely ceremonial. This system filters those out and scores only the ones research shows actually predict returns.

---

## How the Scoring Works

Every open-market purchase (a real cash buy, not a grant or option exercise) receives a score from 0 to 100 based on factors that research links to future outperformance.

### Instant Disqualifiers (Score = 0, Filing Skipped)

| Filter | Why |
|---|---|
| **10b5-1 plan** | Pre-arranged trading plans. The insider set this up months ago, not in response to current conditions. Research shows these have zero predictive value. |
| **Not a purchase** | Sales, option exercises, RSU grants, and awards are excluded. Only open-market buys (transaction code `P`) are scored. |

### Scoring Factors

| Factor | Points | Why It Matters |
|---|---|---|
| **CFO buying** | +20 | CFOs have the deepest operational view of the business — revenues, expenses, cash flow. Research shows their purchases predict the highest returns (21.5%/yr avg). |
| **Director buying** | +16 | Board members have strategic visibility and fiduciary accountability. |
| **Other named officer buying** | +12 | COO, CMO, General Counsel, etc. — operational insight, less complete than CFO. |
| **CEO buying** | +10 | Counterintuitively lower than CFO. CEOs are more public-facing and their trades attract more scrutiny and potential PR motivation. |
| **Cluster signal** (3+ insiders, same company, 14-day window) | +25 | The single strongest factor. When multiple insiders independently decide to buy at the same time, it's rarely coincidental. Research shows ~2× the alpha of a single insider buy. |
| **Small-cap company** (under $2 billion market cap) | +15 | Small companies are less followed by analysts. Information asymmetry is higher — insiders have a bigger edge. Research: +7.4% abnormal return at 12 months. |
| **Mid-cap company** ($2B–$10B) | +8 | Moderate information asymmetry benefit. |
| **Large-cap company** (over $10B) | +0 | Widely followed, heavily analysed. Insider edge is minimal. |
| **First purchase in 12+ months** | +10 | If an insider hasn't bought stock in over a year and suddenly does, it's not routine. Non-routine buys are the most predictive. |
| **Transaction value ≥ $100K** | +8 | Meaningful capital commitment relative to typical insider incomes. |
| **Transaction value ≥ $500K** | +12 | High-conviction bet. Very few people casually spend $500K+ on a single stock. |
| **Purchase within 10% of 52-week low** | +10 | Buying into weakness. The insider isn't just buying because the stock went up — they're buying when it looks bad from the outside. |
| **Purchase within 7 days of blackout period end** | +8 | Many companies prohibit insider trading around earnings. Buying immediately after the window opens suggests the insider was waiting to act. |
| **Sequenced buying** (same insider bought again within 30 days) | +8 | A second purchase shortly after the first suggests sustained conviction, not a one-time event. |

### Signal Types

| Score | Signal | What It Means |
|---|---|---|
| **65+** | 🟢 **BUY** | Strong confluence of positive factors. Research-backed buy signal. Telegram alert sent immediately. |
| **45–64** | 🟡 **WATCH** | Worth tracking. Not enough factors to trigger an alert, but logged on the dashboard. |
| **Any score + cluster flag** | 🔵 **CLUSTER_BUY** | Multiple insiders buying. Always triggers an alert regardless of score — cluster is the strongest single signal in the research. |
| **< 45** | ⚪ **LOW** | Weak signal. Logged in the database but not surfaced. |

---

## What an Alert Looks Like

Every Telegram message includes the full reasoning — no black-box signals. Here's a realistic example:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 BUY SIGNAL — $ACME (AcmeCorp Inc)
Score: 78/100 | Type: CLUSTER_BUY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHO BOUGHT:
  • Jane Smith (CFO) — 15,000 shares @ $42.10 = $631,500
  • Bob Lee (Director) — 5,000 shares @ $42.30 = $211,500
  • Tom Park (COO) — 8,000 shares @ $41.95 = $335,600
  3 insiders in a 9-day window → CLUSTER SIGNAL

WHAT THEY NOW HOLD:
  • Smith: 45,000 shares total (50% increase)
  • Lee: 12,000 shares total (71% increase)
  • Park: 22,000 shares total (57% increase)

TRANSACTION DETAILS:
  Transaction dates: May 8–16 2026
  Filing date: May 19 2026 (signal available: May 20)
  Market price at filing: $43.20 (+2.6% vs avg purchase price)

CONTEXT:
  • Stock is 8% above its 52-week low ($38.90 on Apr 2)
  • All 3 purchases are open-market (not grants or exercises)
  • None flagged as 10b5-1 pre-arranged plan
  • Smith last bought shares 14 months ago

SCORE BREAKDOWN:
  CFO purchase:              +20
  Director purchase:         +16
  Officer (COO) purchase:    +12
  Cluster signal (3 buyers): +25
  Small-cap ($1.8B):         +15
  Transaction value ≥$500K:  +12  (Smith's purchase)
  Near 52-week low:          +10  (within 10% of $38.90)
  ─────────────────────────────
  Total:                      78

RESEARCH BASIS:
  Cluster signal: ~2× alpha vs single buy (multiple studies)
  CFO signal: 21.5% avg annual return (TipRanks/ResearchGate)
  Small-cap: +7.4% abnormal return at 12 months (Lakonishok & Lee 2001)

SUGGESTED HOLD HORIZON: 60–90 days (Jeng et al. 2003 optimal window)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Architecture Overview

```
SEC EDGAR (government website, free public data)
        │
        │  Every weekday at 6 AM ET
        ▼
GitHub Actions (free cloud computer that runs on a schedule)
  ├── Fetch new Form 4 filings
  ├── Parse XML → extract insider, role, shares, price
  ├── Score each purchase (0–100)
  ├── Detect cluster signals
  └── Send Telegram alerts
        │
        ▼
Neon PostgreSQL (free cloud database, stores all history)
        │
        ▼
Streamlit Dashboard (free web app, browse signals + backtest)
        │
        ▼
Telegram Bot (free, sends alerts to your phone)
```

**Every component is free.** The system costs $0/month to run indefinitely.

### Key Terms

- **SEC** — Securities and Exchange Commission. The US government agency that requires public companies and their insiders to disclose stock trades.
- **Form 4** — The SEC form that company insiders must file within 2 business days of any stock transaction. It lists who traded, what they traded, how many shares, and the price.
- **EDGAR** — The SEC's public database of all filings. Freely accessible at sec.gov.
- **GitHub Actions** — A free service (included with any GitHub account) that runs code on a schedule. This is the "cron job" that runs the daily ingest.
- **Neon** — A free cloud-hosted PostgreSQL database. PostgreSQL is an open-source database system widely used in production.
- **Streamlit** — A Python library for building web dashboards. Streamlit Community Cloud hosts them for free.
- **Telegram Bot** — A Telegram account controlled by code rather than a person. Free to create, sends unlimited messages.
- **10b5-1 Plan** — A legal arrangement where an insider pre-schedules future trades months in advance, often to avoid accusations of insider trading. Research shows these pre-arranged trades have no predictive value — we filter them out.
- **Open-market purchase** — When an insider buys stock through a broker at the current market price, using their own cash. This is the only transaction type we score for buy signals.
- **Cluster signal** — When 3 or more insiders from the same company independently buy stock within a 14-day window. The strongest single predictor in the research.
- **Alpha** — In finance, "alpha" means returns above what the broad market earns. A 6% alpha means 6% more than the S&P 500 over the same period.
- **Basis points** — Finance jargon for hundredths of a percent. 82 basis points = 0.82% per month.
- **Market cap** — The total value of all a company's shares. Share price × total shares = market cap. Small-cap = under $2 billion. Mid-cap = $2B–$10B. Large-cap = over $10B.
- **52-week low** — The lowest price a stock has traded at in the past year. Buying near this level shows the insider is buying into weakness.

---

## One-Time Setup (~10 minutes)

You only need to do this once. After setup, the system runs itself permanently.

---

### Step 1 — GitHub Account (3 min)

GitHub is where the code lives and where the automated jobs run.

1. Go to [github.com](https://github.com) → create a free account if you don't have one
2. Click **+** (top right) → **New repository**
3. Name it `insider-signal`
4. Set visibility to **Public** (required for free GitHub Actions minutes)
5. Click **Create repository**

> **Why public?** GitHub gives unlimited free compute minutes to public repos. Private repos have a 2,000 minute/month limit. The ingest job uses ~150 minutes/month. All credentials are stored as encrypted Secrets, never in the code itself.

---

### Step 2 — Neon Database (2 min)

Neon is a free cloud-hosted PostgreSQL database. This stores all the filing data, signals, and backtest results.

1. Go to [neon.tech](https://neon.tech) → create a free account
2. Click **New Project** → name it `insider-signal` → click **Create project**
3. On the project dashboard, find the **Connection string** section
4. You need **two** connection strings:

   **Direct connection** (for GitHub Actions — the ingest job):
   ```
   postgresql://user:password@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

   **Pooled connection** (for the Streamlit dashboard):
   ```
   postgresql://user:password@ep-something-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   The pooled version has `-pooler` in the hostname. Both are shown in the Neon dashboard under different tabs.

> **Why two strings?** The dashboard may have multiple users viewing it simultaneously. The pooled connection string routes through a connection pooler that handles many concurrent users without exhausting Neon's connection limit. The ingest job uses the direct string because it's a single background process.

---

### Step 3 — Telegram Bot (2 min)

Telegram is a free messaging app. You'll create a bot that sends you alerts.

1. Open Telegram (phone or [web.telegram.org](https://web.telegram.org))
2. Search for `@BotFather` — this is Telegram's official bot-creation bot
3. Start a chat and send: `/newbot`
4. When asked for a **name** (display name), type: `Insider Signal`
5. When asked for a **username** (must end in `bot`), type something like: `my_insider_signal_bot`
6. BotFather will reply with your **bot token** — a long string like `1234567890:AAFxxxxxx`. Copy it.
7. Click the link BotFather gives you to start a chat with your new bot, then send it any message (e.g. "hello")
8. Get your **chat ID** by opening this URL in a browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
   Look for `"chat":{"id":123456789}` in the response. That number is your chat ID.

> **Tip:** If the `getUpdates` response is empty, send another message to your bot and refresh.

---

### Step 4 — GitHub Secrets (1 min)

Secrets are encrypted values stored in GitHub. They're injected into the running job as environment variables — they never appear in the code or logs.

In your GitHub repository:
1. Click **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Add all three:

| Secret Name | Where to Get It | What It's For |
|---|---|---|
| `DATABASE_URL` | Neon dashboard — the **direct** connection string | Ingest job writes filing data to the database |
| `TELEGRAM_BOT_TOKEN` | BotFather reply from Step 3 | Sends alerts and error notifications |
| `TELEGRAM_CHAT_ID` | From the `getUpdates` URL in Step 3 | Tells Telegram who to send messages to |

---

### Step 5 — Streamlit Dashboard (2 min)

Streamlit Community Cloud hosts the dashboard for free as a public web app.

1. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with your GitHub account
2. Click **Create app**
3. Select your `insider-signal` repository
4. Set **Main file path** to: `dashboard/app.py`
5. Click **Deploy**
6. Once it's live (1–2 minutes), click the **⋮ menu** → **Settings** → **Secrets**
7. Paste this (replace with your actual pooled connection string from Step 2):
   ```toml
   DATABASE_URL = "postgresql://user:password@ep-something-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
   ```
8. Click **Save**. The app will restart with the database connected.
9. Copy your app URL (e.g. `https://yourusername-insider-signal-app.streamlit.app`)
10. Back in GitHub → **Settings** → **Secrets** → add one more:

| Secret Name | Value |
|---|---|
| `STREAMLIT_APP_URL` | Your Streamlit app URL from step 9 |

This URL is used by the keep-alive workflow that pings the dashboard twice a day to prevent it from going to sleep.

---

### Step 6 — Push the Code

In your terminal, from the project directory:

```bash
# If you haven't already set up git in this folder:
git init
git remote add origin git@github.com:YOUR_USERNAME/insider-signal.git

# Push everything
git add .
git commit -m "initial commit"
git push -u origin main
```

Once pushed, GitHub Actions will automatically start running on schedule. You're done.

---

## Bootstrap: Load Historical Data

The daily ingest only fetches *new* filings (since the last run). To have 2 years of historical data for the backtest engine and dashboard from day one, run the bootstrap script once on your local machine.

```bash
# Install dependencies (Python 3.9+ required)
python3.9 -m venv .venv
source .venv/bin/activate
pip install -r requirements-ingest.txt

# Fetch the S&P 500 + Russell 2000 ticker universe first
python scripts/update_tickers.py

# Dry run — verifies everything works, no database writes
DATABASE_URL="your-direct-connection-string" python scripts/bootstrap.py --dry-run --days 30

# Full 2-year backfill (runs in background; takes 3–5 hours at the safe rate limit)
DATABASE_URL="your-direct-connection-string" nohup python -u scripts/bootstrap.py --days 730 > bootstrap.log 2>&1 &
tail -f bootstrap.log   # watch progress
```

> **Why so slow?** The SEC limits API requests to 10 per second. The bootstrap script intentionally runs at 3/sec to avoid triggering IP blocks during the large burst of requests needed to fetch 2 years of history. The daily ingest runs at 8/sec because it only fetches a small number of new filings each day.

> **Resuming after interruption:** The bootstrap saves progress as it runs. If interrupted, re-running without `--force` will resume from where it left off. Use `--force` to restart from scratch.

---

## How It Runs Daily (No Action Required)

After the initial setup and bootstrap, everything is automated.

### Weekday mornings (6 AM ET)

GitHub Actions runs `scripts/run_ingest.py`:

1. **Connects** to Neon and checks the date of the most recent filing already stored
2. **Fetches** all new Form 4s from SEC EDGAR since that date
3. **Filters** to S&P 500 + Russell 2000 companies only (avoids storing data on obscure micro-caps)
4. **Parses** each filing XML — extracts insider name, role/title, transaction type, shares, price, and whether it's a 10b5-1 plan trade
5. **Classifies** the insider's role from their job title (e.g. "Chief Financial Officer" → `cfo`)
6. **Skips** any transaction that is not an open-market purchase, or is flagged as a 10b5-1 plan
7. **Scores** each eligible purchase against the factor table
8. **Detects** cluster signals (3+ buyers of the same stock in the past 14 days)
9. **Writes** all signals to Neon
10. **Sends** Telegram alerts for BUY and CLUSTER_BUY signals
11. **Commits** a `last_run.txt` timestamp to the repo — this keeps GitHub from disabling the scheduled workflow after 60 days of no code activity

If anything in this pipeline crashes, the error handler catches it and immediately sends you a Telegram message with the error details — failures are never silent.

### Every Sunday (noon UTC)

The weekly backtest runs — it fetches all historical signals from the database, looks up actual stock prices at various points after each signal (30, 60, 90, 180 days), and computes how well the signals actually performed. Results are stored and shown on the dashboard.

### Twice daily (8 AM and 8 PM UTC)

A lightweight keep-alive workflow pings your Streamlit app URL to prevent it from going to sleep. Streamlit puts inactive apps to sleep after ~12 hours; waking takes ~30 seconds. The pings keep it warm.

---

## The Dashboard

The Streamlit dashboard at your deployed URL shows:

- **Signals table** — all recent BUY/WATCH/CLUSTER_BUY signals, sortable by score and date. Click any row to expand the full evidence panel (same detail level as the Telegram alert).
- **Backtest chart** — rolling performance of past signals vs S&P 500. Shows hit rate and average excess return at 30/60/90/180 days.
- **Per-ticker history** — search any ticker to see all insider purchases in the database, who bought, when, and at what price.

> **Note:** The dashboard has a ~30 second cold-start if no one has visited it in the past 12 hours. Subsequent page loads are fast. The keep-alive workflow reduces how often this happens.

---

## Common Questions

**Q: What's the difference between insider *trading* (illegal) and this?**

Illegal insider trading means trading on *material non-public information* — things like an upcoming merger announcement or earnings that beat estimates. The insiders this system tracks are filing legal disclosures of trades they made, which are legal because they're trading based on general business judgment (allowed), not secret tips (not allowed). The SEC requires these filings precisely to create public transparency.

**Q: Won't the market already price in insider buys immediately after the Form 4 is filed?**

Partially, for very high-profile cases. But research shows that in aggregate, insider purchase signals continue to predict outperformance for 60–90 days after filing. The biggest alpha is in small- and mid-cap stocks where fewer people are watching. This system focuses on those.

**Q: What's the suggested holding period?**

Jeng, Metrick & Zeckhauser (2003) found the optimal window is **60–90 days**. The signal is not a short-term trade. It's a medium-term thesis: "this insider bought for a reason, and the market hasn't fully priced it in yet."

**Q: Why only S&P 500 + Russell 2000?**

The free Neon database tier is 0.5 GB. There are ~2,000 Form 4 filings per day across all US public companies. Without filtering, the database would fill up in a few months. The S&P 500 + Russell 2000 covers ~3,500 companies — all large/mid-caps and the most liquid small-caps where insider signals have been studied and shown to work.

**Q: What if a ticker isn't in the universe?**

The `data/tickers.txt` file is what the system reads. You can manually add tickers to it if you want to track a specific company outside the index. Run `python scripts/update_tickers.py` quarterly to refresh S&P 500 and Russell 2000 membership.

**Q: The dashboard is slow to load.**

The first load after inactivity takes ~30 seconds (Streamlit cold start). Subsequent loads are fast. If it's slow every time, check the Streamlit Community Cloud logs for errors.

**Q: How do I know if the daily ingest is running?**

1. Check the GitHub Actions tab in your repository — you'll see a green checkmark or red X for each run.
2. The ingest sends a daily summary Telegram message even on days with no signals.
3. If the ingest fails, you'll get a Telegram error notification immediately.

**Q: Can I track insider *sales* too?**

The current system only scores purchases. Sales are less predictive — insiders sell for many reasons (diversification, taxes, life expenses) that have nothing to do with their view of the company. The research on insider purchases is much stronger. Sales are stored in the database but not scored.

**Q: The bootstrap is taking a long time.**

That's expected. Two years of Form 4 data across 3,500 companies is a lot. At 3 requests/second, it takes 3–5 hours. Let it run in the background (the `nohup` command above handles this). You can monitor progress with `tail -f bootstrap.log`.

---

## Cost Breakdown

| Component | Service | Free Limit | This System's Usage |
|---|---|---|---|
| Scheduler + compute | GitHub Actions | Unlimited (public repo) | ~150 min/month |
| Database | Neon PostgreSQL | 0.5 GB storage | ~160 MB at steady state |
| Dashboard | Streamlit Community Cloud | Unlimited public apps | 1 app |
| Alerts | Telegram Bot API | Unlimited messages | 1–5 msgs/day |
| Market data | yfinance (Yahoo Finance) | Unlimited (informal) | ~20 tickers/day |
| Filing data | SEC EDGAR API | Unlimited (public) | ~500 req/day |

**Total monthly cost: $0.**

---

## Project Structure

```
insider-signal/
├── .github/
│   └── workflows/
│       ├── daily_ingest.yml        # Runs every weekday at 6 AM ET
│       ├── weekly_backtest.yml     # Runs every Sunday at noon UTC
│       └── keep_alive.yml          # Pings Streamlit 2x/day
├── src/
│   ├── db/
│   │   ├── connection.py           # Database connection management
│   │   └── schema.sql              # Table definitions
│   ├── ingest/
│   │   ├── edgar.py                # SEC EDGAR API client (rate-limited)
│   │   ├── parser.py               # Form 4 XML parser + role classifier
│   │   └── store.py                # Database write logic
│   ├── signals/
│   │   ├── scorer.py               # Scores each transaction 0–100
│   │   ├── cluster.py              # Detects 3+ insider buys in 14-day window
│   │   └── formatter.py            # Builds human-readable evidence text
│   ├── alerts/
│   │   └── telegram.py             # Formats and sends Telegram messages
│   ├── market/
│   │   └── prices.py               # Fetches market cap + 52-week low via yfinance
│   └── backtest/
│       └── engine.py               # Validates historical signal accuracy
├── dashboard/
│   └── app.py                      # Streamlit web dashboard
├── scripts/
│   ├── bootstrap.py                # One-time: loads 2 years of historical data
│   ├── run_ingest.py               # Daily entrypoint (called by GitHub Actions)
│   └── update_tickers.py           # Refreshes S&P 500 + Russell 2000 ticker list
├── data/
│   └── tickers.txt                 # Universe of tracked tickers (~3,500)
├── requirements.txt                # Dashboard dependencies (Streamlit Cloud)
└── requirements-ingest.txt         # Full pipeline dependencies (GitHub Actions + local)
```

---

## Research References

All signal factors and thresholds are grounded in peer-reviewed academic research:

1. **Lakonishok, J. & Lee, I. (2001).** "Are Insider Trades Informative?" *Review of Financial Studies*, 14(1), 79–111.
   - Key finding: Small-cap insider purchases generate +7.4% abnormal returns over 12 months; large-cap signals have near-zero alpha.

2. **Jeng, L.A., Metrick, A., & Zeckhauser, R. (2003).** "Estimating the Returns to Insider Trading: A Performance-Evaluation Perspective." *Review of Economics and Statistics*, 85(2), 453–471.
   - Key finding: Insider purchase portfolios earn ~6% annualized alpha. Optimal holding horizon: 60–90 days.

3. **Cohen, L., Malloy, C., & Pomorski, L. (2012).** "Decoding Inside Information." *Journal of Finance*, 67(3), 1009–1043.
   - Key finding: "Opportunistic" insider buys (non-routine, unplanned) earn 82 basis points/month (~9.8%/year). Routine and pre-arranged trades have approximately zero alpha. This study is the direct research basis for the 10b5-1 plan disqualifier.

4. **Seyhun, H.N. (1988, 1992).** Multiple studies on aggregate insider trading ratios.
   - Key finding: The aggregate ratio of insider buyers to sellers predicts 60% of 12-month market returns. Basis for potential macro-level filter.

5. **TipRanks/ResearchGate CFO Study.**
   - Key finding: CFOs generate the highest returns when buying their own stock (21.5%/yr average), followed by Directors (20.7%), Officers (19.8%), and CEOs (19.3%). Counterintuitively, CEO purchases are the weakest predictor.

---

## Disclaimer

This system surfaces publicly disclosed insider trading filings (SEC Form 4s) as informational research signals. It is **not financial advice** and does not constitute a recommendation to buy or sell any security.

All signals are based on historical academic research. Past performance of insider buying signals does not guarantee future results. The stock market involves risk, and you may lose money.

Always conduct your own research and consult a qualified financial advisor before making investment decisions. The authors of this software accept no liability for investment losses.
