"use client";

import Link from "next/link";
import { CategoryBarChart, ChartCard } from "@/components/charts";
import { DataTable, type Column } from "@/components/data-table";
import { EmptyState } from "@/components/empty-state";
import { PieChart } from "lucide-react";
import type { SectorRow, SectorSummary } from "@/lib/queries/sectors";
import { UNKNOWN_SECTOR } from "@/lib/sic";

export function SectorViews({ data }: { data: SectorSummary }) {
  if (data.totalSignals === 0) {
    return (
      <EmptyState
        icon={PieChart}
        title="No signals in this window"
        description="Nothing to break down by sector yet."
      />
    );
  }

  const classified = data.rows.filter((r) => r.sector !== UNKNOWN_SECTOR);
  const unclassified = data.rows.find((r) => r.sector === UNKNOWN_SECTOR);
  const coverage =
    data.totalCompanies > 0
      ? Math.round((data.classifiedCompanies / data.totalCompanies) * 100)
      : 0;

  return (
    <div className="space-y-6">
      {coverage < 100 && (
        <div className="rounded-lg border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-pretty">
          <p className="font-medium">
            {coverage}% of tracked companies have an industry code.
          </p>
          <p className="mt-1 text-muted-foreground">
            Form 4 filings carry no industry classification, so it is fetched separately from
            EDGAR&apos;s submissions API by <code>scripts/backfill_sic.py</code>. Until that has run
            over the whole company table, the rest fall into {UNKNOWN_SECTOR}
            {unclassified ? ` (${unclassified.signals} signals here)` : ""}.
          </p>
        </div>
      )}

      {classified.length > 0 && (
        <ChartCard title="Signals by sector" qualifier={`${data.totalSignals} signals in the window`}>
          <CategoryBarChart
            data={classified.map((r) => ({
              x: shortSector(r.sector),
              clusterBuy: r.clusterBuys,
              buy: r.buys,
              watch: r.signals - r.clusterBuys - r.buys,
            }))}
            series={[
              { key: "clusterBuy", label: "Cluster buy" },
              { key: "buy", label: "Buy" },
              { key: "watch", label: "Watch" },
            ]}
            stacked
            height={280}
          />
        </ChartCard>
      )}

      <DataTable
        rows={data.rows}
        columns={columns}
        getRowKey={(r) => r.sector}
        initialSort={{ key: "signals", dir: "desc" }}
      />
    </div>
  );
}

/** The bar axis has room for roughly a dozen characters per label. */
function shortSector(sector: string): string {
  return sector
    .replace(" & Real Estate", "")
    .replace(" & Utilities", "")
    .replace(" & Forestry", "")
    .replace(" & Energy", "")
    .replace(" Trade", "");
}

const columns: Column<SectorRow>[] = [
  {
    key: "sector",
    header: "Sector",
    width: "minmax(0, 1.4fr)",
    cell: (r) => <span className="font-medium">{r.sector}</span>,
    sortValue: (r) => r.sector,
  },
  {
    key: "signals",
    header: "Signals",
    width: "88px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{r.signals}</span>,
    sortValue: (r) => r.signals,
  },
  {
    key: "clusters",
    header: "Clusters",
    width: "88px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{r.clusterBuys}</span>,
    sortValue: (r) => r.clusterBuys,
  },
  {
    key: "companies",
    header: "Companies",
    width: "96px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{r.companies}</span>,
    sortValue: (r) => r.companies,
  },
  {
    key: "avg",
    header: "Avg score",
    width: "96px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{r.avgScore}</span>,
    sortValue: (r) => r.avgScore,
  },
  {
    key: "top",
    header: "Most signalled",
    width: "minmax(0, 1.6fr)",
    cell: (r) => (
      <span className="flex flex-wrap gap-x-2 truncate text-xs">
        {r.topTickers.map((t) => (
          <Link
            key={t.ticker}
            href={`/ticker/${encodeURIComponent(t.ticker)}`}
            className="font-mono underline-offset-2 hover:underline"
            title={`${t.signals} signal${t.signals === 1 ? "" : "s"}`}
          >
            {t.ticker}
          </Link>
        ))}
      </span>
    ),
  },
];
