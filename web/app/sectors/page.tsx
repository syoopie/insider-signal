import { PieChart } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ComingSoon } from "@/components/coming-soon";

export const metadata = { title: "Sectors" };

export default function Page() {
  return (
    <PageShell
      title="Sectors"
      subtitle="Which industries insiders are buying into, by SIC code."
      icon={PieChart}
    >
      <ComingSoon note="Phase 6. Needs a small ingest addition to populate companies.sic_code from EDGAR's submissions data." />
    </PageShell>
  );
}
