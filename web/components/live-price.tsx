"use client";

import { usePrice } from "@/lib/use-price";
import { Return } from "@/components/money";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Current quote, fetched in the browser after the page renders.
 *
 * Everything else on the page comes from the database and changes once a day;
 * this is the one number that moves continuously, so it is loaded separately
 * and labelled as live rather than folded into the cached render.
 */
export function LivePrice({
  ticker,
  compareTo,
  compareLabel = "vs insider entry",
}: {
  ticker: string;
  /** Optional reference price (e.g. the average insider entry) to show a move against. */
  compareTo?: number | null;
  compareLabel?: string;
}) {
  const { price, isLoading, failed } = usePrice(ticker);

  if (isLoading) return <Skeleton className="h-6 w-28" />;
  if (failed || price == null) {
    return (
      <span className="text-sm text-muted-foreground" title="Yahoo Finance did not return a quote">
        Price unavailable
      </span>
    );
  }

  const change =
    compareTo != null && compareTo > 0 ? ((price - compareTo) / compareTo) * 100 : null;

  return (
    <span className="flex items-baseline gap-2">
      <span className="text-lg font-semibold tabular-nums">${price.toFixed(2)}</span>
      {change != null && (
        <span className="text-sm">
          <Return value={change} /> <span className="text-muted-foreground">{compareLabel}</span>
        </span>
      )}
      <span className="text-xs text-muted-foreground">live</span>
    </span>
  );
}
