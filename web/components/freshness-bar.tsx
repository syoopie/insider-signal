import { Suspense } from "react";
import { CircleDot, Database, TrendingUp } from "lucide-react";
import { getPipelineStatus, nextScheduledRuns } from "@/lib/queries/pipeline";
import { fmtDate, fmtInt, fmtRelative } from "@/lib/format";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex shrink-0 items-baseline gap-1.5" title={hint}>
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums text-foreground">{value}</span>
    </div>
  );
}

async function FreshnessContent() {
  const status = await getPipelineStatus();
  const { nextIngest } = nextScheduledRuns();

  const hasData = status.counts.filings > 0;

  return (
    <div className="mx-auto flex max-w-[1600px] items-center gap-x-4 gap-y-1 overflow-x-auto px-4 py-1.5 text-xs sm:px-6">
      <div className="flex shrink-0 items-center gap-1.5 font-medium">
        <CircleDot
          className={hasData ? "size-3 text-emerald-500" : "size-3 text-muted-foreground"}
          aria-hidden
        />
        <span>{hasData ? "Live" : "No data"}</span>
      </div>

      {hasData && (
        <>
          <Stat
            label="Last ingest"
            value={fmtRelative(status.lastFilingFetchedAt)}
            hint={`Most recent EDGAR fetch: ${status.lastFilingFetchedAt ?? "unknown"}`}
          />
          <Stat
            label="Latest signal"
            value={fmtDate(status.latestSignalDate, { withYear: true })}
            hint="Newest signal_date in the database"
          />
          <Stat
            label="Last backtest"
            value={fmtDate(status.lastBacktestRunDate, { withYear: true })}
            hint="Most recent weekly backtest run"
          />
          <Stat
            label="Signals"
            value={fmtInt(status.counts.signals)}
            hint={`${fmtInt(status.counts.buySignals)} BUY · ${fmtInt(
              status.counts.clusterBuySignals,
            )} CLUSTER_BUY`}
          />
          <div className="flex shrink-0 items-center gap-1.5 text-muted-foreground" title="Scored open-market purchases">
            <Database className="size-3" aria-hidden />
            <span className="tabular-nums">
              {fmtInt(status.counts.purchaseTransactions)} buys
            </span>
          </div>
          <Stat
            label="Next ingest"
            value={fmtRelative(nextIngest.toISOString())}
            hint={`Scheduled: ${nextIngest.toUTCString()} (GitHub Actions, weekdays 11:00 UTC)`}
          />
        </>
      )}
    </div>
  );
}

function FreshnessSkeleton() {
  return (
    <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-4 py-1.5 text-xs sm:px-6">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <TrendingUp className="size-3" aria-hidden />
        <span>Checking pipeline…</span>
      </div>
    </div>
  );
}

export function FreshnessBar() {
  return (
    <div className="border-b bg-muted/40">
      <Suspense fallback={<FreshnessSkeleton />}>
        <FreshnessContent />
      </Suspense>
    </div>
  );
}
