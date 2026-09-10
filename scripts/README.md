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
| `bootstrap.py` | First-time history load, or to fill a coverage gap. `--start` / `--end` / `--days`, `--force`. `--force` re-fetches the XML but does **not** rewrite stored rows: `write_filing` returns early on a duplicate accession, so it cannot repair a stored transaction. Use `repair_transaction_flags.py` for that. |
| `repair_transaction_flags.py` | When `transactions.is_10b51` or `is_routine` is stale. Re-parses every stored filing holding a purchase through the production parser, and re-decides the routine rule over `data/form4/` plus the database. Dry run by default; `--apply` writes. Resumable, `--limit` caps a pass, `--refetch` discards the cache. ~35 min on a cold cache. |
| `backfill_signals.py` | After any change to `src/signals/` — rescores every stored P transaction. `--days 730 --force`. |
| `refresh_market_caps.py` | Weekly before the backtest (the workflow does this); or manually after adding companies. |
| `update_tickers.py` | Quarterly — refreshes the S&P 500 + Russell 2000 universe in `companies`. |
| `backfill_sic.py` | Once after the first ingest, then rarely — fills `companies.sic_code` / `sic_description` for `/sectors`. |
| `purge_debt_transactions.py` | Rarely — removes Table I rows reporting notes rather than stock, which store a principal amount in both the share and price fields. The parser now skips these; this clears rows written before that. `--dry-run`. |
| `apply_schema.py` | After editing `src/db/schema.sql`. Every statement there is idempotent, so this adds what is missing and touches nothing else. `--dry-run`. |
| `seed_telegram_subscriber.py` | Once, right after the first `apply_schema.py` run — puts the existing `TELEGRAM_CHAT_ID` into `telegram_subscribers` so switching to DB-driven fan-out doesn't stop alerting the one person already getting them. |
| `register_telegram_webhook.py` | Once, after deploying the `/api/telegram/webhook` route in `web/` — points Telegram's `setWebhook` at it so `/subscribe` works. `--delete` reverts to `getUpdates` for local debugging. |

## Analysis

| Script | Purpose |
| --- | --- |
| `analyze_factors.py` | Legacy univariate lift report from the latest backtest run. Superseded by `estimate_factors.py`; kept because the dashboard's factor commentary still quotes it. `--label` selects which run. |

## Scoring research

Run these in order. The whole chain takes about 12 minutes the first time and
seconds after that, because only the first step touches the network. See
`docs/scoring-improvement-plan.md` for what they are for and what they found.

| Script | Purpose |
| --- | --- |
| `build_price_panel.py` | Fetch daily adjusted prices for every ticker with a purchase, plus benchmarks, into `data/prices/panel.parquet`. ~12 min, resumable, idempotent. `--coverage` reports without fetching. |
| `verify_price_panel.py` | Prove the panel reproduces the network-measured backtest. Exits non-zero if it does not. Run after any panel rebuild. |
| `build_research_dataset.py` | Join the purchase rollup to the panel and write one labelled, scored row per insider purchase-day, including the LOW class the signals table discards. |
| `verify_scoring_parity.py` | Prove the research dataset scores purchases the way the pipeline does. Exits non-zero below 99% agreement. |
| `evaluate_model.py` | Run the evaluation protocol: time-ordered splits, purge and embargo, decile ranking, and the four baselines. `--split test` is a one-time look. |
| `estimate_factors.py` | Which candidate factors predict, multivariate, clustered on ticker, FDR-corrected. Replaces univariate lift. |
| `fit_models.py` | Fit the candidate models, drop features that drift across the split boundary, select on validation. `--report-test` for the single pre-registered test evaluation. Superseded by `hillclimb.py`: its fixed split tested on three months, 77% of them one month. |
| `hillclimb.py` | **The ruler for rankings.** Walk-forward monthly refit, each pick judged against the other purchases of its own month and volatility quintile, on the mean and the median, against a properly drawn random null. `--mde` prints the smallest effect it could tell from zero, so a candidate under it reports `BELOW RESOLUTION` instead of failing. `--label {spy,iwm,vol,sector}` picks the benchmark. Add hypotheses to `src/research/candidates.py`; changing `src/research/walkforward.py` invalidates every number it has printed. |
| `gates.py` | **The ruler for exclusions.** Same walk-forward machinery asked the other question: does dropping a class raise what is left? Spends every row rather than a 37-row decile, so it resolves 0.2 to 0.8pp where a ranking needs 3.4. Also measures the three shipped disqualifiers on the rows they discard. Hypotheses go in `src/research/gates.py`. |
| `build_form4_archive.py` | Fetch Form 4 history into `data/form4/` parquet, resumable per window via a manifest. Exists because `prune_old_data` holds Neon to 24 months, so the research sample slides rather than accumulating. Keeps `is_director` / `is_officer` / `is_ten_percent`, which the database does not store. ~25 min per calendar month of history. |
| `verify_form4_archive.py` | Prove the archive reproduces the database on the window they still share. Exits non-zero if it does not. Run after any archive fetch, and before trusting a number computed from it. |
| `audit_data.py` | 60+ data-quality checks across every table, plus the `src/db/purchases.py` rollup invariants. Prints `<-- LOOK` on anything non-zero. Read-only; run it after any pipeline change. |

## Local development

`dev/start.ps1` / `dev/start.sh` / `dev/start.bat` launch the Next.js dashboard
in `web/`. They do not touch the Python pipeline.
