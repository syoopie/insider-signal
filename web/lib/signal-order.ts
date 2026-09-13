import type { Signal } from "@/lib/queries/signals";

/** Newest trade first. Ties go to the later filing, then the higher score. */
export function byDate(a: Signal, b: Signal): number {
  return (
    b.signalDate.localeCompare(a.signalDate) ||
    (b.filedDate ?? "").localeCompare(a.filedDate ?? "") ||
    b.score - a.score ||
    b.id - a.id
  );
}

/**
 * Cluster signals lead, ranked by how much the cluster's shape supports it (a tight window and
 * an executive participant are the two flags that separated winners from losers
 * in the backtest), then everything else by score.
 */
function clusterRank(s: Signal): number {
  if (s.signalType !== "CLUSTER_BUY") return 10;
  const tight = !!s.evidence.cluster?.tight_cluster;
  const exec = !!s.evidence.cluster?.executive_cluster;
  if (tight && exec) return 0;
  if (tight) return 1;
  if (exec) return 2;
  return 3;
}

export function byConviction(a: Signal, b: Signal): number {
  return clusterRank(a) - clusterRank(b) || b.score - a.score || byDate(a, b);
}
