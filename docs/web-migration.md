# Web Dashboard Migration (Streamlit → Next.js on Vercel)

Status doc for the in-progress migration. Update the **Phase status** table as
phases land. Branch: `feat/web-vercel-migration`.

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
| 2 | Design system + `/preview` page | **Done** | (this commit) |
| 3 | `/` Signals triage (filters, Top Picks, list, evidence, "new since last visit", calendar) | Not started | |
| 4 | `/backtest` (all Streamlit tab-3 charts + tables in Recharts) | Not started | |
| 5 | `/ticker/[symbol]` + ticker search, on-demand price | Not started | |
| 6 | `/clusters`, `/sectors` (needs SIC ingest add), signal calendar on `/` | Not started | |
| 7 | `/how-it-works` (pipeline diagram, interactive scoring explainer) + `/api/revalidate` webhook in `daily_ingest.yml` | Not started | |
| 8 | Docs sweep + delete `dashboard/`, drop `streamlit`/`plotly`, replace `keep_alive.yml` | Not started | |

## What exists now (end of Phase 2)

Routes: `/` (live pipeline stats), `/backtest` `/clusters` `/sectors` `/ticker`
`/how-it-works` (styled placeholders), `/preview` (dev-only component gallery).

Components (`web/components/`):

- `app-nav`, `page-shell`, `freshness-bar`, `stat-card`, `empty-state`,
  `coming-soon`, `mode-toggle`, `theme-provider`
- `badges` — `SignalTypeBadge`, `ConvictionBadge` + `convictionFor()`, `CapTierBadge`
- `money` — `Money` (signed, `text-success` for inflow), `Return`
- `score-bar` — diverging factor bars, per-factor explainer popover with citations
- `data-table` — shared grid template (header + rows), client sort, "Load more" at 100
- `insider-table` — `DataTable` wired for `evidence.insiders[]`
- `cluster-window` — 14-day timeline of a cluster's purchases
- `charts` — `ChartCard`, `TimeSeriesChart`, `CategoryBarChart`, `Boxplot` (hand-drawn SVG)

Libs: `lib/db.ts`, `lib/format.ts`, `lib/nav.ts`, `lib/types.ts` (zod),
`lib/scoring-factors.ts` (factor metadata for the explainer + how-it-works),
`lib/queries/pipeline.ts`.

## How to resume

### Run locally

```bash
cd web
pnpm install
grep '^DATABASE_URL' ../.env > .env.local   # if not already there
pnpm dev
```

`pnpm build` connects to the DB to prerender. `pnpm lint` must be clean.
Always `rm -rf .next` if a change doesn't seem to take (Turbopack dev cache can
go stale after a `pnpm build`).

### Real data shapes (verified against live DB, 2026-08-29)

`signals.evidence` (JSONB) — newer than the shape documented in the root
`CLAUDE.md`. Key fields:

```
company_name, cap_tier, market_cap, filed_date, signal_date, current_price,
near_52wk_low, pct_above_52wk_low, price_52wk_low, research_basis[],
insiders[] { name, role, role_raw, price, total_value, pct_increase,
             shares_after, shares_bought, purchase_count, transaction_date,
             is_10b51, in_scoring_window, date_range },
cluster { is_cluster, insider_count, tight_cluster, executive_cluster,
          window_start, window_end,
          insiders[] { insider_name, role_category, transaction_date,
                       total_value, price_per_share, shares, is_direct } }
```

`signals.score_breakdown` (JSONB) — `{ factor_key: points }`, e.g.
`{ "role_director": 16, "cap_small": 15, "role_ceo": -5 }`.

`backtest_runs.metrics` (JSONB) — `distribution`, `by_score_band`,
`by_cap_tier`, `by_signal_type` (each `{ "band": { n, hit_rate, avg_return,
median_return, p25_return, p75_return, max_gain, max_loss } | null }`), `risk`,
`cluster_5064`, `iwm_small_cap`, `rolling_hit_rate_90d[] { date, hit_rate, n }`,
`detail[] { ticker, signal_type, score, cap_tier, exec_date, ticker_return,
spy_return, excess_return }` (one row per evaluated signal; drives the
excess-return-over-time chart via `exec_date`).

### Phase 3 (Signals) — starting points

- Query: `lib/queries/signals.ts` — port the Streamlit `tab_signals` SQL
  (`signals` LEFT JOIN `companies`, filters: lookback days, min score, signal
  types, cap tiers). Parse `evidence` / `score_breakdown` with `lib/types.ts`.
- Filters in URL search params via `nuqs` + localStorage fallback.
- `SignalCard` (client): header row (ticker, company, `SignalTypeBadge`,
  `ConvictionBadge`, score, `CapTierBadge`, date), expandable to `InsiderTable` +
  `ScoreBar` + `ClusterWindow` + 52-week-low badge + `research_basis`.
- Top Picks: top 3 CLUSTER_BUY else BUY, using `convictionFor()`.
- "New since last visit": localStorage timestamp, badge signals with
  `signal_date` / `evidence.filed_date` after it.
- Signal calendar: count signals per day, small heatmap.

### Phase 5 price fetch

`web/app/api/price/[ticker]/route.ts` proxies the Yahoo chart API
(`query1.finance.yahoo.com/v8/finance/chart/{ticker}`) with
`Cache-Control: s-maxage=300`. Client hook with SWR. Mirrors
`_fetch_current_price` in the Streamlit app.

### Phase 6 `/sectors` prerequisite

`companies.sic_code` exists in the schema but **nothing populates it**. EDGAR's
`submissions` JSON (already fetched in `src/ingest/edgar.py` via
`_submissions_cache`) carries `sicDescription` / `sic`. Add a small write in
`src/ingest/store.py`'s company upsert, then backfill. This is the one Python
change the migration needs, and it's additive.

### Phase 7 revalidate webhook

`web/app/api/revalidate/route.ts` — check a secret token, call
`revalidateTag("signals")` + `revalidateTag("pipeline")`. Add a step to
`.github/workflows/daily_ingest.yml` that `curl`s it after a successful ingest.
Set `REVALIDATE_SECRET` in both Vercel env and GitHub secrets.

## Deploying to Vercel

1. Vercel project → Settings → **Root Directory = `web`**.
2. Env vars (Production + Preview): `DATABASE_URL` (the Neon string, same value as
   the GitHub Actions `DATABASE_URL` secret), optional `NEXT_PUBLIC_SITE_URL`.
3. Push to `main` → production. Other branches → preview deploys.
4. `keep_alive.yml` still pings the old Streamlit URL; Phase 8 repurposes or
   removes it (Vercel does not sleep).

## Known issues / cleanup owed

- Root `CLAUDE.md` "Stack" and "Dashboard Sections" still describe Streamlit.
  Rewritten in Phase 8.
- `dashboard/app.py`, `streamlit` + `plotly` in `requirements.txt`: removed in
  Phase 8 once parity is confirmed.
- `Boxplot` whiskers render faint; revisit styling when Phase 4 uses it for real.
