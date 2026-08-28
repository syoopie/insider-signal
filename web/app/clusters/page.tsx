import { Network } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ComingSoon } from "@/components/coming-soon";

export const metadata = { title: "Clusters" };

export default function Page() {
  return (
    <PageShell
      title="Clusters"
      subtitle="Active 14-day windows where 3 or more insiders bought the same stock."
      icon={Network}
    >
      <ComingSoon note="Phase 6 adds a timeline view per cluster showing the window filling buyer by buyer." />
    </PageShell>
  );
}
