# Setup Guide

Everything runs automatically after this one-time setup. Total time: ~10 minutes.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed locally (for the bootstrap step; it manages Python and dependencies)
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
3. In the Neon console, find the **Connection string** section
4. Copy the **direct** connection string:

   ```
   postgresql://user:password@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

   Make sure the hostname does **not** contain `-pooler`.

> **One string, two consumers.** The GitHub Actions ingest job connects over TCP
> with psycopg2 and needs the direct string. The dashboard uses Neon's HTTP
> driver (`@neondatabase/serverless`), which is one request per query and needs
> no connection pooler, so the same direct string works there too.

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
| `TELEGRAM_CHAT_ID` | Chat ID from `getUpdates` in Step 3 | Seeds the first row of `telegram_subscribers` — see below |

Alerts fan out to everyone in the `telegram_subscribers` table, not to
`TELEGRAM_CHAT_ID` directly. After the first `apply_schema.py` run, seed
yourself in as the first recipient:

```bash
uv run python scripts/seed_telegram_subscriber.py
```

From then on, anyone can join by messaging the bot `/subscribe` or adding it
to a group — but only once the webhook in `web/` is registered (Step 5
covers deploying it; `scripts/register_telegram_webhook.py` covers pointing
Telegram at it). Until you register the webhook, `/subscribe` gets no
response — the bot only has an outbound send path, not a listener.

---

## Step 5 — Vercel Dashboard (3 min)

Vercel hosts the Next.js dashboard in `web/` for free.

1. Go to [vercel.com](https://vercel.com) → sign in with GitHub → **Add New… → Project**
2. Import your `insider-signal` repository
3. **Settings → General → Root Directory**: set it to `web`. This is the one
   setting that is not the default, and the build fails without it.
4. **Settings → Environment Variables**, for Production *and* Preview:

| Variable | Value |
|---|---|
| `DATABASE_URL` | The Neon connection string from Step 2 |
| `NEXT_PUBLIC_SITE_URL` | Your deployed URL (optional; only affects link previews) |
| `REVALIDATE_SECRET` | Any long random string (optional; see below) |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather in Step 3 (optional; see below) |
| `TELEGRAM_WEBHOOK_SECRET` | Any long random string (optional; see below) |

5. **Deploy**. Pushes to `main` go to production; other branches get previews.

### Optional: instant cache refresh

The dashboard caches query results for 15 minutes. Without this step it serves
yesterday's signals for up to a quarter of an hour after each morning's ingest.

Add two GitHub Actions secrets:

| Secret | Value |
|---|---|
| `REVALIDATE_URL` | `https://<your-deployment>/api/revalidate` |
| `REVALIDATE_SECRET` | The same value you set in Vercel |

`daily_ingest.yml` calls the endpoint after a successful run. The step is skipped
when `REVALIDATE_URL` is unset, and it never fails the workflow — a missed
refresh just means the normal 15-minute expiry catches up.

### Optional: self-serve subscribe/unsubscribe

Without this, adding a recipient means editing `TELEGRAM_CHAT_ID` and
re-seeding. With it, anyone can `/subscribe` by messaging the bot or adding
it to a group.

1. Check **Settings → Deployment Protection** is off for the production
   domain — a protected domain 401s Telegram's requests before your route
   ever sees them.
2. Set `TELEGRAM_WEBHOOK_SECRET` in Vercel (the table above) and deploy.
3. Point Telegram at the deployed route:
   ```bash
   uv run python scripts/register_telegram_webhook.py \
     --url https://<your-deployment>/api/telegram/webhook
   ```
   This also stops `getUpdates` from working — Telegram delivers to one
   destination at a time. `--delete` reverts to `getUpdates` for local
   debugging.

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
# Install dependencies (run once). uv creates and manages the .venv.
uv sync

# Put the Neon connection string where the pipeline can find it
echo 'DATABASE_URL=your-direct-connection-string' > .env

# Fetch the S&P 500 + Russell 2000 ticker universe
uv run python scripts/update_tickers.py

# Dry run first — verifies everything works, no database writes
uv run python scripts/bootstrap.py --dry-run --days 14

# Minimum bootstrap (~5 min)
uv run python scripts/bootstrap.py --days 14

# Full 2-year backfill in background (~3–5 hours)
nohup uv run python -u scripts/bootstrap.py --days 730 > bootstrap.log 2>&1 &
tail -f bootstrap.log
```

> **Resuming after interruption:** Re-running skips already-stored filings. Safe to re-run at any time.

> **Why so slow for long backfills?** SEC limits requests to 10/sec. Bootstrap runs at 3/sec during large bursts to avoid IP blocks. The daily ingest runs at 8/sec because it only fetches a small number of new filings each day.

---

## Refreshing the Ticker Universe

The system tracks S&P 500 + Russell 2000 (~3,500 tickers). Index membership changes quarterly.

```bash
uv run python scripts/update_tickers.py
```

Run this quarterly, or whenever you notice a recently added company isn't appearing. You can also manually add tickers to `data/tickers.txt` to track companies outside these indexes.

---

## Verifying Everything Works

1. **GitHub Actions** — go to the Actions tab in your repo. Trigger the `Daily Ingest` workflow manually (`workflow_dispatch` button). A green checkmark confirms success.
2. **Telegram** — you'll receive a daily summary message even on days with no signals. If the ingest crashes, you get an immediate error message.
3. **Dashboard** — load your Vercel URL. The signals list populates within a day of the first successful ingest run, and the freshness bar at the top says when the pipeline last ran.
4. **Backtest** — the backtest workflow runs every Sunday. It needs signals at least 33 days old to produce results (30-day horizon + 3-day execution lag). Results appear on `/backtest` after the first Sunday with old enough data.
5. **Sectors** — `/sectors` needs industry codes, which no other job writes. Run `uv run python scripts/backfill_sic.py` once after the first ingest; it is safe to re-run and only fills gaps.
