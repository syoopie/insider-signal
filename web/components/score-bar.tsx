"use client";

import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SCORING_FACTORS } from "@/lib/scoring-factors";
import { THRESHOLDS } from "@/lib/scoring-model";
import type { ScoreBreakdown } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * What the score is: a position on the scale it actually is, a percentile, with
 * the BUY cutoff marked. There is one factor, so a chart of contributions would
 * be a single full-width bar.
 */
export function ScoreBar({
  breakdown,
  score,
  className,
}: {
  breakdown: ScoreBreakdown;
  score: number;
  className?: string;
}) {
  const ranked = "discount_rank" in breakdown;
  const meta = SCORING_FACTORS.discount_rank;
  const buyPct = THRESHOLDS.buy;

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Score
        </span>
        <span className="font-mono text-sm font-semibold tabular-nums">{score}/100</span>
      </div>

      {ranked ? (
        <Popover>
          <PopoverTrigger
            className={cn(
              "group w-full space-y-1.5 rounded px-1 py-1 text-left",
              "hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
            )}
          >
            <span className="flex items-center gap-1.5 text-xs">
              <span className="truncate">{meta.label}</span>
              <Info className="size-3 shrink-0 text-muted-foreground opacity-40 group-hover:opacity-100" />
            </span>
            <div className="relative h-1.5 w-full rounded-full bg-muted">
              <div
                className={cn(
                  "h-1.5 rounded-full",
                  score >= buyPct ? "bg-success/80" : "bg-muted-foreground/50",
                )}
                style={{ width: `${Math.max(1, Math.min(100, score))}%` }}
              />
              <div
                aria-hidden
                className="absolute inset-y-[-2px] w-px bg-border"
                style={{ left: `${buyPct}%` }}
                title={`BUY at ${buyPct}`}
              />
            </div>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72">
            <p className="font-medium">{meta.label}</p>
            <p className="text-xs text-muted-foreground">{meta.reason}</p>
            <p className="mt-2 border-t pt-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Measured: </span>
              {meta.research}
            </p>
          </PopoverContent>
        </Popover>
      ) : (
        <p className="text-sm text-muted-foreground text-pretty">
          {SCORING_FACTORS.price_context_missing.reason}
        </p>
      )}
    </div>
  );
}
