import { LineChart } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ComingSoon } from "@/components/coming-soon";

export const metadata = { title: "Backtest" };

export default function Page() {
  return (
    <PageShell
      title="Backtest"
      subtitle="Hit rate, excess return vs SPY/IWM, distribution, stratification, and alpha decay over a 730-day window."
      icon={LineChart}
    >
      <ComingSoon note="Phase 4 ports every backtest chart and table from the Streamlit app to Recharts." />
    </PageShell>
  );
}
