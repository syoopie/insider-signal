import { confidenceFor } from "@/lib/confidence";
import { cn } from "@/lib/utils";

const META = {
  none: { title: "No signals in this bucket", className: "text-muted-foreground" },
  low: {
    title: "Fewer than 10 signals — not enough to draw any conclusion",
    className: "text-destructive",
  },
  medium: {
    title: "Fewer than 30 signals — directional at best",
    className: "text-warning",
  },
  high: { title: "30 or more signals", className: "text-foreground" },
} as const;

/**
 * A sample size that says how far to trust itself.
 *
 * The backtest slices thin — some cap-tier/horizon cells hold a handful of
 * signals — and a hit rate over n=6 reads exactly like one over n=600 unless
 * the count is marked. Weight and a symbol carry the warning, not colour alone.
 */
export function SampleSize({ n, className }: { n: number; className?: string }) {
  const level = confidenceFor(n);
  const meta = META[level];
  return (
    <span className={cn("tabular-nums", meta.className, className)} title={meta.title}>
      {n || "—"}
      {level === "low" && <span aria-hidden> !</span>}
      {level === "medium" && <span aria-hidden> ~</span>}
      {(level === "low" || level === "medium") && (
        <span className="sr-only"> ({meta.title})</span>
      )}
    </span>
  );
}

/** The legend that makes the markers above readable. */
export function SampleSizeLegend({ className }: { className?: string }) {
  return (
    <p className={cn("text-xs text-muted-foreground", className)}>
      <span className="text-destructive">n !</span> under 10 signals — ignore ·{" "}
      <span className="text-warning">n ~</span> under 30 — directional only
    </p>
  );
}
