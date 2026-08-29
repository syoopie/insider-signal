"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, ChevronDown, ExternalLink, Layers, TrendingDown } from "lucide-react";
import { CapTierBadge, ConvictionBadge, SignalTypeBadge, convictionFor } from "@/components/badges";
import { ClusterWindow } from "@/components/cluster-window";
import { InsiderTable } from "@/components/insider-table";
import { ScoreBar } from "@/components/score-bar";
import { fmtCurrency, fmtDate, fmtRelative } from "@/lib/format";
import type { Signal } from "@/lib/queries/signals";
import { cn } from "@/lib/utils";

/**
 * One signal in the triage list. Collapsed, the header answers "is this worth
 * my attention" — ticker, conviction, score, how much was bought, when.
 * Expanded, it answers "why", with the evidence the score was computed from.
 */
export function SignalCard({ signal, isNew = false }: { signal: Signal; isNew?: boolean }) {
  const [open, setOpen] = useState(false);
  const cluster = signal.evidence.cluster;
  const conviction = convictionFor(signal.signalType, signal.score, cluster);
  const bodyId = `signal-${signal.id}-detail`;

  // For a plain BUY under 70 the conviction is literally "Buy", which the type
  // badge beside it already says. Only show it when it adds something.
  const showConviction =
    conviction !== null && (signal.signalType === "CLUSTER_BUY" || conviction === "HIGH");

  const tags: string[] = [];
  if (cluster?.tight_cluster) tags.push("tight window");
  if (cluster?.executive_cluster) tags.push("exec cluster");

  return (
    <div
      id={`signal-${signal.id}`}
      className={cn(
        "scroll-mt-24 overflow-hidden rounded-xl border bg-card transition-colors",
        open && "border-primary/30",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/40 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      >
        <ChevronDown
          className={cn(
            "mt-1 size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />

        <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="font-mono text-base font-semibold tracking-tight">
                {signal.ticker}
              </span>
              <span className="sr-only">— expand for evidence</span>
              <span className="min-w-0 truncate text-sm text-muted-foreground">
                {signal.companyName}
              </span>
              {isNew && (
                <span className="rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                  New
                </span>
              )}
            </div>

            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <SignalTypeBadge type={signal.signalType} />
              {showConviction && <ConvictionBadge conviction={conviction} />}
              <CapTierBadge tier={signal.capTier} />
              {signal.insiderCount > 1 && (
                <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                  <Layers className="size-3" aria-hidden />
                  {signal.insiderCount} insiders
                </span>
              )}
              {signal.evidence.near_52wk_low && (
                <span className="inline-flex items-center gap-1 rounded-full border border-watch/30 bg-watch/10 px-2 py-0.5 text-xs text-watch">
                  <TrendingDown className="size-3" aria-hidden />
                  Near 52wk low
                </span>
              )}
              {tags.length > 0 && (
                <span className="text-xs text-muted-foreground">{tags.join(" · ")}</span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-4 sm:justify-end">
            <div className="text-left sm:text-right">
              <div className="text-xs text-muted-foreground">Bought</div>
              <div className="tabular-nums text-sm font-medium text-success">
                {signal.totalValue != null ? fmtCurrency(signal.totalValue) : "—"}
              </div>
            </div>
            <div className="text-left sm:text-right">
              <div className="text-xs text-muted-foreground">Signal</div>
              <div
                className="text-sm tabular-nums"
                title={fmtDate(signal.signalDate, { withYear: true })}
              >
                {fmtRelative(signal.signalDate)}
              </div>
            </div>
            <div className="min-w-14 text-right">
              <div className="text-xs text-muted-foreground">Score</div>
              <div className="font-mono text-lg font-semibold leading-tight tabular-nums">
                {signal.score}
                <span className="text-xs font-normal text-muted-foreground">/100</span>
              </div>
            </div>
          </div>
        </div>
      </button>

      {open && (
        <div id={bodyId} className="border-t bg-muted/20 px-4 py-4">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="min-w-0 space-y-4">
              <section>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Who bought
                </h3>
                <InsiderTable insiders={signal.evidence.insiders ?? []} />
              </section>

              {cluster?.is_cluster && (
                <section>
                  <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Cluster window
                  </h3>
                  <p className="mb-3 text-sm text-muted-foreground text-pretty">
                    {cluster.insider_count ?? 0} insiders bought within the same 14-day window
                    {cluster.tight_cluster && ", at least 3 of them inside 5 days"}
                    {cluster.executive_cluster && ", including a CFO, CEO, COO or Chairman"}. Three
                    or more insiders buying together historically carries about twice the alpha of a
                    single buy.
                  </p>
                  <ClusterWindow cluster={cluster} />
                </section>
              )}

              {signal.evidence.near_52wk_low && (
                <p className="rounded-lg border border-watch/30 bg-watch/5 px-3 py-2 text-sm">
                  Trading near its 52-week low
                  {signal.evidence.price_52wk_low != null &&
                    ` — ${(signal.evidence.pct_above_52wk_low ?? 0).toFixed(0)}% above $${signal.evidence.price_52wk_low.toFixed(2)}`}
                  . Buying into weakness scores higher than buying strength.
                </p>
              )}
            </div>

            <div className="min-w-0 space-y-4">
              <ScoreBar breakdown={signal.breakdown} score={signal.score} />

              {(signal.evidence.research_basis ?? []).length > 0 && (
                <section>
                  <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Research basis
                  </h3>
                  <ul className="space-y-1 text-xs text-muted-foreground">
                    {signal.evidence.research_basis!.map((r, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span aria-hidden>•</span>
                        <span className="text-pretty">{r}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-3 text-xs">
                <dt className="text-muted-foreground">Filed with SEC</dt>
                <dd className="text-right tabular-nums">
                  {fmtDate(signal.filedDate, { withYear: true })}
                </dd>
                <dt className="text-muted-foreground">Signal date</dt>
                <dd className="text-right tabular-nums">
                  {fmtDate(signal.signalDate, { withYear: true })}
                </dd>
                {signal.marketCap != null && (
                  <>
                    <dt className="text-muted-foreground">Market cap</dt>
                    <dd className="text-right tabular-nums">{fmtCurrency(signal.marketCap)}</dd>
                  </>
                )}
              </dl>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <Link
                  href={`/ticker/${encodeURIComponent(signal.ticker)}`}
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-2 hover:underline"
                >
                  Full history for {signal.ticker}
                  <ArrowRight className="size-3" aria-hidden />
                </Link>
                <a
                  href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(
                    signal.ticker,
                  )}&type=4&dateb=&owner=include&count=40`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  Verify on SEC EDGAR
                  <ExternalLink className="size-3" aria-hidden />
                </a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
