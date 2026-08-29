# Web Dashboard Migration (Streamlit → Next.js on Vercel)

Record of the completed migration: what was decided, what was built, and the
things a future change needs to know. Branch: `feat/web-vercel-migration`.

---

## Goal

Replace `dashboard/app.py` (Streamlit, on Streamlit Community Cloud) with a
Next.js app in `web/`, hosted on Vercel. Rethink what's shown; keep every bit of
data and every ingest method. Everything Python stays exactly as it is.

## Decisions (settled with the user before any code)

| Question | Decision |
| --- | --- |
| "Keep the data retrieval methods" | Meant the **EDGAR Form 4 ingest** (`src/ingest/`). That is untouched. The web app's DB access is rewritten with new (TypeScript) conventions. |
| Backend | Next.js App Router queries Neon directly via `@neondatabase/serverless`. No Python on Vercel. |
| Access | Public. No auth. |
| Lead views | Daily signal triage, backtest monitoring, per-ticker research. Plus new views and a strong transparency surface (user asked for both). |
| Positions / live P&L | Not a lead view. Dropped as a standalone tab; "% since signal" context moves onto signal cards / ticker page via **on-demand client-side** price fetch. |
| Repo layout | Next app in `web/`. Vercel **Root Directory = `web/`**. |
| UI stack | shadcn/ui (`base-nova`) + Tailwind v4 + Recharts. Components owned in `web/components/ui/`. |
| Prices | Client-side, on demand only. No price table, no price cron. |
| Transparency | Always-on freshness bar **and** a deep `/how-it-works` page. |
| Charts | Recharts (via shadcn chart primitives). No Plotly. |
| Tables | Hand-rolled `DataTable` (TanStack Table v9 shipped a brand-new API mid-migration; not worth the churn for small datasets). |

## Architecture

```
SEC EDGAR ──► src/ingest ──► Neon Postgres ◄── web/ (read-only)
              (Python, unchanged)              Next.js on Vercel
                    ▲
        scripts/ + .github/workflows/ (Python, unchanged)
```

- `web/lib/db.ts` — Neon HTTP client. `query()` returns `[]` on failure so pages
  render an empty state instead of a 500.
- `web/lib/queries/*` — one typed module per concern. Each query fn is wrapped in
  `unstable_cache` with tags (`pipeline`, `signals`, `backtest`) and a 15-min
  revalidate. Pages also set `export const revalidate`.
- `web/lib/types.ts` — zod schemas for the `evidence` / `score_breakdown` /
  backtest `metrics` JSONB blobs. **Trust boundary**: parse once in the query
  layer, hand typed data to components. Tolerant (`.optional().nullable()`
  everywhere) because old rows predate newer fields.
- Data-fetching components are wrapped in `<Suspense>` in the layout/page so the
  static shell ships immediately and DB reads stream.

### Next.js 16 notes (read `web/AGENTS.md` — it has breaking changes)

- Cache Components (`cacheComponents: true`) is **not** enabled. Using the classic
  model: `unstable_cache` + route-segment `revalidate` + `revalidateTag`.
- `fetch` is not cached by default.
- Function props (e.g. a chart's `yFormat`) **cannot** cross the server→client
  boundary. Chart components (`components/charts.tsx`) take function props, so
  they must be used from client components. In practice every interactive data
  section is a client component that receives plain serialized data from a server
  page — that's the intended shape. `/preview` is `"use client"` for this reason.
- Generated types: `LayoutProps<"/">`, `PageProps<"/ticker/[symbol]">`.

## Phase status

| # | Phase | Status | Commit |
| --- | --- | --- | --- |
| 1 | Scaffold + freshness bar + placeholder routes | **Done** | `8521e98` |
| 2 | Design system + `/preview` page | **Done** | `3db1d6f` |
| 3 | `/` Signals triage (filters, Top Picks, list, evidence, "new since last visit", calendar) | **Done** | `e80ed63`, `d26a512` |
| 4 | `/backtest` (all Streamlit tab-3 charts + tables in Recharts) | **Done** | `f2bbef2` |
| 5 | `/ticker/[symbol]` + ticker search, on-demand price | **Done** | `3dcd052` |
| 6 | `/clusters`, `/sectors` (needed a SIC backfill), signal calendar on `/` | **Done** | `075c4ad` |
| 7 | `/how-it-works` (pipeline diagram, interactive scoring explainer) + `/api/revalidate` webhook in `daily_ingest.yml` | **Done** | `1d6d5be` |
| 8 | Docs sweep + delete `dashboard/`, drop `streamlit`/`plotly`, remove `keep_alive.yml` | **Done** | this commit |

## What exists now

Routes: `/` (signal triage), `/backtest`, `/clusters`, `/sectors`, `/ticker` and
`/ticker/[symbol]`, `/how-it-works`, plus `/preview` (dev-only component
gallery). API: `GET /api/price/[ticker]`, `POST /api/revalidate`.

Query modules (`web/lib/queries/`): `pipeline`, `signals`, `backtest`,
`ticker`, `clusters`, `sectors`. Each function is wrapped in `unstable_cache`
with a tag and a 15-minute revalidate.

## Things a future change needs to know

**`lib/db.ts` imports `server-only`.** A client component that imports a *value*
from a query module pulls the database client and every SQL string into the
browser bundle. This happened twice during the migration (`confidenceFor`,
`TRANSACTION_CODES`) and both times it was silent — the Neon driver is
browser-compatible, so nothing complained. It is now a build error. `import type`
is erased and always fine; shared runtime helpers live in neutral modules
(`lib/confidence.ts`, `lib/transaction-codes.ts`, `lib/signal-filters.ts`).

**Joins to `companies` must be `LEFT JOIN LATERAL ... LIMIT 1`.** The table is
keyed by CIK and `ticker` is not unique, so a plain `ON c.ticker = s.ticker`
duplicates every signal whose ticker also belongs to a predecessor registrant —
and the duplicates disagree on `cap_tier`. The Streamlit app had this bug.

**Cache keys must stay bounded.** Free-text search is applied in memory rather
than in SQL, so typing in a search box cannot explode the `unstable_cache` key
space. Chip-style filters (a handful of discrete values) are safe to cache on.

**Big JSONB gets aggregated server-side.** `backtest_runs.metrics.detail[]` holds
one row per evaluated signal across four horizons; the chart needs a monthly
mean, so it is reduced in the query module rather than shipped to the browser.

**Cluster logic is never reimplemented.** `cluster.py`'s
`cluster_from_transactions()` is the one implementation of the six eligibility
filters; the live path and `backfill_signals.py` both call it. `/clusters` reads
`signals.evidence.cluster` and recomputes nothing.

**Recharts sorts legends by value and tooltips by name.** For an ordered series
set (`30d, 60d, 90d, 180d`) that renders as `180d, 30d, 60d, 90d`. `charts.tsx`
pins both with an `itemSorter`.

## Corrections made to ported logic

The Streamlit app and the root `CLAUDE.md` both carried errors that the port
surfaced. Fixed here, and in `CLAUDE.md`:

| What | Was | Now |
| --- | --- | --- |
| `companies` join | Plain join, duplicating rows | `LEFT JOIN LATERAL ... LIMIT 1` |
| `convictionFor()` | Fell through to "BUY" for any non-cluster type, labelling a WATCH a buy | Returns null for WATCH and LOW |
| `ClusterWindow` day count | `span + 1`, so every cluster read "15 days" | `span`, matching `cluster.py`'s 14 |
| `fmtCurrency` | No billions tier — a market cap read "$1200.0M" | "$1.2B" |
| Transaction value | Sales rendered "+$4.9M" in the inflow colour | Sales are negative and use the loss colour |
| `CLAUDE.md` classification | Claimed avg ≥28 / max ≥45 | Code uses 22 / 30; doc corrected |
| Large-cap downgrade | Documented as part of `classify_signal()` | It is applied by the callers; doc corrected |

## The one Python change

`/sectors` needed industry codes and nothing had ever written
`companies.sic_code` — Form 4 XML carries no classification. Added, additively:

- `scripts/backfill_sic.py` — fills `sic_code` and `sic_description` from
  EDGAR's per-company submissions API. Idempotent, rate-limited to 6 req/s,
  interruptible; only fills gaps unless `--force`.
- `companies.sic_description` in `schema.sql`, via the existing
  `ADD COLUMN IF NOT EXISTS` pattern.

Run it once after setup; re-run occasionally as new companies appear.

## Verifying locally without Neon

Neon's HTTP driver needs Neon's proxy, so a plain local Postgres is not reachable
through `lib/db.ts` as written. Every phase of this migration was checked like
this:

1. `initdb` a scratch cluster, load `src/db/schema.sql`, seed representative rows.
   Include two `companies` rows sharing one ticker — that is the case the LATERAL
   join exists for — and a signal whose ticker has no `companies` row at all.
2. Temporarily route `query()` through `pg` behind an env flag, then
   `pnpm build && pnpm start`. Revert before committing.
3. Drive it with Playwright (`/opt/pw-browsers` has Chromium) and check both
   themes and the browser console.

That process is also what caught the two `server-only` leaks: bundling `pg` for
the browser fails loudly where bundling the Neon driver does not.

Yahoo Finance is blocked by some sandboxes. `/api/price/[ticker]` was verified
against a local stub for the success path and against the real upstream for the
failure path.

## Deploying to Vercel

1. Vercel project → Settings → **Root Directory = `web`**.
2. Env vars (Production + Preview): `DATABASE_URL` (the Neon string, same value as
   the GitHub Actions `DATABASE_URL` secret), optional `NEXT_PUBLIC_SITE_URL`.
3. Push to `main` → production. Other branches → preview deploys.
4. Optionally set `REVALIDATE_SECRET` in Vercel and `REVALIDATE_URL` +
   `REVALIDATE_SECRET` as GitHub Actions secrets, so the ingest can bust the
   dashboard cache. See `web/README.md`.

## Known gaps

- `/sectors` is only as good as `backfill_sic.py` has been run. The page states
  its own coverage rather than quietly under-reporting.
- The backtest has no per-sector breakdown; `metrics` does not carry one, and
  adding it means changing `engine.py` and re-running the backtest.
- Live quotes are per-visitor and uncached beyond the CDN's five minutes. There
  is deliberately no price table and no price cron.
- `/preview` is a dev-only gallery and is not linked from the nav.
