import { Suspense } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink, Search } from "lucide-react";
import { PageShell } from "@/components/page-shell";
import { CapTierBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { LivePrice } from "@/components/live-price";
import { TickerSearch } from "@/components/ticker-search";
import { TickerViews } from "@/components/ticker-views";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getAllTickers,
  getTickerCompany,
  getTickerSignals,
  getTickerTransactions,
} from "@/lib/queries/ticker";
import { fmtCurrency, fmtDate } from "@/lib/format";

export const revalidate = 3600;

export async function generateMetadata({ params }: PageProps<"/ticker/[symbol]">) {
  const { symbol } = await params;
  return { title: symbol.toUpperCase() };
}

async function Content({ symbol }: { symbol: string }) {
  const [company, transactions, signals, allTickers] = await Promise.all([
    getTickerCompany(symbol),
    getTickerTransactions(symbol),
    getTickerSignals(symbol),
    getAllTickers(),
  ]);

  if (!company && transactions.length === 0 && signals.length === 0) {
    return (
      <div className="space-y-6">
        <EmptyState
          icon={Search}
          title={`Nothing stored for ${symbol}`}
          description="This ticker is not in the tracked universe, or no Form 4 has been filed for it inside the ingested window. Try another."
        />
        <TickerSearch options={allTickers} className="mx-auto max-w-xl" />
      </div>
    );
  }

  // The average price the insiders actually paid, share-weighted, over the
  // open-market buys on record. It is the only reference price on this page
  // that means anything: it is what conviction cost them.
  const buys = transactions.filter(
    (t) => t.transactionCode === "P" && t.pricePerShare != null && (t.shares ?? 0) > 0,
  );
  const totalShares = buys.reduce((s, t) => s + (t.shares ?? 0), 0);
  const avgEntry =
    totalShares > 0
      ? buys.reduce((s, t) => s + t.pricePerShare! * (t.shares ?? 0), 0) / totalShares
      : null;

  const lastFiled = transactions.find((t) => t.filedDate)?.filedDate ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4 rounded-xl border bg-card px-5 py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="font-mono text-2xl font-semibold tracking-tight">{company?.ticker ?? symbol}</h2>
            <span className="text-muted-foreground">{company?.name ?? "Unknown company"}</span>
            {company && <CapTierBadge tier={company.capTier} />}
          </div>
          <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            {company?.marketCap != null && (
              <div className="flex gap-1.5">
                <dt>Market cap</dt>
                <dd className="tabular-nums text-foreground">{fmtCurrency(company.marketCap)}</dd>
              </div>
            )}
            {avgEntry != null && (
              <div className="flex gap-1.5">
                <dt>Avg insider entry</dt>
                <dd className="tabular-nums text-foreground">${avgEntry.toFixed(2)}</dd>
              </div>
            )}
            {lastFiled && (
              <div className="flex gap-1.5">
                <dt>Last filing</dt>
                <dd className="tabular-nums text-foreground">{fmtDate(lastFiled, { withYear: true })}</dd>
              </div>
            )}
          </dl>
        </div>

        <div className="flex flex-col items-end gap-2">
          <LivePrice ticker={company?.ticker ?? symbol} compareTo={avgEntry} />
          <a
            href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(
              company?.ciks[0] ?? symbol,
            )}&type=4&dateb=&owner=include&count=40`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Form 4 filings on SEC EDGAR
            <ExternalLink className="size-3" aria-hidden />
          </a>
        </div>
      </div>

      <TickerViews
        ticker={company?.ticker ?? symbol}
        transactions={transactions}
        signals={signals}
      />
    </div>
  );
}

export default async function Page({ params }: PageProps<"/ticker/[symbol]">) {
  const { symbol } = await params;
  const ticker = decodeURIComponent(symbol).toUpperCase();

  return (
    <PageShell
      title={ticker}
      subtitle="Full insider transaction and signal history."
      icon={Search}
      actions={
        <Link
          href="/ticker"
          className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm hover:bg-muted"
        >
          <ArrowLeft className="size-3.5" aria-hidden />
          All tickers
        </Link>
      }
    >
      <Suspense fallback={<Skeleton className="h-[600px] rounded-xl" />}>
        <Content symbol={ticker} />
      </Suspense>
    </PageShell>
  );
}
