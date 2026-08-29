"use client";

import Link from "next/link";
import { Layers, Users } from "lucide-react";
import { CapTierBadge, ConvictionBadge, SignalTypeBadge, convictionFor } from "@/components/badges";
import { ClusterWindow } from "@/components/cluster-window";
import { EmptyState } from "@/components/empty-state";
import { fmtCurrency, fmtDate, titleCase } from "@/lib/format";
import type { ClusterSignal } from "@/lib/queries/clusters";

/**
 * Every cluster in the window, each shown as its 14-day timeline.
 *
 * The list leads with the timeline rather than a table because the shape of a
 * cluster is the point: three insiders buying on one day after an offering is a
 * different event from three buying independently across two weeks, and only
 * the first tells you nothing.
 */
export function ClusterList({ clusters }: { clusters: ClusterSignal[] }) {
  if (clusters.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No clusters in this window"
        description="A cluster needs three or more distinct insiders making direct open-market purchases of at least $25,000 within 14 days. Most windows have none."
      />
    );
  }

  return (
    <div className="space-y-3">
      {clusters.map((c) => {
        const conviction = convictionFor(c.signalType, c.score, c.cluster);
        return (
        <article key={c.id} className="rounded-xl border bg-card p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Link
                  href={`/ticker/${encodeURIComponent(c.ticker)}`}
                  className="font-mono text-base font-semibold tracking-tight underline-offset-2 hover:underline"
                >
                  {c.ticker}
                </Link>
                <span className="truncate text-sm text-muted-foreground">{c.companyName}</span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <SignalTypeBadge type={c.signalType} />
                {conviction && <ConvictionBadge conviction={conviction} />}
                <CapTierBadge tier={c.capTier} />
                <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs text-muted-foreground">
                  <Layers className="size-3" aria-hidden />
                  {c.insiderCount} insiders
                </span>
              </div>
              {c.roles.length > 0 && (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {c.roles.map(titleCase).join(" · ")}
                </p>
              )}
            </div>

            <div className="flex gap-5 text-right">
              <div>
                <div className="text-xs text-muted-foreground">Bought</div>
                <div className="text-sm font-medium tabular-nums text-success">
                  {c.totalValue != null ? fmtCurrency(c.totalValue) : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Signal</div>
                <div className="text-sm tabular-nums">{fmtDate(c.signalDate, { withYear: true })}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Score</div>
                <div className="font-mono text-base font-semibold tabular-nums">{c.score}</div>
              </div>
            </div>
          </div>

          <div className="mt-4 border-t pt-4">
            <ClusterWindow cluster={c.cluster} />
          </div>
        </article>
        );
      })}
    </div>
  );
}
