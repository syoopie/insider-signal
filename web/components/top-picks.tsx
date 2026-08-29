"use client";

import { Layers } from "lucide-react";
import { ConvictionBadge, convictionFor } from "@/components/badges";
import { fmtCurrency, fmtDate } from "@/lib/format";
import type { Signal } from "@/lib/queries/signals";
import { titleCase } from "@/lib/format";

/**
 * The three signals worth opening first. Cluster buys lead — three or more
 * insiders buying inside 14 days is the strongest pattern in the research — and
 * the list falls back to plain BUY signals when no cluster is in the window.
 *
 * Each card links to its full entry in the list below rather than duplicating
 * the evidence, so there is exactly one place a signal is explained.
 */
export function TopPicks({ signals }: { signals: Signal[] }) {
  const clusters = signals.filter((s) => s.signalType === "CLUSTER_BUY").slice(0, 3);
  const top = clusters.length > 0 ? clusters : signals.filter((s) => s.signalType === "BUY").slice(0, 3);

  if (top.length === 0) return null;

  return (
    <section>
      <h2 className="mb-2 text-sm font-medium">
        Top picks
        <span className="ml-2 font-normal text-muted-foreground">
          {clusters.length > 0
            ? "highest-conviction clusters in this window"
            : "highest-scoring buys in this window (no clusters)"}
        </span>
      </h2>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {top.map((s) => {
          const cluster = s.evidence.cluster;
          const conviction = convictionFor(s.signalType, s.score, cluster);
          const tags: string[] = [];
          if (cluster?.tight_cluster) tags.push("tight window");
          if (cluster?.executive_cluster) tags.push("exec cluster");

          return (
            <a
              key={s.id}
              href={`#signal-${s.id}`}
              className="group flex flex-col gap-2 rounded-xl border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              {conviction && <ConvictionBadge conviction={conviction} className="self-start" />}

              <div className="min-w-0">
                <div className="font-mono text-2xl font-semibold tracking-tight">{s.ticker}</div>
                <div className="truncate text-sm text-muted-foreground" title={s.companyName}>
                  {s.companyName}
                </div>
              </div>

              <dl className="mt-auto grid grid-cols-2 gap-x-3 gap-y-1 pt-1 text-xs">
                <dt className="text-muted-foreground">Score</dt>
                <dd className="text-right font-mono font-medium tabular-nums">{s.score}/100</dd>
                <dt className="text-muted-foreground">Bought</dt>
                <dd className="text-right tabular-nums text-success">
                  {s.totalValue != null ? fmtCurrency(s.totalValue) : "—"}
                </dd>
                <dt className="text-muted-foreground">Cap tier</dt>
                <dd className="text-right">{titleCase(s.capTier)}</dd>
              </dl>

              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t pt-2 text-xs text-muted-foreground">
                {s.insiderCount > 1 && (
                  <span className="inline-flex items-center gap-1">
                    <Layers className="size-3" aria-hidden />
                    {s.insiderCount} insiders
                  </span>
                )}
                <span>{fmtDate(s.signalDate, { withYear: true })}</span>
                {tags.length > 0 && <span className="text-cluster">{tags.join(" · ")}</span>}
              </div>
            </a>
          );
        })}
      </div>
    </section>
  );
}
