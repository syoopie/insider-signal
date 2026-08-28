"use client";

import { notFound } from "next/navigation";
import { Boxplot, CategoryBarChart, ChartCard, TimeSeriesChart } from "@/components/charts";
import { CapTierBadge, ConvictionBadge, SignalTypeBadge } from "@/components/badges";
import { ClusterWindow } from "@/components/cluster-window";
import { EmptyState } from "@/components/empty-state";
import { InsiderTable } from "@/components/insider-table";
import { Money, Return } from "@/components/money";
import { ScoreBar } from "@/components/score-bar";
import { StatCard } from "@/components/stat-card";
import { PageShell } from "@/components/page-shell";
import { DatabaseZap } from "lucide-react";
import type { Cluster, Insider } from "@/lib/types";

const MOCK_INSIDERS: Insider[] = [
  { name: "Sakellaris George P", role: "CEO", role_raw: "Chief Executive Officer", price: 21.02, total_value: 124810, pct_increase: 0.6, shares_after: 1001535, shares_bought: 5938, transaction_date: "2026-08-25" },
  { name: "Sutton Joseph W.", role: "DIRECTOR", role_raw: "Director", price: 20.87, total_value: 202439, pct_increase: 13.7, shares_bought: 9700, transaction_date: "2026-08-24" },
  { name: "Cox Brian C", role: "DIRECTOR", role_raw: "Director", price: 21.64, total_value: 100085, pct_increase: 11.6, shares_bought: 4625, transaction_date: "2026-08-21" },
  { name: "Miller Jennifer L", role: "DIRECTOR", role_raw: "Director", price: 20.96, total_value: 41920, pct_increase: 6.1, shares_bought: 2000, transaction_date: "2026-08-24" },
];

const MOCK_CLUSTER: Cluster = {
  is_cluster: true,
  insider_count: 4,
  tight_cluster: true,
  executive_cluster: true,
  window_start: "2026-08-13",
  window_end: "2026-08-27",
  insiders: [
    { insider_name: "Cox Brian C", role_category: "director", transaction_date: "2026-08-21", total_value: 100085, price_per_share: 21.64, shares: 4625, is_direct: true },
    { insider_name: "Sutton Joseph W.", role_category: "director", transaction_date: "2026-08-24", total_value: 202439, price_per_share: 20.87, shares: 9700, is_direct: true },
    { insider_name: "Miller Jennifer L", role_category: "director", transaction_date: "2026-08-24", total_value: 41920, price_per_share: 20.96, shares: 2000, is_direct: true },
    { insider_name: "Sakellaris George P", role_category: "ceo", transaction_date: "2026-08-25", total_value: 89485, price_per_share: 22.03, shares: 4062, is_direct: true },
  ],
};

const MOCK_BREAKDOWN = {
  role_director: 16,
  cap_small: 15,
  holdings_increase_5pct: 15,
  sequenced_buying_30d: 10,
  near_52wk_low_10pct: 7,
  role_ceo: -5,
  indirect_purchase: -15,
};

const ROLLING = Array.from({ length: 12 }, (_, i) => ({
  x: `M${i + 1}`,
  "30d": 48 + Math.round(Math.sin(i / 2) * 8) + i,
  "90d": 52 + Math.round(Math.cos(i / 3) * 6),
}));

const HIT_BY_HORIZON = [
  { x: "30d", hit: 52 },
  { x: "60d", hit: 55 },
  { x: "90d", hit: 55 },
  { x: "180d", hit: 61 },
];

const DIST = [
  { label: "30d", p25: -6.4, median: 0.7, p75: 6.8, min: -58, max: 69 },
  { label: "90d", p25: -8.1, median: 2.4, p75: 11.2, min: -63, max: 88 },
  { label: "180d", p25: -3.1, median: 8.9, p75: 24.5, min: -63, max: 120 },
];

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="font-heading text-sm font-semibold text-muted-foreground">{title}</h2>
      <div className="flex flex-wrap items-start gap-3">{children}</div>
    </section>
  );
}

export default function PreviewPage() {
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <PageShell title="Component preview" subtitle="Dev-only. Every shared component in its states." icon={DatabaseZap}>
      <div className="space-y-10">
        <Row title="Signal type badges">
          <SignalTypeBadge type="CLUSTER_BUY" />
          <SignalTypeBadge type="BUY" />
          <SignalTypeBadge type="WATCH" />
          <SignalTypeBadge type="LOW" />
          <SignalTypeBadge type="BUY" showIcon={false} />
        </Row>

        <Row title="Conviction badges">
          <ConvictionBadge conviction="PRIME" />
          <ConvictionBadge conviction="STRONG" />
          <ConvictionBadge conviction="CLUSTER" />
          <ConvictionBadge conviction="HIGH" />
          <ConvictionBadge conviction="BUY" />
        </Row>

        <Row title="Cap tier badges">
          <CapTierBadge tier="small" />
          <CapTierBadge tier="mid" />
          <CapTierBadge tier="large" />
          <CapTierBadge tier="unknown" />
        </Row>

        <Row title="Money & returns">
          <div className="space-y-1 text-sm">
            <div>
              <Money value={202439} /> · <Money value={41920} /> · <Money value={-15000} />
            </div>
            <div>
              <Money value={1_240_000} emphasizeAbove={250_000} /> (emphasised)
            </div>
            <div>
              <Return value={13.7} /> · <Return value={-8.2} /> · <Return value={0} />
            </div>
          </div>
        </Row>

        <Row title="Stat cards">
          <div className="grid w-full grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Signals" value="2,731" hint="142 BUY · 229 CLUSTER_BUY" />
            <StatCard label="Hit rate 90d" value="55%" hint="n=289" />
            <StatCard label="Top sector" value="Financials" variant="text" hint="18% of recent buys" />
            <StatCard label="Latest signal" value="Aug 26, 2026" variant="text" />
          </div>
        </Row>

        <Row title="Score breakdown">
          <div className="w-full max-w-md rounded-lg border p-4">
            <ScoreBar breakdown={MOCK_BREAKDOWN} score={53} />
          </div>
        </Row>

        <Row title="Insider table">
          <div className="w-full">
            <InsiderTable insiders={MOCK_INSIDERS} />
          </div>
        </Row>

        <Row title="Cluster window">
          <div className="w-full max-w-xl rounded-lg border p-4">
            <ClusterWindow cluster={MOCK_CLUSTER} />
          </div>
        </Row>

        <Row title="Charts">
          <div className="grid w-full gap-4 lg:grid-cols-2">
            <ChartCard title="Rolling hit rate" qualifier="last 12 months">
              <TimeSeriesChart
                data={ROLLING}
                series={[
                  { key: "30d", label: "30-day" },
                  { key: "90d", label: "90-day" },
                ]}
                yFormat={(v) => `${v}%`}
                referenceY={50}
              />
            </ChartCard>
            <ChartCard title="Hit rate by horizon">
              <CategoryBarChart
                data={HIT_BY_HORIZON}
                series={[{ key: "hit", label: "Hit rate" }]}
                yFormat={(v) => `${v}%`}
                referenceY={50}
              />
            </ChartCard>
            <ChartCard title="Excess return distribution" qualifier="vs SPY" className="lg:col-span-2">
              <Boxplot rows={DIST} />
            </ChartCard>
          </div>
        </Row>

        <Row title="Empty & error states">
          <div className="grid w-full gap-3 sm:grid-cols-2">
            <EmptyState icon={DatabaseZap} title="No signals match your filters" description="Try widening the lookback window or lowering the minimum score." />
            <EmptyState variant="error" icon={DatabaseZap} title="Couldn't load signals" description="The database query failed. This usually clears on its own." />
          </div>
        </Row>
      </div>
    </PageShell>
  );
}
