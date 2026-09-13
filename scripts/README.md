# scripts/

Operational entrypoints. Run each with `uv run python scripts/<name>.py`, after `uv sync` if the
environment is cold. `DATABASE_URL` must be set: in the repo-root `.env` locally, or as the GitHub
Actions secret in CI.

Research entrypoints live in [`research/scripts/`](../research/README.md).

## Scheduled

| Script | Run by | What it does |
| --- | --- | --- |
| `run_ingest.py` | `daily_ingest.yml`, weekdays 11:00 UTC | Fetch new Form 4s, fill their price context, rebuild the signals for the last week of filings, and alert BUY and CLUSTER_BUY signals carrying a filing from this run. |
| `run_backtest.py` | `weekly_backtest.yml` and `rescore.yml` | Measure historical BUY and CLUSTER_BUY signals against realised prices. `--label <name>` stores a research run without replacing the dashboard's `scheduled` rows. |
| `refresh_market_caps.py` | `weekly_backtest.yml` | The only writer of `companies.market_cap` and `cap_tier`. `--force` refreshes rows that already have a cap. |
| `backfill_signals.py` | `rescore.yml` and `bootstrap.yml` | Rebuild stored signals from stored transactions. With no range it rebuilds everything; `--start`/`--end` or `--days` narrow it; `--dry-run` counts without writing. |

## By hand

| Script | When to run it |
| --- | --- |
| `bootstrap.py` | First-time history load, or to fill a gap longer than the daily job's 7-day reach. `--start`/`--end` or `--days`. It fills price context for its range. `--force` re-fetches XML but cannot rewrite a stored row. |
| `repair_transaction_flags.py` | When `transactions.is_10b51` or `is_routine` is stale. Re-parses every stored filing holding a purchase and re-decides the routine rule over `data/form4/` plus the database, so it needs the research dependency group. Dry run by default; `--apply` writes. Follow an apply with `backfill_signals.py`. |
| `audit_data.py` | After any pipeline change. Data-quality checks across every table; anything non-zero is marked `<-- LOOK`. Read-only. |
| `update_tickers.py` | Quarterly. Refreshes the S&P 500 and Russell 2000 universe in `data/tickers.txt`. |
| `backfill_sic.py` | Once after the first ingest, then rarely. Fills `companies.sic_code` and `sic_description` for `/sectors`. |
| `apply_schema.py` | After editing `src/db/schema.sql`. Every statement is idempotent. `--dry-run`. |
| `seed_telegram_subscriber.py` | Once, on a fresh deploy. Puts a local `TELEGRAM_CHAT_ID` into `telegram_subscribers` so alerts have a recipient before anyone subscribes. |
| `register_telegram_webhook.py` | Once, after deploying `web/`. Points Telegram at `/api/telegram/webhook` so `/subscribe` works. `--delete` reverts to `getUpdates`. |

## Local development

`dev/start.ps1`, `dev/start.sh` and `dev/start.bat` launch the Next.js dashboard in `web/`.
