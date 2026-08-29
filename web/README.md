# Insider Signal — Web Dashboard

Next.js (App Router) dashboard that replaces the Streamlit app in `../dashboard/`.
Read-only: it queries the Neon database the Python pipeline writes to and never
mutates anything.

## Stack

| Concern | Choice |
| --- | --- |
| Framework | Next.js 16 (App Router, Turbopack) |
| Language | TypeScript |
| Styling | Tailwind v4 + shadcn/ui (`base-nova` style, components in `components/ui/`) |
| Charts | Recharts (via shadcn chart primitives) |
| Tables | TanStack Table |
| Database | `@neondatabase/serverless` (HTTP), typed query modules in `lib/queries/` |
| URL state | `nuqs` |
| Client data | SWR (on-demand price fetches only) |
| Theme | `next-themes`, light + dark, defaults to system |

## Local development

```bash
cd web
pnpm install
# DATABASE_URL must be set. Copy it from the repo-root .env:
#   grep '^DATABASE_URL' ../.env > .env.local
pnpm dev            # http://localhost:3000
```

`.env.local` is gitignored. The only variable the app needs is `DATABASE_URL`
(the same Neon connection string the Python side uses). `NEXT_PUBLIC_SITE_URL`
is optional and only affects Open Graph metadata.

```bash
pnpm build          # production build (connects to the DB to prerender)
pnpm start           # serve the production build
pnpm lint            # eslint
```

## Deployment (Vercel)

The Vercel project's **Root Directory** must be set to `web`. Everything else is
default Next.js detection.

Environment variables to set in Vercel (Production + Preview):

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Neon connection string (same as the `DATABASE_URL` GitHub Actions secret) |
| `NEXT_PUBLIC_SITE_URL` | The deployed URL, e.g. `https://insider-signal.vercel.app` (optional) |
| `REVALIDATE_SECRET` | Shared secret for `POST /api/revalidate` (optional; without it the route refuses every request) |

Pushes to `main` deploy to production; other branches get preview deployments.

## Caching

Data queries are wrapped in `unstable_cache` with a 15-minute revalidation and
tags (`pipeline`, `signals`, `backtest`). The pipeline changes at most once per
weekday, so pages serve from the edge and refresh on their own.

`POST /api/revalidate` closes the gap right after an ingest, when the dashboard
would otherwise serve yesterday's signals for another quarter of an hour. It
takes `Authorization: Bearer $REVALIDATE_SECRET` and busts all three tags with
the `"max"` profile, so no visitor ever blocks on the refresh.

To wire it up, set two GitHub Actions secrets:

| Secret | Value |
| --- | --- |
| `REVALIDATE_URL` | `https://<your-deployment>/api/revalidate` |
| `REVALIDATE_SECRET` | The same value as the Vercel `REVALIDATE_SECRET` env var |

`daily_ingest.yml` calls it after a successful run. The step is skipped when
`REVALIDATE_URL` is unset and never fails the workflow — a missed bust just
means the 15-minute TTL catches up on its own.

## Layout

```
app/                 route segments; each page is a server component
  layout.tsx         nav + freshness bar + theme + footer
  page.tsx           Signals (Phase 3 builds the real triage list)
  backtest/ clusters/ sectors/ ticker/ how-it-works/
components/          domain components (StatCard, FreshnessBar, PageShell, …)
  ui/                shadcn primitives (owned, restyleable)
lib/
  db.ts              Neon client + query helpers
  format.ts          currency / percent / date formatting
  nav.ts             single source of truth for primary navigation
  queries/           one typed module per data concern
```
