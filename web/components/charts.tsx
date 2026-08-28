"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Card + heading + optional right-aligned qualifier around any chart body. */
export function ChartCard({
  title,
  qualifier,
  children,
  className,
}: {
  title: string;
  qualifier?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-baseline justify-between gap-2 pb-0">
        <h3 className="font-heading text-sm font-semibold">{title}</h3>
        {qualifier && <span className="text-xs text-muted-foreground">{qualifier}</span>}
      </CardHeader>
      <CardContent className="pt-4">{children}</CardContent>
    </Card>
  );
}

const SERIES_VARS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
];

export type SeriesDef = { key: string; label: string };

function configFor(series: SeriesDef[]): ChartConfig {
  return Object.fromEntries(
    series.map((s, i) => [s.key, { label: s.label, color: SERIES_VARS[i % SERIES_VARS.length] }]),
  );
}

/** Multi-series line chart. `data` rows have an `x` field plus one field per series key. */
export function TimeSeriesChart({
  data,
  series,
  xKey = "x",
  yFormat = (v) => String(v),
  referenceY,
  height = 260,
}: {
  data: Record<string, number | string>[];
  series: SeriesDef[];
  xKey?: string;
  yFormat?: (v: number) => string;
  referenceY?: number;
  height?: number;
}) {
  const config = configFor(series);
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <LineChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey={xKey}
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          minTickGap={32}
          className="text-xs"
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={44}
          tickFormatter={yFormat}
          className="text-xs"
        />
        {referenceY != null && (
          <ReferenceLine y={referenceY} strokeDasharray="4 4" className="stroke-muted-foreground" />
        )}
        <ChartTooltip content={<ChartTooltipContent />} />
        {series.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            stroke={`var(--color-${s.key})`}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}

/** Categorical bars with an optional reference line (e.g. 50% coin-flip). */
export function CategoryBarChart({
  data,
  series,
  xKey = "x",
  yFormat = (v) => String(v),
  referenceY,
  height = 240,
}: {
  data: Record<string, number | string>[];
  series: SeriesDef[];
  xKey?: string;
  yFormat?: (v: number) => string;
  referenceY?: number;
  height?: number;
}) {
  const config = configFor(series);
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <BarChart data={data} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" className="stroke-border" />
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tickMargin={8} className="text-xs" />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={44}
          tickFormatter={yFormat}
          className="text-xs"
        />
        {referenceY != null && (
          <ReferenceLine y={referenceY} strokeDasharray="4 4" className="stroke-muted-foreground" />
        )}
        <ChartTooltip content={<ChartTooltipContent />} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} fill={`var(--color-${s.key})`} radius={[4, 4, 0, 0]} />
        ))}
      </BarChart>
    </ChartContainer>
  );
}

/**
 * Distribution as a horizontal box plot per group. Recharts has no box mark, so
 * this is a small hand-drawn SVG: box = p25..p75, line = median, whiskers = min..max.
 * The mean alone hides tail risk, which is the whole point of showing it.
 */
export type BoxRow = {
  label: string;
  p25: number;
  median: number;
  p75: number;
  min: number;
  max: number;
};

export function Boxplot({ rows, unit = "%" }: { rows: BoxRow[]; unit?: string }) {
  const all = rows.flatMap((r) => [r.min, r.max]);
  const lo = Math.min(0, ...all);
  const hi = Math.max(0, ...all);
  const span = hi - lo || 1;
  const x = (v: number) => ((v - lo) / span) * 100;

  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[64px_1fr] items-center gap-3">
          <span className="text-xs font-medium text-muted-foreground">{r.label}</span>
          <div className="relative h-6">
            {/* zero line */}
            <div
              className="absolute inset-y-0 w-px bg-border"
              style={{ left: `${x(0)}%` }}
              aria-hidden
            />
            {/* whisker */}
            <div
              className="absolute top-1/2 h-px -translate-y-1/2 bg-muted-foreground"
              style={{ left: `${x(r.min)}%`, width: `${x(r.max) - x(r.min)}%` }}
            />
            {/* box */}
            <div
              className="absolute top-1/2 h-4 -translate-y-1/2 rounded-sm border border-primary/50 bg-primary/15"
              style={{ left: `${x(r.p25)}%`, width: `${Math.max(1, x(r.p75) - x(r.p25))}%` }}
            />
            {/* median */}
            <div
              className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 bg-primary"
              style={{ left: `${x(r.median)}%` }}
              title={`median ${r.median}${unit}`}
            />
          </div>
        </div>
      ))}
      <div className={cn("flex justify-between text-[10px] text-muted-foreground")}>
        <span>
          {lo.toFixed(0)}
          {unit}
        </span>
        <span>0{unit}</span>
        <span>
          +{hi.toFixed(0)}
          {unit}
        </span>
      </div>
    </div>
  );
}
