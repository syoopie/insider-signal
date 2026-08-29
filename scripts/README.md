# scripts/

Run everything with `uv run python scripts/<name>.py`. `uv sync` first if the
environment is cold. `DATABASE_URL` must be set (repo-root `.env` locally, the
GitHub Actions secret in CI).

## Scheduled entrypoints (GitHub Actions runs these)

| Script | When | What it does |
| --- | --- | --- |
| `run_ingest.py` | `daily_ingest.yml`, weekdays 11:00 UTC | Fetch new Form 4s since the last run, score them, write signals, send alerts. |
| `run_backtest.py` | `weekly_backtest.yml`, Sundays 12:00 UTC | Re-evaluate historical BUY / CLUSTER_BUY signals against realised prices. `LOOKBACK_DAYS = 730`. |

## Operational (run by hand when needed)

| Script | When to run it |
| --- | --- |
| `bootstrap.py` | First-time history load, or to fill a coverage gap. `--start` / `--end` / `--days`, `--force`. |
| `backfill_signals.py` | After any change to `src/signals/` — rescores every stored P transaction. `--days 730 --force`. |
| `refresh_market_caps.py` | Weekly before the backtest (the workflow does this); or manually after adding companies. |
| `update_tickers.py` | Quarterly — refreshes the S&P 500 + Russell 2000 universe in `companies`. |
| `backfill_sic.py` | Once after the first ingest, then rarely — fills `companies.sic_code` / `sic_description` for `/sectors`. |

## Analysis

| Script | Purpose |
| --- | --- |
| `analyze_factors.py` | Factor-return correlation report from the latest backtest run. Read-only. |

## Local development

`dev/start.ps1` / `dev/start.sh` / `dev/start.bat` launch the Next.js dashboard
in `web/`. They do not touch the Python pipeline.
