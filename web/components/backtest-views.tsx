"use client";

import { DataTable, type Column } from "@/components/data-table";
import { Boxplot, CategoryBarChart, ChartCard, TimeSeriesChart } from "@/components/charts";
import { Return } from "@/components/money";
import { SampleSize, SampleSizeLegend } from "@/components/sample-size";
import { EmptyState } from "@/components/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtDate, fmtPct, titleCase } from "@/lib/format";
import type { Backtest, ClusterRow, RiskRow, StratRow } from "@/lib/queries/backtest";
import { ChartLine } from "lucide-react";

const pctAxis = (v: number) => `${v}%`;

/**
 * Stratification groups are a mix of words ("small", "CLUSTER_BUY") and score
 * ranges ("65-74", "85+"). titleCase() turns underscores and hyphens into
 * spaces, which is right for the first kind and mangles the second into
 * "65 74" — so ranges pass through with the hyphen promoted to an en dash.
 */
const groupLabel = (group: string) =>
  /\d/.test(group) && !/[a-z]/i.test(group) ? group.replace(/-/g, "\u2013") : titleCase(group);

/** "2026-08-01" -> "Aug 2026". The axis carries months, not days. */
const monthLabel = (iso: string) =>
  fmtDate(iso, { withYear: true }).replace(/\s\d+,/, "");

/**
 * Every backtest view. One client component because the chart wrappers take
 * function props (`yFormat`), which cannot cross the server→client boundary.
 * The server page hands it plain aggregated data.
 */
export function BacktestViews({ data }: { data: Backtest }) {
  const series = data.horizonKeys.map((k) => ({ key: k, label: k }));
  const monthly = data.monthlyExcess.map((p) => ({ ...p, x: monthLabel(p.x) }));

  return (
    <div className="space-y-6">
      <HitRateCards data={data} />

      {data.monthlyExcess.length > 0 ? (
        <ChartCard
          title="Excess return vs SPY, by the month the signal fired"
          qualifier={`${data.detailCount.toLocaleString("en-US")} evaluated signals, monthly mean`}
        >
          <TimeSeriesChart
            data={monthly}
            series={series}
            yFormat={pctAxis}
            referenceY={0}
            height={280}
          />
          <p className="mt-3 text-xs text-muted-foreground text-pretty">
            Each point averages the individual signals that fired that month, held for the given
            horizon. Above the zero line the signals beat SPY over the holding period; below it they
            lagged. Recent months are thinner — a 180-day hold needs 183 days of history before it
            can be scored at all.
          </p>
        </ChartCard>
      ) : (
        <ChartCard title="Excess return vs SPY by hold horizon" qualifier="latest run">
          <CategoryBarChart
            data={data.horizons.map((h) => ({ x: `${h.horizonDays}d`, avg: h.avgReturn ?? 0 }))}
            series={[{ key: "avg", label: "Avg excess return" }]}
            yFormat={pctAxis}
            referenceY={0}
          />
          <p className="mt-3 text-xs text-muted-foreground">
            Per-signal detail is not in this run, so this falls back to the horizon averages.
          </p>
        </ChartCard>
      )}

      <Tabs defaultValue="distribution">
        <TabsList variant="line" className="flex-wrap">
          <TabsTrigger value="distribution">Distribution</TabsTrigger>
          <TabsTrigger value="score">Score band</TabsTrigger>
          <TabsTrigger value="cap">Cap tier</TabsTrigger>
          <TabsTrigger value="type">Signal type</TabsTrigger>
          <TabsTrigger value="risk">Risk</TabsTrigger>
          <TabsTrigger value="cluster">Cluster 50–64</TabsTrigger>
        </TabsList>

        <TabsContent value="distribution" className="pt-4">
          {data.distribution.length > 0 ? (
            <div className="rounded-xl border p-5">
              <p className="mb-4 text-sm text-muted-foreground text-pretty">
                Box spans the 25th to 75th percentile, the line is the median, whiskers reach the
                worst and best single outcome. The average alone hides the tail — and the tail is
                where a strategy actually fails.
              </p>
              <Boxplot rows={data.distribution} />
            </div>
          ) : (
            <NoData what="distribution" />
          )}
        </TabsContent>

        <TabsContent value="score" className="pt-4">
          <StratView rows={data.byScoreBand} dimension="Score band" />
        </TabsContent>
        <TabsContent value="cap" className="pt-4">
          <StratView rows={data.byCapTier} dimension="Cap tier" />
        </TabsContent>
        <TabsContent value="type" className="pt-4">
          <StratView rows={data.bySignalType} dimension="Signal type" />
        </TabsContent>

        <TabsContent value="risk" className="pt-4">
          {data.risk.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground text-pretty">
                A high share of losses over 20%, or a long losing streak, means the strategy is
                survivable on paper but hard to hold through.
              </p>
              <DataTable
                rows={data.risk}
                columns={riskColumns}
                getRowKey={(r) => r.horizon}
                dense
              />
            </div>
          ) : (
            <NoData what="risk" />
          )}
        </TabsContent>

        <TabsContent value="cluster" className="pt-4">
          {data.cluster5064.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground text-pretty">
                Cluster signals scoring 50–64 sit below the BUY threshold but still carry cluster
                conviction. This bucket has historically been one of the strongest in the system,
                which is why it is tracked separately.
              </p>
              <DataTable
                rows={data.cluster5064}
                columns={clusterColumns}
                getRowKey={(r) => r.horizon}
                dense
              />
              <SampleSizeLegend />
            </div>
          ) : (
            <NoData what="cluster 50–64" />
          )}
        </TabsContent>
      </Tabs>

      {data.rollingHitRate.length > 0 && (
        <ChartCard title="Rolling 90-day hit rate" qualifier="sampled every 14 days">
          <TimeSeriesChart
            data={data.rollingHitRate}
            series={series}
            yFormat={pctAxis}
            xFormat={(x) => fmtDate(x, { withYear: true })}
            referenceY={50}
            height={260}
          />
          <p className="mt-3 text-xs text-muted-foreground text-pretty">
            The dashed line is 50% — a coin flip. Flat or rising means the edge is holding; a
            sustained decline means the model is decaying or the regime has changed.
          </p>
        </ChartCard>
      )}
    </div>
  );
}

function HitRateCards({ data }: { data: Backtest }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {data.horizons.map((h) => {
        const vs50 = h.hitRate == null ? null : h.hitRate - 50;
        return (
          <div key={h.horizonDays} className="rounded-xl border bg-card p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {h.horizonDays}-day hold
            </p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{fmtPct(h.hitRate)}</p>
            <p className="text-xs text-muted-foreground">
              hit rate ·{" "}
              {vs50 == null ? (
                "—"
              ) : (
                <>
                  <Return value={vs50} digits={1} className="text-xs" /> vs coin flip
                </>
              )}
            </p>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 border-t pt-2 text-xs">
              <dt className="text-muted-foreground">Avg excess</dt>
              <dd className="text-right">
                <Return value={h.avgReturn} />
              </dd>
              <dt className="text-muted-foreground">Median</dt>
              <dd className="text-right">
                <Return value={h.medianReturn} />
              </dd>
              <dt className="text-muted-foreground">Signals</dt>
              <dd className="text-right">
                <SampleSize n={h.nTrades} />
              </dd>
            </dl>
          </div>
        );
      })}
    </div>
  );
}

function StratView({ rows, dimension }: { rows: StratRow[]; dimension: string }) {
  if (rows.length === 0) return <NoData what={dimension.toLowerCase()} />;
  return (
    <div className="space-y-2">
      <DataTable
        rows={rows}
        columns={stratColumns(dimension)}
        getRowKey={(r) => `${r.group}-${r.horizon}`}
        dense
      />
      <SampleSizeLegend />
    </div>
  );
}

function NoData({ what }: { what: string }) {
  return (
    <EmptyState
      icon={ChartLine}
      title={`No ${what} data in this run`}
      description="The backtest writes this section once it has enough completed exits. It runs weekly, Sundays at 12:00 UTC."
    />
  );
}

const stratColumns = (dimension: string): Column<StratRow>[] => [
  {
    key: "group",
    header: dimension,
    width: "minmax(0, 1.4fr)",
    cell: (r) => <span className="font-medium">{groupLabel(r.group)}</span>,
    sortValue: (r) => r.group,
  },
  {
    key: "horizon",
    header: "Horizon",
    width: "88px",
    cell: (r) => r.horizon,
    sortValue: (r) => parseInt(r.horizon, 10),
  },
  {
    key: "n",
    header: "Signals",
    width: "80px",
    align: "end",
    cell: (r) => <SampleSize n={r.n} />,
    sortValue: (r) => r.n,
  },
  {
    key: "hit",
    header: "Hit rate",
    width: "88px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{fmtPct(r.hitRate)}</span>,
    sortValue: (r) => r.hitRate ?? -1,
  },
  {
    key: "avg",
    header: "Avg excess",
    width: "96px",
    align: "end",
    cell: (r) => <Return value={r.avgReturn} />,
    sortValue: (r) => r.avgReturn ?? 0,
  },
  {
    key: "median",
    header: "Median",
    width: "96px",
    align: "end",
    cell: (r) => <Return value={r.medianReturn} />,
    sortValue: (r) => r.medianReturn ?? 0,
  },
];

const riskColumns: Column<RiskRow>[] = [
  { key: "horizon", header: "Horizon", width: "96px", cell: (r) => r.horizon },
  {
    key: "loss20",
    header: "Losses over 20%",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <span className="tabular-nums">{fmtPct(r.pctLossGt20, 1)}</span>,
    sortValue: (r) => r.pctLossGt20 ?? 0,
  },
  {
    key: "streak",
    header: "Longest losing streak",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <span className="tabular-nums">{r.maxConsecutiveLosses ?? "—"}</span>,
    sortValue: (r) => r.maxConsecutiveLosses ?? 0,
  },
  {
    key: "worst",
    header: "Worst single outcome",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <Return value={r.worstOutcome} />,
    sortValue: (r) => r.worstOutcome ?? 0,
  },
  {
    key: "nospy",
    header: "Missing SPY data",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <span className="tabular-nums text-muted-foreground">{r.nNoSpyData ?? 0}</span>,
    sortValue: (r) => r.nNoSpyData ?? 0,
  },
];

const clusterColumns: Column<ClusterRow>[] = [
  { key: "horizon", header: "Horizon", width: "96px", cell: (r) => r.horizon },
  {
    key: "n",
    header: "Signals",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <SampleSize n={r.n} />,
    sortValue: (r) => r.n,
  },
  {
    key: "hit",
    header: "Hit rate",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <span className="tabular-nums">{fmtPct(r.hitRate)}</span>,
    sortValue: (r) => r.hitRate ?? -1,
  },
  {
    key: "avg",
    header: "Avg excess",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <Return value={r.avgReturn} />,
    sortValue: (r) => r.avgReturn ?? 0,
  },
  {
    key: "median",
    header: "Median",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <Return value={r.medianReturn} />,
    sortValue: (r) => r.medianReturn ?? 0,
  },
];
