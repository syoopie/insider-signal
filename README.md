# Insider Signal

Tracks open-market stock purchases by company insiders, scores each one on the single factor
that measured, and alerts on the ones worth a look.

**Runs unattended on free tiers. Sends Telegram alerts. Read-only dashboard on Vercel.**

---

## What it does

1. Every weekday morning it pulls new Form 4 filings from SEC EDGAR for the S&P 500 and
   Russell 2000.
2. It keeps open-market purchases and discards pre-arranged 10b5-1 plan trades, routine
   same-month buyers and trivial amounts.
3. It scores each remaining purchase by how far below its 52-week high the stock sat on the day
   the insider bought, as a percentile of the purchases disclosed in the previous 30 days.
4. It sends a Telegram alert for a BUY, which is the top decile, and for a CLUSTER_BUY, which is
   three or more insiders buying into weakness together. Every signal is published to the
   dashboard.

A weekly backtest measures past signals against SPY.

---

## Why this factor

The literature supports the eligibility rules. Opportunistic purchases carry information and
routine or pre-planned ones do not (Cohen, Malloy & Pomorski 2012; Jeng, Metrick & Zeckhauser
2003).

The ranking rests on this repository's own walk-forward test. Over 18 months out of sample, the
top decile of the 52-week discount returned +11.13 percentage points above purchases of the same
month and volatility, with a median of +7.39pp. The same screen run on stocks nobody bought has a
median of −1.30pp, so the filing itself carries the effect. Role, company size, position size and
cluster size were each measured and none of them ranks purchases.
[`docs/findings.md`](docs/findings.md) has every number and where it came from.

---

## Repository layout

| Path | What lives there |
|---|---|
| `src/` | The production pipeline: ingest, database, scoring, backtest, alerts |
| `scripts/` | Operational entrypoints. [scripts/README.md](scripts/README.md) says when to run each |
| `research/` | Offline research: the rulers, the Form 4 archive, the labelled dataset. [research/README.md](research/README.md) |
| `tests/` | pytest suite, no database needed |
| `web/` | Next.js dashboard on Vercel, read-only. [web/README.md](web/README.md) |
| `docs/` | Long-form documentation |
| `data/` | The ticker universe. Research data is built here locally and never committed |
| `.github/workflows/` | Daily ingest, weekly backtest, rescore on push, bootstrap, tests, keepalive |

---

## Documentation

| Document | What is in it |
|---|---|
| [docs/setup.md](docs/setup.md) | One-time setup, bootstrap, verification |
| [docs/scoring.md](docs/scoring.md) | Disqualifiers, the score, clusters, signal types |
| [docs/findings.md](docs/findings.md) | What the measurements say, dated |
| [docs/research.md](docs/research.md) | The literature each rule rests on, and what was measured away |
| [docs/architecture.md](docs/architecture.md) | Plain-language overview, free-tier limits, glossary |
| [docs/faq.md](docs/faq.md) | Common questions |
| [docs/history/](docs/history/) | Dated research journals and the web migration record |
| [CLAUDE.md](CLAUDE.md) | Rules and a map for AI agents working in this repository |

---

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20+ and pnpm, and free accounts at GitHub,
[neon.tech](https://neon.tech), [vercel.com](https://vercel.com) and Telegram.

```bash
uv sync                                          # Python environment, including research tools
git config core.hooksPath .githooks              # lint and test before every push
uv run pytest -q
uv run python scripts/bootstrap.py --days 730    # seed historical filings
uv run python scripts/backfill_signals.py        # build signals from them
```

[docs/setup.md](docs/setup.md) is the full guide. In short:

1. Push the code to a public GitHub repository.
2. Add the `DATABASE_URL` and `TELEGRAM_BOT_TOKEN` secrets. Use Neon's direct URL, not the
   pooled one.
3. Deploy `web/` to Vercel with Root Directory set to `web` and its own `DATABASE_URL`.
4. Bootstrap locally to seed history.

Credentials live only in GitHub Actions secrets and Vercel environment variables. The repository
is public, and `.env` is gitignored.

---

## Scheduled jobs

| Workflow | When | What it does |
|---|---|---|
| `daily_ingest.yml` | Weekdays 11:00 UTC | Fetch, rebuild the week's signals, alert, bust the dashboard cache |
| `weekly_backtest.yml` | Sundays 12:00 UTC | Refresh market caps, then re-run the backtest |
| `rescore.yml` | Every push that changes how a signal is built | Rebuild every stored signal, then re-run the backtest |
| `keepalive.yml` | Monthly | Re-enable the scheduled workflows, which GitHub disables after 60 idle days |
| `bootstrap.yml` | Manual | Historical load over a chosen range |
| `tests.yml` | Every push and pull request | ruff, then pytest |

---

## Disclaimer

This system surfaces publicly disclosed SEC Form 4 filings as informational research signals. It
is not financial advice and does not recommend buying or selling any security. Past performance
of insider buying signals does not guarantee future results.
