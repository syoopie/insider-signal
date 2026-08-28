import { Layers, Eye, CircleCheck, CircleDashed } from "lucide-react";
import type { SignalType, CapTier } from "@/lib/types";
import { cn } from "@/lib/utils";

const base =
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap";

// ── Signal type ─────────────────────────────────────────────────────────────

const SIGNAL_META: Record<
  SignalType,
  { label: string; className: string; icon: typeof Layers }
> = {
  CLUSTER_BUY: {
    label: "Cluster Buy",
    className: "border-cluster/30 bg-cluster/10 text-cluster",
    icon: Layers,
  },
  BUY: {
    label: "Buy",
    className: "border-buy/30 bg-buy/10 text-buy",
    icon: CircleCheck,
  },
  WATCH: {
    label: "Watch",
    className: "border-watch/30 bg-watch/10 text-watch",
    icon: Eye,
  },
  LOW: {
    label: "Low",
    className: "border-border bg-muted text-muted-foreground",
    icon: CircleDashed,
  },
};

export function SignalTypeBadge({
  type,
  className,
  showIcon = true,
}: {
  type: SignalType;
  className?: string;
  showIcon?: boolean;
}) {
  const meta = SIGNAL_META[type] ?? SIGNAL_META.LOW;
  const Icon = meta.icon;
  return (
    <span className={cn(base, meta.className, className)}>
      {showIcon && <Icon className="size-3" aria-hidden />}
      {meta.label}
    </span>
  );
}

// ── Conviction (derived, for cluster/high-score signals) ─────────────────────

export type Conviction = "PRIME" | "STRONG" | "CLUSTER" | "HIGH" | "BUY";

const CONVICTION_META: Record<Conviction, { label: string; className: string }> = {
  PRIME: { label: "Prime", className: "border-cluster/40 bg-cluster/15 text-cluster" },
  STRONG: { label: "Strong", className: "border-cluster/30 bg-cluster/10 text-cluster" },
  CLUSTER: { label: "Cluster", className: "border-cluster/20 bg-cluster/5 text-cluster" },
  HIGH: { label: "High conviction", className: "border-buy/30 bg-buy/10 text-buy" },
  BUY: { label: "Buy", className: "border-buy/20 bg-buy/5 text-buy" },
};

export function ConvictionBadge({ conviction, className }: { conviction: Conviction; className?: string }) {
  const meta = CONVICTION_META[conviction] ?? CONVICTION_META.BUY;
  return <span className={cn(base, meta.className, "uppercase tracking-wide", className)}>{meta.label}</span>;
}

/** Mirrors `_conviction()` in the Streamlit app. */
export function convictionFor(
  signalType: SignalType,
  score: number,
  cluster: { tight_cluster?: boolean | null; executive_cluster?: boolean | null } | null | undefined,
): Conviction {
  if (signalType === "CLUSTER_BUY") {
    const tight = !!cluster?.tight_cluster;
    const exec = !!cluster?.executive_cluster;
    if (tight && exec) return "PRIME";
    if (tight || exec) return "STRONG";
    return "CLUSTER";
  }
  return score >= 70 ? "HIGH" : "BUY";
}

// ── Cap tier ────────────────────────────────────────────────────────────────

const CAP_META: Record<CapTier, { label: string; className: string }> = {
  small: { label: "Small-cap", className: "border-chart-2/30 bg-chart-2/10 text-chart-2" },
  mid: { label: "Mid-cap", className: "border-border bg-muted text-muted-foreground" },
  large: { label: "Large-cap", className: "border-border bg-muted text-muted-foreground" },
  unknown: { label: "Cap unknown", className: "border-border bg-muted text-muted-foreground" },
};

export function CapTierBadge({ tier, className }: { tier: CapTier; className?: string }) {
  const meta = CAP_META[tier] ?? CAP_META.unknown;
  return <span className={cn(base, meta.className, className)}>{meta.label}</span>;
}
