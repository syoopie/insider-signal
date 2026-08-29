import { Suspense } from "react";
import { LineChart } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { BacktestViews } from "@/components/backtest-views";
import { EmptyState } from "@/components/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { getBacktest } from "@/lib/queries/backtest";
import { fmtDate } from "@/lib/format";

// The backtest is rewritten once a week; an hour of staleness is invisible.
export const revalidate = 3600;

export const metadata = { title: "Backtest" };

async function Content() {
  const data = await getBacktest();

  if (!data) {
    return (
      <EmptyState
        icon={LineChart}
        title="No backtest has been recorded yet"
        description="The backtest runs weekly (Sundays, 12:00 UTC) and writes one row per hold horizon. Until the first run completes there is nothing to show."
      />
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground text-pretty">
        Latest run <span className="font-medium text-foreground">{fmtDate(data.runDate, { withYear: true })}</span>{" "}
        over the trailing 730 days. Every return below is <em>excess</em> return: the stock&apos;s
        move minus SPY&apos;s over the same window, so a positive number means the signal beat simply
        holding the index. Entry is modelled at the filing date plus four days, which is roughly when
        a person reading the same disclosure could actually have bought.
      </p>
      <BacktestViews data={data} />
    </div>
  );
}

function ContentSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[168px] rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-[380px] rounded-xl" />
      <Skeleton className="h-[280px] rounded-xl" />
    </div>
  );
}

export default function Page() {
  return (
    <PageShell
      title="Backtest"
      subtitle="How the signals actually performed against SPY, measured over the trailing 730 days."
      icon={LineChart}
    >
      <Suspense fallback={<ContentSkeleton />}>
        <Content />
      </Suspense>
    </PageShell>
  );
}
