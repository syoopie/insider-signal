import { Suspense } from "react";
import { Activity } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { PipelineDiagram } from "@/components/pipeline-diagram";
import { ScoringExplainer } from "@/components/scoring-explainer";
import { StatCard } from "@/components/stat-card";
import { Skeleton } from "@/components/ui/skeleton";
import { getPipelineStatus } from "@/lib/queries/pipeline";
import { DISQUALIFIERS, LIMITATIONS, RESEARCH, THRESHOLDS } from "@/lib/scoring-model";
import { fmtDate, fmtInt } from "@/lib/format";

export const revalidate = 3600;
export const metadata = { title: "How it works" };

function Section({
  title,
  lead,
  children,
}: {
  title: string;
  lead?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {lead && <p className="mt-1 text-sm text-muted-foreground text-pretty">{lead}</p>}
      </div>
      {children}
    </section>
  );
}

async function LiveCoverage() {
  const s = await getPipelineStatus();
  if (s.counts.filings === 0) return null;

  const capCoverage =
    s.counts.companies > 0 ? Math.round((s.counts.companiesWithCap / s.counts.companies) * 100) : 0;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard
        label="Filings ingested"
        value={fmtInt(s.counts.filings)}
        hint={`since ${fmtDate(s.coverageStart, { withYear: true })}`}
      />
      <StatCard
        label="Purchases scored"
        value={fmtInt(s.counts.purchaseTransactions)}
        hint={`of ${fmtInt(s.counts.transactions)} transactions on file`}
      />
      <StatCard
        label="Signals produced"
        value={fmtInt(s.counts.signals)}
        hint={`${fmtInt(s.counts.buySignals)} BUY · ${fmtInt(s.counts.clusterBuySignals)} CLUSTER_BUY`}
      />
      <StatCard
        label="Companies tracked"
        value={fmtInt(s.counts.companies)}
        hint={`${capCoverage}% have a resolved market cap`}
      />
    </div>
  );
}

export default function Page() {
  return (
    <PageShell
      title="How it works"
      subtitle="Every number on this site comes from the tables below, produced by the code below. Nothing is hand-curated."
      icon={Activity}
    >
      <div className="space-y-10">
        <Suspense
          fallback={
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-[104px] rounded-xl" />
              ))}
            </div>
          }
        >
          <LiveCoverage />
        </Suspense>

        <Section
          title="The pipeline"
          lead="A GitHub Actions job runs on weekdays at 11:00 UTC. It fetches the previous day's Form 4 filings, parses them, scores every open-market purchase, and writes any signals it finds."
        >
          <PipelineDiagram />
        </Section>

        <Section
          title="What gets thrown away first"
          lead="Four checks run before anything is scored. Each one returns a zero and stops — no partial credit."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            {DISQUALIFIERS.map((d) => (
              <div key={d.title} className="rounded-lg border p-4">
                <p className="text-sm font-medium">{d.title}</p>
                <p className="mt-1 text-sm text-muted-foreground text-pretty">{d.detail}</p>
                {"research" in d && d.research && (
                  <p className="mt-2 border-t pt-2 text-xs text-muted-foreground text-pretty">
                    {d.research}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="Score a purchase yourself"
          lead="The same weights the pipeline uses, wired to controls. Try to reach the BUY threshold — it takes three or four strong factors, and there is no route to it through sheer dollar value."
        >
          <ScoringExplainer />
        </Section>

        <Section
          title="How a score becomes a signal"
          lead="Thresholds are fixed and identical in the daily ingest and the backfill."
        >
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Type</th>
                  <th className="px-3 py-2 text-left font-medium">Rule</th>
                  <th className="px-3 py-2 text-left font-medium">Alerted</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                <tr>
                  <td className="px-3 py-2 font-medium">CLUSTER_BUY</td>
                  <td className="px-3 py-2 text-muted-foreground text-pretty">
                    {THRESHOLDS.clusterMinInsiders}+ distinct insiders inside{" "}
                    {THRESHOLDS.clusterWindowDays} days, their average score at least{" "}
                    {THRESHOLDS.clusterAvg}, and either the window is tight (
                    {THRESHOLDS.tightWindowDays} days) or one of them scored{" "}
                    {THRESHOLDS.clusterMaxScore}+. Large-caps are downgraded to WATCH regardless.
                  </td>
                  <td className="px-3 py-2">Yes</td>
                </tr>
                <tr>
                  <td className="px-3 py-2 font-medium">BUY</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    A single purchase scoring {THRESHOLDS.buy} or more.
                  </td>
                  <td className="px-3 py-2">Yes</td>
                </tr>
                <tr>
                  <td className="px-3 py-2 font-medium">WATCH</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    Score {THRESHOLDS.watch}–{THRESHOLDS.buy - 1}, or a cluster that missed its bar.
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">No</td>
                </tr>
                <tr>
                  <td className="px-3 py-2 font-medium">LOW</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    Under {THRESHOLDS.watch}. Stored, never surfaced.
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">No</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Section>

        <Section
          title="What counts as a cluster"
          lead="Three insiders buying the same stock in the same fortnight is the strongest pattern in the research — but only when they decided independently."
        >
          <ul className="space-y-2 text-sm">
            {[
              `Direct purchases only. Buying through an LLC, trust or family entity does not count toward the ${THRESHOLDS.clusterMinInsiders}.`,
              `At least $${(THRESHOLDS.clusterMinValue / 1000).toFixed(0)},000 each, which filters out automated payroll and dividend reinvestment.`,
              "If three or more buyers took the exact same share count at the same price on the same day, the whole block is discarded — that is an allocation, not three decisions.",
              "The same applies to three or more buyers at one price on one day with different share counts: a secondary offering.",
            ].map((rule) => (
              <li key={rule} className="flex gap-2 rounded-lg border p-3 text-pretty">
                <span aria-hidden className="text-muted-foreground">
                  •
                </span>
                <span>{rule}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="How the backtest measures itself"
          lead="Weekly, over the trailing 730 days, at four hold horizons."
        >
          <ul className="space-y-2 text-sm">
            {[
              "A signal is dated the day after its filing reached EDGAR — never the transaction date, which nobody could have known about at the time.",
              "Entry is modelled four days after the filing, approximating a realistic fill.",
              "Return is measured against SPY over the identical window; small-caps are also measured against IWM.",
              "A delisted ticker is scored as a 50% loss rather than dropped, so failures stay in the record.",
            ].map((line) => (
              <li key={line} className="flex gap-2 rounded-lg border p-3 text-pretty">
                <span aria-hidden className="text-muted-foreground">
                  •
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="The research this is built on">
          <div className="space-y-3">
            {RESEARCH.map((r) => (
              <div key={r.cite} className="rounded-lg border p-4">
                <p className="text-sm font-medium">{r.cite}</p>
                <p className="mt-1 text-sm text-muted-foreground text-pretty">{r.finding}</p>
                <p className="mt-2 border-t pt-2 text-xs text-pretty">
                  <span className="font-medium">Used for: </span>
                  <span className="text-muted-foreground">{r.used}</span>
                </p>
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="What this does not do"
          lead="The honest list. Read it before treating any of this as a recommendation."
        >
          <ul className="space-y-2">
            {LIMITATIONS.map((l) => (
              <li key={l} className="rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm text-pretty">
                {l}
              </li>
            ))}
          </ul>
          <p className="rounded-lg border p-4 text-sm text-muted-foreground text-pretty">
            This is a research tool, not investment advice. It surfaces public disclosures and
            scores them against published findings. It has no view on whether you should own
            anything.
          </p>
        </Section>
      </div>
    </PageShell>
  );
}
