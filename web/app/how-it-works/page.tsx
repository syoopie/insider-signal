import { Activity } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { ComingSoon } from "@/components/coming-soon";

export const metadata = { title: "How it works" };

export default function Page() {
  return (
    <PageShell
      title="How it works"
      subtitle="The full pipeline, the scoring model, the backtest methodology, and the research it rests on."
      icon={Activity}
      maxWidth="prose"
    >
      <ComingSoon note="Phase 7: pipeline diagram, an interactive scoring-model explainer with citations, and the methodology write-up from the old About tab." />
    </PageShell>
  );
}
