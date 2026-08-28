import { Search } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ComingSoon } from "@/components/coming-soon";

export const metadata = { title: "Research" };

export default function Page() {
  return (
    <PageShell
      title="Research"
      subtitle="Full insider transaction history, purchase timeline, and signal history for one ticker."
      icon={Search}
    >
      <ComingSoon note="Phase 5 adds ticker search and the per-ticker page at /ticker/[symbol], with on-demand current price." />
    </PageShell>
  );
}
