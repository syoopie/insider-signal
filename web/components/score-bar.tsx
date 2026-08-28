"use client";

import { useMemo } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { factorMeta } from "@/lib/scoring-factors";
import type { ScoreBreakdown } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The score's factor breakdown as a diverging bar chart. Positive factors grow
 * right in `--success`, penalties grow left in `--destructive`. Every row is a
 * button that opens a plain-English explanation with the research citation.
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
  const rows = useMemo(() => {
    const entries = Object.entries(breakdown).filter(([, v]) => v !== 0);
    entries.sort((a, b) => b[1] - a[1]);
    const maxAbs = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));
    return { entries, maxAbs };
  }, [breakdown]);

  if (rows.entries.length === 0) {
    return <p className="text-sm text-muted-foreground">No scored factors.</p>;
  }

  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Score breakdown
        </span>
        <span className="font-mono text-sm font-semibold tabular-nums">{score}/100</span>
      </div>

      {rows.entries.map(([key, value]) => {
        const meta = factorMeta(key);
        const pct = (Math.abs(value) / rows.maxAbs) * 100;
        const positive = value > 0;
        return (
          <Popover key={key}>
            <PopoverTrigger
              className={cn(
                "group grid w-full grid-cols-[1fr_auto] items-center gap-x-3 rounded px-1 py-0.5 text-left text-xs",
                "hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
              )}
            >
              <span className="flex items-center gap-1.5">
                <span className="truncate">{meta.label}</span>
                <Info className="size-3 shrink-0 text-muted-foreground opacity-40 group-hover:opacity-100" />
              </span>
              <span
                className={cn(
                  "font-mono font-medium tabular-nums",
                  positive ? "text-success" : "text-destructive",
                )}
              >
                {positive ? "+" : ""}
                {value}
              </span>
              <div className="col-span-2 mt-0.5 flex h-1.5 items-center">
                <div className="flex-1">
                  <div className="flex justify-end">
                    {!positive && (
                      <div
                        className="h-1.5 rounded-l-full bg-destructive/70"
                        style={{ width: `${pct}%` }}
                      />
                    )}
                  </div>
                </div>
                <div className="w-px self-stretch bg-border" />
                <div className="flex-1">
                  {positive && (
                    <div
                      className="h-1.5 rounded-r-full bg-success/70"
                      style={{ width: `${pct}%` }}
                    />
                  )}
                </div>
              </div>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-72">
              <p className="font-medium">{meta.label}</p>
              <p className="text-xs text-muted-foreground">{meta.reason}</p>
              {meta.research && (
                <p className="border-t pt-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Research: </span>
                  {meta.research}
                </p>
              )}
            </PopoverContent>
          </Popover>
        );
      })}
    </div>
  );
}
