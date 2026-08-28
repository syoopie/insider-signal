import { Suspense } from "react";
import { LayoutGrid } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { StatCard } from "@/components/stat-card";
import { Skeleton } from "@/components/ui/skeleton";
import { getPipelineStatus, nextScheduledRuns } from "@/lib/queries/pipeline";
import { fmtDate, fmtInt, fmtRelative } from "@/lib/format";

// Signals change at most once per weekday (ingest runs 11:00 UTC); an hour of
// staleness is invisible and keeps the page served from the edge.
export const revalidate = 3600;

export const metadata = { title: "Signals" };

async function Overview() {
  const status = await getPipelineStatus();
  const { nextIngest, nextBacktest } = nextScheduledRuns();

  if (status.counts.filings === 0) {
    return (
      <div className="rounded-lg border border-dashed px-6 py-16 text-center">
        <p className="font-medium">No pipeline data reachable</p>
        <p className="mt-1 text-sm text-muted-foreground">
          The database is empty or unreachable. Set <code>DATABASE_URL</code> and run the ingest
          pipeline.
        </p>
      </div>
    );
  }

  const capCoverage =
    status.counts.companies > 0
      ? Math.round((status.counts.companiesWithCap / status.counts.companies) * 100)
      : 0;

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard
          label="Signals"
          value={fmtInt(status.counts.signals)}
          hint={`${fmtInt(status.counts.buySignals)} BUY · ${fmtInt(
            status.counts.clusterBuySignals,
          )} CLUSTER_BUY`}
        />
        <StatCard
          label="Scored purchases"
          value={fmtInt(status.counts.purchaseTransactions)}
          hint={`of ${fmtInt(status.counts.transactions)} total Form 4 transactions`}
        />
        <StatCard
          label="Companies tracked"
          value={fmtInt(status.counts.companies)}
          hint={`${capCoverage}% have a resolved market cap`}
        />
        <StatCard
          label="Filings ingested"
          value={fmtInt(status.counts.filings)}
          hint={`since ${fmtDate(status.coverageStart, { withYear: true })}`}
        />
        <StatCard
          label="Last ingest"
          value={fmtRelative(status.lastFilingFetchedAt)}
          variant="text"
          hint={`Next: ${fmtRelative(nextIngest.toISOString())} (weekdays 11:00 UTC)`}
        />
        <StatCard
          label="Latest signal"
          value={fmtDate(status.latestSignalDate, { withYear: true })}
          variant="text"
        />
        <StatCard
          label="Last backtest"
          value={fmtDate(status.lastBacktestRunDate, { withYear: true })}
          variant="text"
          hint={`Next: ${fmtRelative(nextBacktest.toISOString())} (Sundays 12:00 UTC)`}
        />
      </div>

      <div className="rounded-lg border bg-muted/30 px-5 py-4 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">Migration in progress</p>
        <p className="mt-1 text-pretty">
          This is the new Next.js dashboard replacing the Streamlit app. The signal triage list,
          backtest views, cluster timeline, sector breakdown, per-ticker research, and the full
          methodology page are landing one at a time. The numbers above are live from the database.
        </p>
      </div>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 7 }).map((_, i) => (
        <Skeleton key={i} className="h-[104px] rounded-xl" />
      ))}
    </div>
  );
}

export default function Page() {
  return (
    <PageShell
      title="Signals"
      subtitle="Research-backed buy signals from SEC Form 4 insider purchases."
      icon={LayoutGrid}
    >
      <Suspense fallback={<OverviewSkeleton />}>
        <Overview />
      </Suspense>
    </PageShell>
  );
}
