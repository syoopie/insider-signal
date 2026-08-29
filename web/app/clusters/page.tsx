import { Suspense } from "react";
import { Network } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ClusterList } from "@/components/cluster-list";
import { Skeleton } from "@/components/ui/skeleton";
import { getClusters } from "@/lib/queries/clusters";

export const revalidate = 3600;
export const metadata = { title: "Clusters" };

const WINDOW_DAYS = 60;

async function Content() {
  const clusters = await getClusters(WINDOW_DAYS);

  const tight = clusters.filter((c) => c.tight).length;
  const exec = clusters.filter((c) => c.executive).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <Stat label="Clusters" value={clusters.length} />
        <Stat label="Tight window" value={tight} hint="3+ buyers inside 5 days" />
        <Stat label="With an executive" value={exec} hint="CFO, CEO, COO or Chairman among the buyers" />
      </div>

      <p className="text-sm text-muted-foreground text-pretty">
        Three or more insiders buying the same stock inside 14 days has historically carried about
        twice the alpha of a single insider buy. The pipeline only counts direct open-market
        purchases of at least $25,000, and discards blocks where three or more buyers took the same
        price on the same day — those are offering allocations, not independent decisions.
      </p>

      <ClusterList clusters={clusters} />
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="flex items-baseline gap-1.5" title={hint}>
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}

export default function Page() {
  return (
    <PageShell
      title="Clusters"
      subtitle={`Rolling 14-day windows where three or more insiders bought the same stock, over the last ${WINDOW_DAYS} days.`}
      icon={Network}
    >
      <Suspense
        fallback={
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[260px] rounded-xl" />
            ))}
          </div>
        }
      >
        <Content />
      </Suspense>
    </PageShell>
  );
}
