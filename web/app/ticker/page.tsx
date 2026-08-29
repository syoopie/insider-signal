import { Suspense } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { TickerSearch } from "@/components/ticker-search";
import { Skeleton } from "@/components/ui/skeleton";
import { getAllTickers } from "@/lib/queries/ticker";

export const revalidate = 3600;

export const metadata = { title: "Research" };

async function SearchPanel() {
  const tickers = await getAllTickers();
  const withSignals = tickers.filter((t) => t.signals > 0);
  const busiest = [...withSignals].sort((a, b) => b.signals - a.signals).slice(0, 12);

  return (
    <div className="space-y-8">
      <TickerSearch options={tickers} autoFocus className="max-w-xl" />

      <p className="text-sm text-muted-foreground">
        {tickers.length.toLocaleString("en-US")} companies tracked ·{" "}
        {withSignals.length.toLocaleString("en-US")} have produced at least one signal.
      </p>

      {busiest.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-medium">Most signalled</h2>
          <div className="flex flex-wrap gap-2">
            {busiest.map((t) => (
              <Link
                key={t.ticker}
                href={`/ticker/${encodeURIComponent(t.ticker)}`}
                className="group inline-flex items-baseline gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors hover:border-primary/40 hover:bg-muted"
              >
                <span className="font-mono font-semibold">{t.ticker}</span>
                <span className="max-w-40 truncate text-xs text-muted-foreground">{t.name}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{t.signals}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <PageShell
      title="Research"
      subtitle="Every Form 4 transaction and every signal the pipeline has recorded for one company."
      icon={Search}
    >
      <Suspense fallback={<Skeleton className="h-11 max-w-xl rounded-lg" />}>
        <SearchPanel />
      </Suspense>
    </PageShell>
  );
}
