"use client";

import { useMemo } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { factorMeta, isScoringKey } from "@/lib/scoring-factors";
import { THRESHOLDS } from "@/lib/scoring-model";
import type { ScoreBreakdown } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * What the score is, and what the filing said.
 *
 * This used to be a diverging bar chart of an additive factor table, which was
 * the right picture of the model at the time. There is one factor now, so a
 * chart of contributions would be a single full-width bar. The score renders as
 * a position on the scale it actually is, a percentile, with the BUY cutoff
 * marked; everything else the filing recorded is listed underneath as what it
 * is, context that does not move the number.
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
  const { ranked, context } = useMemo(() => {
    const keys = Object.keys(breakdown);
    return {
      ranked: keys.some(isScoringKey),
      context: keys.filter((k) => !isScoringKey(k)),
    };
  }, [breakdown]);

  const meta = factorMeta("discount_rank");
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
            {meta.research && (
              <p className="mt-2 border-t pt-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Measured: </span>
                {meta.research}
              </p>
            )}
          </PopoverContent>
        </Popover>
      ) : (
        <p className="text-sm text-muted-foreground text-pretty">
          {factorMeta("price_context_missing").reason}
        </p>
      )}

      {context.length > 0 && (
        <div className="space-y-1.5 border-t pt-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            On the filing, not in the score
          </p>
          <div className="flex flex-wrap gap-1">
            {context.map((key) => {
              const item = factorMeta(key);
              return (
                <Popover key={key}>
                  <PopoverTrigger
                    className={cn(
                      "rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground",
                      "hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                    )}
                  >
                    {item.label}
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-72">
                    <p className="font-medium">{item.label}</p>
                    <p className="text-xs text-muted-foreground">{item.reason}</p>
                    <p className="mt-2 border-t pt-2 text-xs text-muted-foreground text-pretty">
                      Recorded, not scored. Measured out of sample, none of the filing
                      attributes ranked purchases better than chance.
                    </p>
                  </PopoverContent>
                </Popover>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
