"use client";

import { useQueryStates } from "nuqs";
import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FilterChip, FilterGroup } from "@/components/filter-chip";
import {
  DEFAULT_CAPS,
  DEFAULT_LOOKBACK,
  DEFAULT_MIN_SCORE,
  DEFAULT_TYPES,
  LOOKBACK_OPTIONS,
  MIN_SCORE_OPTIONS,
  signalFilterParsers,
} from "@/lib/signal-filters";
import { fmtDate } from "@/lib/format";
import type { CapTier, SignalType } from "@/lib/types";

const TYPE_LABELS: Record<Exclude<SignalType, "LOW">, string> = {
  CLUSTER_BUY: "Cluster Buy",
  BUY: "Buy",
  WATCH: "Watch",
};

const CAP_LABELS: Record<CapTier, { label: string; title: string }> = {
  small: { label: "Small", title: "Under $2B — where the research finds the most alpha" },
  mid: { label: "Mid", title: "$2B–$10B" },
  large: { label: "Large", title: "Over $10B — 0% hit rate at 90d in this system's backtest" },
  unknown: { label: "Unknown", title: "Market cap not resolvable from EDGAR; scored conservatively" },
};

/**
 * The triage filter bar. Every control writes to the URL, so a filtered view is
 * a link. Chips rather than sliders: the score is a sum of fixed integer
 * factors, so a continuous control would imply precision the model doesn't have.
 */
export function SignalFilters() {
  const [filters, setFilters] = useQueryStates(signalFilterParsers, {
    shallow: false, // the server component owns the query, so the URL change must reach it
    history: "replace",
  });

  const toggle = <T extends string>(current: T[], value: T): T[] =>
    current.includes(value) ? current.filter((v) => v !== value) : [...current, value];

  const isDefault =
    filters.days === DEFAULT_LOOKBACK &&
    filters.min === DEFAULT_MIN_SCORE &&
    filters.q === "" &&
    filters.day === "" &&
    sameSet(filters.types, DEFAULT_TYPES) &&
    sameSet(filters.caps, DEFAULT_CAPS);

  return (
    <div className="rounded-xl border bg-card/50 p-4">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        <FilterGroup label="Lookback">
          {LOOKBACK_OPTIONS.map((d) => (
            <FilterChip
              key={d}
              selected={filters.days === d}
              onClick={() => setFilters({ days: d, day: null })}
            >
              {d}d
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Min score">
          {MIN_SCORE_OPTIONS.map((s) => (
            <FilterChip key={s} selected={filters.min === s} onClick={() => setFilters({ min: s })}>
              {s === 0 ? "Any" : `≥ ${s}`}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Signal type">
          {(Object.keys(TYPE_LABELS) as Array<keyof typeof TYPE_LABELS>).map((t) => (
            <FilterChip
              key={t}
              selected={filters.types.includes(t)}
              onClick={() => setFilters({ types: toggle(filters.types, t) })}
            >
              {TYPE_LABELS[t]}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Market cap">
          {(Object.keys(CAP_LABELS) as CapTier[]).map((c) => (
            <FilterChip
              key={c}
              title={CAP_LABELS[c].title}
              selected={filters.caps.includes(c)}
              onClick={() => setFilters({ caps: toggle(filters.caps, c) })}
            >
              {CAP_LABELS[c].label}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Search">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filters.q}
              // Shallow: the list component filters the rows it already has, so
              // typing updates the URL without a server round trip per keystroke.
              onChange={(e) => setFilters({ q: e.target.value || null }, { shallow: true })}
              placeholder="Ticker or company"
              aria-label="Search by ticker or company"
              className="h-7 w-44 pl-8 text-xs"
            />
          </div>
        </FilterGroup>
      </div>

      {(filters.day || !isDefault) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
          {filters.day && (
            <button
              type="button"
              onClick={() => setFilters({ day: null })}
              className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 text-xs font-medium hover:bg-primary/20"
            >
              {fmtDate(filters.day, { withYear: true })} only
              <X className="size-3" aria-hidden />
              <span className="sr-only">Clear day filter</span>
            </button>
          )}
          {!isDefault && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() =>
                setFilters({ days: null, min: null, types: null, caps: null, q: null, day: null })
              }
            >
              Reset filters
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && [...a].sort().join() === [...b].sort().join();
}
