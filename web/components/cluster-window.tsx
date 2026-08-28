import type { Cluster } from "@/lib/types";
import { fmtCurrency, fmtDate, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The cluster's rolling window as a timeline: each insider purchase is a marker
 * placed by its transaction date between window_start and window_end. Makes
 * "3 insiders bought within 5 days" legible at a glance.
 */
export function ClusterWindow({ cluster, className }: { cluster: Cluster; className?: string }) {
  const start = cluster.window_start ? new Date(cluster.window_start) : null;
  const end = cluster.window_end ? new Date(cluster.window_end) : null;
  const txns = (cluster.insiders ?? []).filter((t) => t.transaction_date);

  if (!start || !end || txns.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">Window detail unavailable for this cluster.</p>
    );
  }

  const span = Math.max(1, end.getTime() - start.getTime());
  const pos = (iso: string) =>
    Math.min(100, Math.max(0, ((new Date(iso).getTime() - start.getTime()) / span) * 100));

  const sorted = [...txns].sort(
    (a, b) => new Date(a.transaction_date!).getTime() - new Date(b.transaction_date!).getTime(),
  );

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{fmtDate(cluster.window_start, { withYear: true })}</span>
        <span>
          {txns.length} purchase{txns.length === 1 ? "" : "s"} in{" "}
          {Math.round(span / 86_400_000) + 1} days
        </span>
        <span>{fmtDate(cluster.window_end, { withYear: true })}</span>
      </div>

      <div className="relative h-2 rounded-full bg-muted">
        {sorted.map((t, i) => (
          <div
            key={`${t.insider_name}-${i}`}
            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-cluster"
            style={{ left: `${pos(t.transaction_date!)}%` }}
            title={`${t.insider_name} — ${fmtDate(t.transaction_date, { withYear: true })} — ${fmtCurrency(
              t.total_value,
            )}`}
          />
        ))}
      </div>

      <ul className="space-y-1 text-sm">
        {sorted.map((t, i) => (
          <li
            key={`${t.insider_name}-row-${i}`}
            className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3"
          >
            <span className="truncate">
              {t.insider_name}
              <span className="ml-1.5 text-xs text-muted-foreground">
                {titleCase(t.role_category)}
              </span>
            </span>
            <span className="text-xs text-muted-foreground">
              {fmtDate(t.transaction_date, { withYear: true })}
            </span>
            <span className="font-mono text-xs tabular-nums text-success">
              {fmtCurrency(t.total_value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
