# scripts/

Run everything with `uv run python scripts/<name>.py`. `uv sync` first if the
environment is cold. `DATABASE_URL` must be set (repo-root `.env` locally, the
GitHub Actions secret in CI).

## Scheduled entrypoints (GitHub Actions runs these)

| Script | When | What it does |
| --- | --- | --- |
| `run_ingest.py` | `daily_ingest.yml`, weekdays 11:00 UTC | Fetch new Form 4s since the last run, score them, write signals, send alerts. |
| `run_backtest.py` | `weekly_backtest.yml`, Sundays 12:00 UTC | Re-evaluate historical BUY / CLUSTER_BUY signals against realised prices. `LOOKBACK_DAYS = 730`. Pass `--label <name>` to store a research run without overwriting the dashboard's rows. |

## Operational (run by hand when needed)

| Script | When to run it |
| --- | --- |
| `bootstrap.py` | First-time history load, or to fill a coverage gap. `--start` / `--end` / `--days`, `--force`. |
| `backfill_signals.py` | After any change to `src/signals/` — rescores every stored P transaction. `--days 730 --force`. |
| `refresh_market_caps.py` | Weekly before the backtest (the workflow does this); or manually after adding companies. |
| `update_tickers.py` | Quarterly — refreshes the S&P 500 + Russell 2000 universe in `companies`. |
| `backfill_sic.py` | Once after the first ingest, then rarely — fills `companies.sic_code` / `sic_description` for `/sectors`. |
| `purge_debt_transactions.py` | Rarely — removes Table I rows reporting notes rather than stock, which store a principal amount in both the share and price fields. The parser now skips these; this clears rows written before that. `--dry-run`. |
| `apply_schema.py` | After editing `src/db/schema.sql`. Every statement there is idempotent, so this adds what is missing and touches nothing else. `--dry-run`. |

## Analysis

| Script | Purpose |
| --- | --- |
| `analyze_factors.py` | Factor-return correlation report from the latest backtest run. Read-only. `--label` selects which run. |
| `build_price_panel.py` | Once, then when the window needs widening — fetches daily adjusted prices for every ticker with a purchase, plus benchmarks, into `data/prices/panel.parquet`. ~12 min, resumable, idempotent. `--coverage` reports without fetching. |
| `build_research_dataset.py` | After any panel rebuild — joins the purchase rollup to the panel and writes one labelled row per insider purchase-day. Seconds, no network. |
| `verify_price_panel.py` | After any panel rebuild — proves the panel reproduces the network-measured backtest. Exits non-zero if it does not. |
| `audit_data.py` | 60+ data-quality checks across every table, plus the `src/db/purchases.py` rollup invariants. Prints `<-- LOOK` on anything non-zero. Read-only; run it after any pipeline change. |

## Local development

`dev/start.ps1` / `dev/start.sh` / `dev/start.bat` launch the Next.js dashboard
in `web/`. They do not touch the Python pipeline.
