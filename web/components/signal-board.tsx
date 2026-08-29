"use client";

import { useMemo } from "react";
import { useQueryStates } from "nuqs";
import { Inbox, Sparkles } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { SignalCard } from "@/components/signal-card";
import { TopPicks } from "@/components/top-picks";
import { Button } from "@/components/ui/button";
import { matchesQuery, signalFilterParsers } from "@/lib/signal-filters";
import { isNewSince, useLastVisit } from "@/lib/use-last-visit";
import { fmtDate } from "@/lib/format";
import type { Signal } from "@/lib/queries/signals";

/**
 * The triage list itself: summary, top picks, and every matching signal.
 *
 * Rows arrive already filtered by the server on the four bounded filters and the
 * day pin. Text search is re-applied here so typing filters instantly against
 * rows that are already in the browser.
 */
export function SignalBoard({ signals }: { signals: Signal[] }) {
  const [{ q }, setFilters] = useQueryStates(signalFilterParsers, { shallow: true });
  const { since, markAllSeen } = useLastVisit();

  const rows = useMemo(() => signals.filter((s) => matchesQuery(s, q)), [signals, q]);

  const counts = useMemo(
    () => ({
      clusterBuy: rows.filter((s) => s.signalType === "CLUSTER_BUY").length,
      buy: rows.filter((s) => s.signalType === "BUY").length,
      watch: rows.filter((s) => s.signalType === "WATCH").length,
      avgScore: rows.length
        ? Math.round(rows.reduce((sum, s) => sum + s.score, 0) / rows.length)
        : 0,
    }),
    [rows],
  );

  // `since` is null on the server and during hydration, so nothing is marked new
  // until the browser's own value arrives — no mismatch, no flash.
  const newCount = rows.filter((s) => isNewSince(s.signalDate, since)).length;

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title={q ? `Nothing matches “${q}”` : "No signals match these filters"}
        description={
          q
            ? "Clear the search to see the rest of the window."
            : "Widen the lookback, lower the minimum score, or add a signal type. The chart above shows what the pipeline actually produced in this window."
        }
      >
        {q && (
          <Button variant="outline" size="sm" onClick={() => setFilters({ q: null })}>
            Clear search
          </Button>
        )}
      </EmptyState>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <Summary label="Matching" value={rows.length} />
        <Summary label="Cluster buy" value={counts.clusterBuy} />
        <Summary label="Buy" value={counts.buy} />
        {counts.watch > 0 && <Summary label="Watch" value={counts.watch} />}
        <Summary label="Avg score" value={counts.avgScore} />
      </div>

      {newCount > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="size-4 text-primary" aria-hidden />
            <span>
              <strong className="font-medium">{newCount}</strong> new since your last visit
              {since && (
                <span className="text-muted-foreground"> ({fmtDate(since, { withYear: true })})</span>
              )}
            </span>
          </span>
          <Button variant="ghost" size="xs" onClick={markAllSeen}>
            Mark all seen
          </Button>
        </div>
      )}

      <TopPicks signals={rows} />

      <section>
        <h2 className="mb-2 text-sm font-medium">
          All signals
          <span className="ml-2 font-normal text-muted-foreground">
            {rows.length}, highest conviction first
          </span>
        </h2>
        <div className="space-y-2">
          {rows.map((s) => (
            <SignalCard key={s.id} signal={s} isNew={isNewSince(s.signalDate, since)} />
          ))}
        </div>
      </section>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}
