# Insider Signal

Automated SEC Form 4 insider trading signal system. Scores insider purchase filings using research-backed factors and sends buy/watch alerts via Telegram. Runs entirely for free.

**Stack:** GitHub Actions (compute) → Neon PostgreSQL (storage) → Streamlit (dashboard) → Telegram (alerts)

---

## One-Time Setup (~10 minutes)

### Step 1 — GitHub (3 min)

1. Go to [github.com](https://github.com) and create a free account (if you don't have one)
2. Click **New repository** → name it `insider-signal` → set to **Public** → click **Create repository**
3. Copy the repo URL (e.g. `https://github.com/YOUR_USERNAME/insider-signal`)

### Step 2 — Neon Database (2 min)

1. Go to [neon.tech](https://neon.tech) and sign up for free
2. Click **New Project** → name it `insider-signal` → click **Create project**
3. On the project page, find **Connection string** — you need **two** strings:
   - **Direct** (for GitHub Actions): looks like `postgresql://user:pass@ep-xxx.region.neon.tech/neondb`
   - **Pooled** (for Streamlit dashboard): same but with `-pooler` in the hostname: `ep-xxx-pooler.region.neon.tech`
   - Both strings are shown on the Neon dashboard; copy each one

### Step 3 — Telegram Bot (2 min)

1. Open Telegram on your phone or computer
2. Search for `@BotFather` and open the chat
3. Send: `/newbot`
4. When prompted for a name, type: `InsiderSignal`
5. When prompted for a username, type something like: `my_insider_signal_bot`
6. BotFather will reply with your **bot token** — copy it (looks like `1234567890:AAF...`)
7. Start a chat with your new bot by clicking the link BotFather sends
8. Get your **chat ID** by visiting this URL in a browser (replace YOUR_TOKEN):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
   Send any message to your bot first, then open that URL. Find `"chat":{"id":XXXXXXX}` — that number is your chat ID.

### Step 4 — GitHub Secrets (1 min)

In your GitHub repo:
1. Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Add these three secrets:

| Secret Name | Value |
|---|---|
| `DATABASE_URL` | The **direct** Neon connection string |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (the number from Step 3) |

### Step 5 — Streamlit Dashboard (2 min)

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub
2. Click **New app**
3. Select your `insider-signal` repository
4. Set **Main file path** to: `dashboard/app.py`
5. Click **Deploy**
6. Once deployed, go to **⋮ menu** → **Settings** → **Secrets**
7. Paste the following (replace with your pooled connection string):
   ```toml
   DATABASE_URL = "postgresql://user:pass@ep-xxx-pooler.region.neon.tech/neondb?sslmode=require"
   ```
8. Copy the deployed app URL (e.g. `https://your-app.streamlit.app`)
9. Add it as a GitHub secret named `STREAMLIT_APP_URL` (used by the keep-alive workflow)

---

## Push the Code

```bash
git clone https://github.com/YOUR_USERNAME/insider-signal
cd insider-signal
# Copy all files from this project into the cloned directory
git add .
git commit -m "initial commit"
git push
```

---

## Bootstrap Historical Data (One-Time)

After pushing the code, run the bootstrap script once to load 2 years of historical Form 4s. This gives the signal engine and backtest engine data to work with immediately.

Run it locally (requires `DATABASE_URL` in your environment):

```bash
pip install -r requirements.txt

# Dry run first — verifies parsing works, no DB writes
DATABASE_URL="your-direct-connection-string" python scripts/bootstrap.py --dry-run --days 30

# Full 2-year backfill (takes 30–60 minutes; rate-limited to 3 req/sec)
DATABASE_URL="your-direct-connection-string" python scripts/bootstrap.py
```

Or trigger it as a one-off GitHub Actions job (add `workflow_dispatch` trigger to `daily_ingest.yml` — already included).

---

## How It Works

### Daily (6 AM ET, weekdays)

GitHub Actions runs `scripts/run_ingest.py`:
1. Fetches new Form 4 filings from SEC EDGAR since last run
2. Filters to S&P 500 + Russell 2000 universe (~3,500 tickers)
3. Parses each filing: extracts insider role, transaction type, shares, price
4. Skips: 10b5-1 plan trades, option exercises, grants — only open-market purchases scored
5. Scores each purchase against the research-backed factor table
6. Detects cluster signals (3+ insiders, same ticker, 14-day window)
7. Sends Telegram alerts for BUY and CLUSTER_BUY signals
8. Saves all signals to Neon for the dashboard

### Weekly (Sunday)

GitHub Actions runs the backtest engine — validates signal accuracy on historical data and updates the dashboard's performance chart.

---

## Signal Scoring

| Score | Type | Alert? |
|---|---|---|
| 65+ | **BUY** | Yes, Telegram |
| 45–64 | **WATCH** | Dashboard only |
| Any + cluster | **CLUSTER_BUY** | Yes, Telegram (priority) |

Scores are built from research-backed factors:
- CFO role: +20 (strongest predictive signal per TipRanks/ResearchGate)
- Cluster signal (3+ insiders, 14 days): +25 (≈2× alpha vs single buy)
- Small-cap (<$2B): +15 (7.4% abnormal return at 12mo per Lakonishok & Lee 2001)
- Transaction ≥$500K: +12
- Near 52-week low: +10
- First purchase in 12+ months: +10

Every signal includes the full evidence: who bought, how much, why it scored, and the research citation for each factor.

---

## Cost

**$0/month.** Everything runs on free tiers:
- GitHub Actions: unlimited minutes on public repos
- Neon PostgreSQL: 0.5 GB free (system uses ~160 MB)
- Streamlit Community Cloud: free for public apps
- Telegram Bot API: free
- SEC EDGAR API: free (public government data)

---

## Disclaimer

This system surfaces publicly disclosed insider trading filings (SEC Form 4s) as research signals. It is not financial advice. Past signal performance does not guarantee future returns. Always do your own research before making investment decisions.
