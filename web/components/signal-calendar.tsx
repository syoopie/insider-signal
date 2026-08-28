"use client";

import { useMemo } from "react";
import { useQueryStates } from "nuqs";
import { signalFilterParsers } from "@/lib/signal-filters";
import type { SignalDay } from "@/lib/queries/signals";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Signal volume per day across the lookback window. Two jobs:
 *
 * 1. Orientation. It counts everything the pipeline produced in the window,
 *    ignoring the score/type/cap filters, so a quiet list can be read as
 *    "nothing was filed" rather than "my filters are too tight".
 * 2. Navigation. Clicking a day pins the list to it.
 *
 * Weekends and holidays show as gaps because EDGAR does not file on them —
 * that absence is real information, so days are never collapsed out.
 */
export function SignalCalendar({ days, data }: { days: number; data: SignalDay[] }) {
  const [{ day }, setFilters] = useQueryStates(signalFilterParsers, {
    shallow: false,
    history: "replace",
  });

  const cells = useMemo(() => buildCells(days, data), [days, data]);
  const max = Math.max(1, ...cells.map((c) => c.total));
  const windowTotal = cells.reduce((sum, c) => sum + c.total, 0);

  return (
    <section aria-label="Signals per day">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">
          Signals per day
          <span className="ml-2 font-normal text-muted-foreground">
            {windowTotal} in the last {days} days, before filters
          </span>
        </h2>
        <span className="hidden text-xs text-muted-foreground sm:inline">
          Click a day to pin the list to it
        </span>
      </div>

      <div className="flex items-end gap-[3px] overflow-x-auto rounded-lg border bg-card px-3 py-3">
        {cells.map((c) => {
          const selected = day === c.date;
          // Square-root scaling: a single quiet day stays visible next to a
          // 40-signal filing spike instead of collapsing to a hairline.
          const height = c.total === 0 ? 3 : 6 + Math.round((Math.sqrt(c.total) / Math.sqrt(max)) * 42);
          return (
            <button
              key={c.date}
              type="button"
              aria-pressed={selected}
              onClick={() => setFilters({ day: selected ? null : c.date })}
              title={`${fmtDate(c.date, { withYear: true })} — ${c.total} signal${
                c.total === 1 ? "" : "s"
              }${c.total > 0 ? ` (${c.clusterBuy} cluster · ${c.buy} buy · ${c.watch} watch)` : ""}`}
              className="group flex min-w-[8px] flex-1 flex-col justify-end rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
              style={{ height: 52 }}
            >
              <span
                className={cn(
                  "w-full rounded-sm transition-colors",
                  c.total === 0
                    ? "bg-border"
                    : selected
                      ? "bg-primary"
                      : "bg-chart-1/60 group-hover:bg-chart-1",
                )}
                style={{ height }}
              />
            </button>
          );
        })}
      </div>
    </section>
  );
}

/**
 * Every day in the window, including the ones with no signals. The query only
 * returns days that have rows, so the gaps have to be filled in here.
 */
function buildCells(days: number, data: SignalDay[]): SignalDay[] {
  const byDate = new Map(data.map((d) => [d.date, d]));
  const out: SignalDay[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const iso = d.toISOString().slice(0, 10);
    out.push(byDate.get(iso) ?? { date: iso, total: 0, clusterBuy: 0, buy: 0, watch: 0 });
  }
  return out;
}
