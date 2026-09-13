# Insider Signal: Claude Reference

Rules and a map for agents working in this repository. It says what is true now. How any of it
was found is in [`docs/findings.md`](docs/findings.md) and, for the dated runs,
[`docs/history/`](docs/history/).

---

## Layout

```
src/              the production pipeline, installed as `src`
  config.py       loads .env once
  log.py          log(), phase(), setup_log_tee(), used by every entrypoint
  tickers.py      the ticker universe and clean_ticker()
  ingest/         edgar.py (EDGAR client), parser.py (Form 4 XML), fetch.py
  db/             connection.py, schema.sql
                  store.py      filings, companies, price context, retention, discount reference
                  purchases.py  the purchase rollup and the loaders built on it
                  signals.py    the only writer of the signals table
  market/         context.py (52-week discount, stored at ingest), prices.py (Yahoo, cap tiers)
  signals/        scorer.py, discount.py, cluster.py, formatter.py, constants.py
                  batch.py      build_signal(), the only signal assembler
                  rebuild.py    rebuild_signals(), the only caller that writes
  backtest/       engine.py
  alerts/         telegram.py
scripts/          operational entrypoints; scripts/README.md says when to run each
research/         offline research package, never imported by the pipeline; research/README.md
  scripts/        the rulers, the archive and dataset builders, their verifiers
tests/            pytest, no database
web/              Next.js 16 dashboard on Vercel
docs/             findings.md, scoring.md, research.md, architecture.md, setup.md, faq.md, history/
```

---

## Commit and push every change

- Stage only the files you changed. Never `git add -A` or `git add .`.
- Write a concise message saying what changed and why, and push to `origin main`. Do not ask.
- Before pushing, run `uv run ruff check` and then `uv run pytest -q`. CI lints first, so a lint
  error fails the run with the suite never having run.
- `.githooks/pre-push` runs both. Enable it once per clone with
  `git config core.hooksPath .githooks`.

---

## Changing how a signal is built

Push the change. `.github/workflows/rescore.yml` runs on every push to `main` that touches
`src/signals/**`, `src/db/purchases.py` or `src/db/signals.py`. It runs the tests, rebuilds every
stored signal with `scripts/backfill_signals.py`, re-runs `scripts/run_backtest.py` and busts the
dashboard cache. It can also be started by hand from the Actions tab.

To see what a change would do first, run `uv run python scripts/backfill_signals.py --dry-run`.

If the change is to what ranks or what is excluded, measure it before shipping it. See Research.

---

## Data flow

```
daily_ingest.yml, weekdays 11:00 UTC → scripts/run_ingest.py
  edgar.fetch_form4_index → fetch.fetch_and_parse → store.write_filing   precomputes is_routine
  store.fill_missing_price_context                                        stores pct_below_52wk_high
  signals.rebuild.rebuild_signals(from the fetch window start, at least 7 days)
    db.purchases.work_items / purchases_by_ticker
    → signals.batch.build_signal → db.signals.replace_signals
  db.signals.unsent_alerts_filed_since(fetch window start) → alerts.telegram
  store.prune_old_data, on the 1st of the month

weekly_backtest.yml, Sundays 12:00 UTC   refresh_market_caps.py, then run_backtest.py
rescore.yml, on push                     see above
keepalive.yml, monthly                   re-enables the scheduled workflows through the API
web/                                     reads every table; writes only telegram_subscribers
```

**One path builds every signal.** `build_signal` is the only assembler, `rebuild_signals` the
only caller that writes, and `replace_signals` the only writer. The daily ingest and the backfill
are two date ranges handed to the same function. Do not add a second assembly path. The last one
disagreed with the backfill on the scoring window, the cluster's as-of date, the prior purchases
the routine check saw, and the filing date the backtest trades on.

**Never read `transactions` directly for scoring.** Use `src/db/purchases.py`. One row is one
broker fill, and a 4/A amendment restates purchases under a new accession number. The rollup
picks the newest filing per (issuer, insider, date, code, ownership form) and totals it. Direct
and indirect purchases stay separate rows.

**Scores are a pure function of stored data.** Nothing in `score_transaction` or `build_signal`
reads a live price, and the evidence blob carries no live market data. An input only one path
can compute must be stored at ingest instead.

---

## Signal dating

- A work item is `(filed_date, ticker)`. Its scoring window is the 7 days of filings ending on
  that date, so a Form 4 disclosing an old trade is scored on the day it lands.
- `signal_date` is the newest trade date in the window. It is a display axis only.
- `evidence.filed_date` is the work item's date: by then everything in the signal was public.
  `engine.py` enters at `filed_date + 1 + EXEC_LAG_DAYS`.
- **If anything trades on `signal_date`, that is look-ahead bias.**
- A cluster as of a work item counts only purchases filed by that date. Prior purchases for the
  routine check and the timing facts are those filed before the window.
- `replace_signals` deletes and rewrites by `evidence.filed_date` range in one transaction. A
  `(ticker, signal_date)` that survives the rebuild keeps `alerted`.

---

## Scoring rules

Read the values in `src/signals/constants.py`, `discount.py` and `cluster.py`, not in prose.

**Hard disqualifiers**, checked in order, each scoring 0:

1. `transaction_code != 'P'`. `score_transaction` returns None.
2. `is_10b51` is true.
3. `total_value < 2,000`. A missing price counts as zero, deliberately.
4. `total_value > 1,000,000,000`, a filer error.
5. `is_routine` is true, or it is NULL and the insider bought in the same calendar month in 2 or
   more of the 3 prior years among the purchases the caller can see.

**The score** is `discount_score(pct_below_52wk_high, reference)`: the percentile of the purchase's
discount among purchases disclosed in the 30 days before its filing, from
`store.get_discount_reference`. The reference holds only filings on or before that date. With
fewer than 120 reference rows, or no 52-week high (under 200 bars of history), the purchase is
unranked: score 0, breakdown `{"price_context_missing": 0}`, never alerted. Production never
falls back to the fixed table.

**`score_breakdown` holds only what moved the score**: `{"discount_rank": N}` or
`{"price_context_missing": 0}`. Role, ownership form, holdings increase and purchase timing are
recorded per buyer on `evidence.insiders[]` (`role_category`, `is_direct`, `pct_increase`,
`timing`) and score nothing.

**Classification** happens in `classify_signal` and nowhere else:

```
cluster:     avg(participant scores) >= 80 AND (tight OR max score >= 85) AND cap_tier != 'large'
               → CLUSTER_BUY, otherwise WATCH
no cluster:  score >= 90 → BUY;  score >= 70 → WATCH;  otherwise LOW (built and counted, never stored)
```

**A cluster** (`cluster_from_transactions`) is 3 or more distinct insiders inside 14 days, each
purchase direct, at least $25,000 and not a 10b5-1 trade. Any block where 3 or more buyers share
`(shares, price, date)` or `(price, date)` is removed as an offering allocation.
`tight_cluster` means 3 inside 5 days. `executive_cluster` means a CFO, CEO, COO or chairman took
part. A cluster adds no points.

**The cooldown.** `replace_signals` suppresses a signal within 7 days of another for the same
ticker unless the score rose by 10 or the type upgraded, then removes superseded duplicates.

**A rebuild of the whole table scores the oldest ~30 days at 0.** `prune_old_data` has deleted the
filings those purchases would be ranked against. That is correct.

---

## Research

Offline, in `research/` and `research/scripts/`. `uv sync` installs the `research` dependency
group (DuckDB, pyarrow); the scheduled workflows install with `--no-default-groups` and skip it.

1. **Ask what the ruler can resolve before believing a result.** `hillclimb.py --mde` prints it.
   A result under the resolution is unmeasured, not zero.
2. **Ask about exclusions before rankings.** `gates.py` resolves roughly five times finer than
   `hillclimb.py`, because it spends every row.
3. **Hypotheses go in `research/candidates.py` and `research/gates.py`, never in the harness.**
   Changing `research/walkforward.py` invalidates every number it has printed.
4. **A finding goes in `docs/findings.md`**, dated, with where its run is recorded.

`research/README.md` has the build order. The Form 4 archive in `data/form4/` reaches back to
2016, and `research/archive.py` runs the production rollup SQL over it in DuckDB.

---

## Database

`src/db/schema.sql` is the schema, and every statement in it is idempotent.
`scripts/apply_schema.py` applies it. What the columns do not tell you:

- `companies` is keyed by CIK and `ticker` is not unique. Web joins use
  `LEFT JOIN LATERAL ... LIMIT 1`.
- `companies.cap_tier` has one writer, `refresh_market_caps.py`, weekly. A company new to the
  database is `unknown` until the next Sunday.
- `transactions.is_10b51` and `is_routine` are written once at ingest. `is_routine` records
  what the database could see then, and pruning can make it stale.
  `scripts/repair_transaction_flags.py` re-derives both; follow `--apply` with a rescore.
- `transactions.pct_below_52wk_high` is stored at ingest by `src/market/context.py`.
  `bootstrap.py` fills it over its own range.
- Only `transaction_code = 'P'` is ever scored.
- `backtest_runs.run_label = 'scheduled'` is the weekly job and the only label the dashboard
  reads. `run_backtest.py --label <name>` stores a research run beside it, and
  `save_backtest_results` replaces today's rows for its own label only.
- `backtest_runs.metrics.detail` is the per-signal return list the dashboard's return chart is
  built from, keyed by `exec_date`.
- `telegram_subscribers` is written only by the web webhook and read by
  `store.get_active_telegram_subscribers`. Every alert fans out to all active rows.
- `store.RETENTION_MONTHS` is 48, the smallest window holding the routine check's 3-year
  lookback. Neon's free tier is 500MB; re-check the headroom before raising it.

---

## Web (`web/`)

- **Read `web/AGENTS.md` before editing.** Next.js 16 has breaking changes and ships its own docs
  in `node_modules/next/dist/docs/`.
- **`web/lib/db.ts` imports `server-only`.** A client component that imports a *value* from
  `web/lib/queries/` fails the build. `import type` is fine. Shared runtime helpers go in neutral
  modules such as `lib/scoring-factors.ts`, `lib/confidence.ts` and `lib/signal-filters.ts`.
- The app never writes to the database, except `app/api/telegram/webhook/route.ts`, which owns
  its own write client.
- JSONB is parsed once, by the zod schemas in `lib/types.ts`. Queries are wrapped in
  `unstable_cache` with the tags `pipeline`, `signals` and `backtest` and a 15-minute revalidate.
  `POST /api/revalidate` with the bearer token busts all three.
- Anything that reads `backtest_runs` for display must filter on `run_label = 'scheduled'`.
- Check a change with `pnpm exec tsc --noEmit` and `pnpm lint` in `web/`.

---

## Operations

| Task | Command |
|---|---|
| Fill an ingest gap | `uv run python scripts/bootstrap.py --start YYYY-MM-DD --end YYYY-MM-DD` |
| Rebuild signals | `uv run python scripts/backfill_signals.py [--start D --end D \| --days N] [--dry-run]` |
| Re-run the backtest | `uv run python scripts/run_backtest.py [--label NAME]` |
| Check data quality | `uv run python scripts/audit_data.py`, which marks anything non-zero `<-- LOOK` |
| Repair stale flags | `uv run python scripts/repair_transaction_flags.py [--apply]` |

`bootstrap.py --force` re-fetches XML but cannot repair a stored row, because `write_filing` does
nothing on a duplicate accession number.

The daily ingest reaches at most 7 days back, because EDGAR caps a query at 10,000 hits. A longer
outage stays missing until bootstrapped, and the run sends a Telegram warning naming the command.

---

## Known traps

- Neon scales to zero, so the first query after an idle period is slow. GitHub Actions uses the
  direct URL, never the `-pooler` one.
- A scheduled workflow was disabled: re-enable it from the Actions tab and check that
  `keepalive.yml` is running.
- No alerts: `telegram_subscribers` has no active row (`scripts/seed_telegram_subscriber.py`), or
  the signal's newest filing predates that run's fetch window.
- `/subscribe` gets no reply: the webhook is not registered (`scripts/register_telegram_webhook.py`),
  Vercel Deployment Protection is on, or `TELEGRAM_WEBHOOK_SECRET` does not match.
- A signal is missing from the dashboard: check the score and type filters, the 7-day cooldown,
  and `evidence.filed_date`.
- DRIP and fund-partnership noise (WERN, EPAM, GABC, CMPO-style LLCs) is caught by the $25k floor,
  the direct-only rule and the identical-block filter.

---

## Never

- Commit `.env` or any credential. The repository is public.
- Use `get_conn()` outside a `with` block.
- Add non-deterministic ordering to anything that builds signals or research datasets.
- Change a threshold or disqualifier without measuring it first. The push rescores everything.
- Write to the database from `web/` pages or `lib/`.
- Write a comment that says what code does. Comment only the non-obvious why.
