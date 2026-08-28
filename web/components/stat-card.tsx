import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type StatCardProps = {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** Non-numeric values (a name, a date) read better without the mono treatment. */
  variant?: "numeric" | "text";
  className?: string;
};

export function StatCard({ label, value, hint, variant = "numeric", className }: StatCardProps) {
  return (
    <Card className={cn("py-4", className)}>
      <CardContent className="px-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <p
          className={cn(
            "mt-1 truncate text-2xl font-semibold",
            // Sans + tabular-nums for large display figures. A monospace comma
            // reads as a gap at this size; mono is reserved for table columns.
            variant === "numeric" && "tabular-nums",
          )}
        >
          {value}
        </p>
        {hint && <p className="mt-1 text-xs text-muted-foreground text-pretty">{hint}</p>}
      </CardContent>
    </Card>
  );
}
