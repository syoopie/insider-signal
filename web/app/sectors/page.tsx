import { Suspense } from "react";
import { PieChart } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { SectorViews } from "@/components/sector-views";
import { Skeleton } from "@/components/ui/skeleton";
import { getSectors } from "@/lib/queries/sectors";

export const revalidate = 3600;
export const metadata = { title: "Sectors" };

const WINDOW_DAYS = 90;

async function Content() {
  const data = await getSectors(WINDOW_DAYS);
  return <SectorViews data={data} />;
}

export default function Page() {
  return (
    <PageShell
      title="Sectors"
      subtitle={`Which industries insiders have been buying into over the last ${WINDOW_DAYS} days.`}
      icon={PieChart}
    >
      <Suspense fallback={<Skeleton className="h-[420px] rounded-xl" />}>
        <Content />
      </Suspense>
    </PageShell>
  );
}
