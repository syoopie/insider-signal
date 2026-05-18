# Setup Guide

Everything runs automatically after this one-time setup. Total time: ~10 minutes.

---

## Prerequisites

- Python 3.9+ installed locally (for the bootstrap step)
- A free GitHub account
- A Telegram account (phone or web)

---

## Step 1 — GitHub Repository (3 min)

GitHub is where the code lives and where the scheduled jobs run for free.

1. Go to [github.com](https://github.com) → create a free account if you don't have one
2. Click **+** (top right) → **New repository**
3. Name it `insider-signal`
4. Set visibility to **Public** — this is required for unlimited free GitHub Actions minutes
5. Click **Create repository**

> **Why public?** GitHub gives unlimited free compute to public repos. Private repos are limited to 2,000 minutes/month. This system uses ~150 minutes/month. All credentials are stored as encrypted Secrets — they never appear in the code or logs.

---

## Step 2 — Neon Database (2 min)

Neon provides a free cloud-hosted PostgreSQL database.

1. Go to [neon.tech](https://neon.tech) → create a free account
2. Click **New Project** → name it `insider-signal` → click **Create project**
3. On the project dashboard, find the **Connection string** section
4. Copy **two** connection strings:

   **Direct connection** (for GitHub Actions ingest job):
   ```
   postgresql://user:password@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

   **Pooled connection** (for the Streamlit dashboard):
   ```
   postgresql://user:password@ep-something-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   The pooled version has `-pooler` in the hostname. Both strings are available in the Neon dashboard under separate tabs.

> **Why two strings?** The dashboard can have multiple concurrent visitors. The pooled connection string routes through a connection pooler that handles concurrency without exhausting Neon's connection limit. The ingest job uses the direct string because it's a single background process.

---

## Step 3 — Telegram Bot (2 min)

You'll create a bot that sends you alerts.

1. Open Telegram (phone or [web.telegram.org](https://web.telegram.org))
2. Search for `@BotFather` — Telegram's official bot-creation tool
3. Send: `/newbot`
4. When asked for a name, enter: `Insider Signal`
5. When asked for a username (must end in `bot`), enter something like: `my_insider_signal_bot`
6. BotFather replies with your **bot token** — a string like `1234567890:AAFxxxxxx`. Copy it.
7. Click the link BotFather provides to open your new bot, then send it any message (e.g. "hello")
8. Get your **chat ID** — open this URL in a browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
   Find `"chat":{"id":123456789}` in the response. That number is your chat ID.

> **Tip:** If `getUpdates` returns an empty result, send another message to your bot and refresh.

---

## Step 4 — GitHub Secrets (1 min)

Secrets are encrypted values injected into the running jobs as environment variables. They never appear in code or logs.

In your GitHub repository:
1. Go to **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Add all three:

| Secret Name | Value | Purpose |
|---|---|---|
| `DATABASE_URL` | Neon **direct** connection string | Ingest job writes to the database |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather in Step 3 | Sends alerts and error notifications |
| `TELEGRAM_CHAT_ID` | Chat ID from `getUpdates` in Step 3 | Tells Telegram who to message |

---

## Step 5 — Streamlit Dashboard (2 min)

Streamlit Community Cloud hosts the dashboard for free.

1. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub
2. Click **Create app**
3. Select your `insider-signal` repository
4. Set **Main file path** to: `dashboard/app.py`
5. Click **Deploy**
6. Once live (1–2 minutes), go to **⋮ menu** → **Settings** → **Secrets**
7. Paste the following (replace with your actual pooled connection string from Step 2):
   ```toml
   DATABASE_URL = "postgresql://user:password@ep-something-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
   ```
8. Click **Save**. The app restarts with the database connected.
9. Copy your app URL (e.g. `https://yourusername-insider-signal-app.streamlit.app`)
10. Back in GitHub → **Settings** → **Secrets** → add:

| Secret Name | Value |
|---|---|
| `STREAMLIT_APP_URL` | Your Streamlit app URL from step 9 |

This URL is used by the keep-alive workflow that pings the dashboard twice a day to prevent it from going to sleep.

---

## Step 6 — Push the Code

From the project directory in your terminal:

```bash
# Set up git if you haven't already
git init
git remote add origin git@github.com:YOUR_USERNAME/insider-signal.git

# Push everything
git add .
git commit -m "initial commit"
git push -u origin main
```

Once pushed, GitHub Actions starts running on schedule. You're done with setup.

---

## Bootstrap: Load Historical Data

The daily ingest only fetches new filings (since the last run). On first run the database is empty, so the bootstrap script seeds it with historical data.

**How much history to load:**

| `--days` | Time | What it enables |
|---|---|---|
| **14** (minimum) | ~5 min | Cluster detection works immediately. Some scoring factors (first purchase in 12+ months, routine-trader filter) are understated until more history accumulates. |
| **365** | ~1–2 hours | Full annual scoring accuracy including routine-trader detection. |
| **730** | ~3–5 hours | Full 2-year backtest history visible in the dashboard. |

```bash
# Install dependencies (run once)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-ingest.txt

# Fetch the S&P 500 + Russell 2000 ticker universe
python3 scripts/update_tickers.py

# Dry run first — verifies everything works, no database writes
DATABASE_URL="your-direct-connection-string" python3 scripts/bootstrap.py --dry-run --days 14

# Minimum bootstrap (~5 min)
DATABASE_URL="your-direct-connection-string" python3 scripts/bootstrap.py --days 14

# Full 2-year backfill in background (~3–5 hours)
DATABASE_URL="your-direct-connection-string" nohup python3 -u scripts/bootstrap.py --days 730 > bootstrap.log 2>&1 &
tail -f bootstrap.log
```

> **Resuming after interruption:** Re-running skips already-stored filings. Safe to re-run at any time.

> **Why so slow for long backfills?** SEC limits requests to 10/sec. Bootstrap runs at 3/sec during large bursts to avoid IP blocks. The daily ingest runs at 8/sec because it only fetches a small number of new filings each day.

---

## Refreshing the Ticker Universe

The system tracks S&P 500 + Russell 2000 (~3,500 tickers). Index membership changes quarterly.

```bash
python3 scripts/update_tickers.py
```

Run this quarterly, or whenever you notice a recently added company isn't appearing. You can also manually add tickers to `data/tickers.txt` to track companies outside these indexes.

---

## Verifying Everything Works

1. **GitHub Actions** — go to the Actions tab in your repo. Trigger the `Daily Ingest` workflow manually (`workflow_dispatch` button). A green checkmark confirms success.
2. **Telegram** — you'll receive a daily summary message even on days with no signals. If the ingest crashes, you get an immediate error message.
3. **Dashboard** — load your Streamlit URL. The signals table should populate within a day of the first successful ingest run.
4. **Backtest** — the backtest workflow runs every Sunday. It needs signals at least 33 days old to produce results (30-day horizon + 3-day execution lag). Results appear in the dashboard after the first Sunday with old enough data.
