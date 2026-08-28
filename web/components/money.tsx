import { fmtCurrency, fmtPctSigned } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Money rule: always show the sign, never encode direction by colour alone.
 * Inflow (positive) is `text-success`; the default reading colour carries
 * negatives. Above a magnitude threshold, weight (not hue) marks size.
 */
export function Money({
  value,
  className,
  emphasizeAbove,
}: {
  value: number | null | undefined;
  className?: string;
  /** Bold when |value| >= this. */
  emphasizeAbove?: number;
}) {
  const n = value == null ? null : Number(value);
  const positive = n != null && n > 0;
  const heavy = n != null && emphasizeAbove != null && Math.abs(n) >= emphasizeAbove;
  return (
    <span
      className={cn(
        "tabular-nums",
        positive ? "text-success" : "text-foreground",
        heavy && "font-semibold",
        className,
      )}
    >
      {n != null && n > 0 ? "+" : ""}
      {fmtCurrency(n)}
    </span>
  );
}

/** Signed percentage return. Same colour contract as Money. */
export function Return({
  value,
  className,
  digits = 1,
}: {
  value: number | null | undefined;
  className?: string;
  digits?: number;
}) {
  const n = value == null ? null : Number(value);
  return (
    <span
      className={cn(
        "tabular-nums",
        n != null && n > 0 ? "text-success" : n != null && n < 0 ? "text-destructive" : "text-muted-foreground",
        className,
      )}
    >
      {fmtPctSigned(n, digits)}
    </span>
  );
}
