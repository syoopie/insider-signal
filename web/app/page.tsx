import { Suspense } from "react";
import { LayoutGrid } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { SignalBoard } from "@/components/signal-board";
import { SignalCalendar } from "@/components/signal-calendar";
import { SignalFilters } from "@/components/signal-filters";
import { Skeleton } from "@/components/ui/skeleton";
import { applyDayFilter, getSignalCalendar, getSignals } from "@/lib/queries/signals";
import { loadSignalFilters, matchesQuery, normalizeFilters } from "@/lib/signal-filters";
import type { SignalFilters as Filters } from "@/lib/signal-filters";

export const metadata = { title: "Signals" };

async function Results({ filters }: { filters: Filters }) {
  const [signals, calendar] = await Promise.all([
    getSignals(filters.days, filters.min, filters.types, filters.caps),
    getSignalCalendar(filters.days),
  ]);

  // The day pin narrows the cached window; the text query is applied here only so
  // a shared `?q=` link is right on first paint — the board re-applies it live.
  const rows = applyDayFilter(signals, filters).filter((s) => matchesQuery(s, filters.q));

  return (
    <div className="space-y-6">
      <SignalCalendar days={filters.days} data={calendar} />
      <SignalBoard signals={rows} />
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-[86px] rounded-lg" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-[186px] rounded-xl" />
        ))}
      </div>
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-[84px] rounded-xl" />
        ))}
      </div>
    </div>
  );
}

export default async function Page({ searchParams }: PageProps<"/">) {
  const filters = normalizeFilters(await loadSignalFilters(searchParams));

  return (
    <PageShell
      title="Signals"
      subtitle="Open-market insider purchases from SEC Form 4, scored and ranked. Highest conviction first."
      icon={LayoutGrid}
    >
      <div className="space-y-6">
        <SignalFilters />
        {/* Re-keyed on the filters so a filter change swaps in the skeleton
            instead of holding the previous result while the query runs. */}
        <Suspense key={JSON.stringify(filters)} fallback={<ResultsSkeleton />}>
          <Results filters={filters} />
        </Suspense>
      </div>
    </PageShell>
  );
}
