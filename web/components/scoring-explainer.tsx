"use client";

import { useMemo, useState } from "react";
import { SignalTypeBadge } from "@/components/badges";
import { FilterChip, FilterGroup } from "@/components/filter-chip";
import { SCORING_FACTORS, factorMeta } from "@/lib/scoring-factors";
import { THRESHOLDS, classifySignal } from "@/lib/scoring-model";
import { cn } from "@/lib/utils";

/**
 * Build a purchase and watch the model score it.
 *
 * A table of weights tells you what the factors are worth; it does not tell you
 * that a CEO buying a large-cap on a first purchase scores 0 while a director
 * buying a small-cap they have been adding to scores 61. Being able to reach the
 * BUY threshold by hand is the difference between a documented model and an
 * understood one.
 */

const ROLES = ["director", "cfo", "coo", "officer", "chairman", "ceo", "other"] as const;
const CAPS = ["small", "mid", "large", "unknown"] as const;
const TIMING = ["prior_purchase_31_365d", "sequenced_buying_30d", "first_purchase_12mo"] as const;
const PRICE = ["none", "near_52wk_low_5pct", "near_52wk_low_10pct"] as const;

export function ScoringExplainer() {
  const [role, setRole] = useState<(typeof ROLES)[number]>("director");
  const [cap, setCap] = useState<(typeof CAPS)[number]>("small");
  const [timing, setTiming] = useState<(typeof TIMING)[number]>("prior_purchase_31_365d");
  const [price, setPrice] = useState<(typeof PRICE)[number]>("none");
  const [holdings, setHoldings] = useState(true);
  const [indirect, setIndirect] = useState(false);
  const [isCluster, setIsCluster] = useState(false);
  const [tight, setTight] = useState(false);

  const { score, breakdown, type } = useMemo(() => {
    const keys = [`role_${role}`, `cap_${cap}`, timing];
    if (price !== "none") keys.push(price);
    if (holdings) keys.push("holdings_increase_5pct");
    if (indirect) keys.push("indirect_purchase");

    const entries = keys.map((k) => [k, SCORING_FACTORS[k]?.points ?? 0] as const);
    // scorer.py caps the total at 100 and floors it at 0.
    const raw = entries.reduce((sum, [, pts]) => sum + pts, 0);
    const total = Math.max(0, Math.min(100, raw));

    return {
      score: total,
      breakdown: entries.filter(([, pts]) => pts !== 0),
      type: classifySignal({
        score: total,
        isCluster,
        clusterAvg: total,
        tightCluster: tight,
        capTier: cap,
      }),
    };
  }, [role, cap, timing, price, holdings, indirect, isCluster, tight]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-5 rounded-xl border bg-card p-5">
        <FilterGroup label="Who bought">
          {ROLES.map((r) => (
            <FilterChip
              key={r}
              selected={role === r}
              onClick={() => setRole(r)}
              title={factorMeta(`role_${r}`).reason}
            >
              {factorMeta(`role_${r}`).label.replace(" purchase", "")}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Company size">
          {CAPS.map((c) => (
            <FilterChip
              key={c}
              selected={cap === c}
              onClick={() => setCap(c)}
              title={factorMeta(`cap_${c}`).reason}
            >
              {factorMeta(`cap_${c}`).label}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Buying history">
          {TIMING.map((t) => (
            <FilterChip key={t} selected={timing === t} onClick={() => setTiming(t)}>
              {factorMeta(t).label}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Price context">
          <FilterChip selected={price === "none"} onClick={() => setPrice("none")}>
            Not near the low
          </FilterChip>
          {PRICE.filter((p) => p !== "none").map((p) => (
            <FilterChip key={p} selected={price === p} onClick={() => setPrice(p)}>
              {factorMeta(p).label}
            </FilterChip>
          ))}
        </FilterGroup>

        <FilterGroup label="Other">
          <FilterChip selected={holdings} onClick={() => setHoldings((v) => !v)}>
            Grew their position ≥5%
          </FilterChip>
          <FilterChip selected={indirect} onClick={() => setIndirect((v) => !v)}>
            Bought through a trust or LLC
          </FilterChip>
        </FilterGroup>

        <FilterGroup label="Cluster">
          <FilterChip
            selected={isCluster}
            onClick={() => {
              setIsCluster((v) => !v);
              if (isCluster) setTight(false);
            }}
          >
            {THRESHOLDS.clusterMinInsiders}+ insiders in {THRESHOLDS.clusterWindowDays} days
          </FilterChip>
          <FilterChip
            selected={tight}
            onClick={() => {
              setTight((v) => !v);
              if (!tight) setIsCluster(true);
            }}
          >
            …within {THRESHOLDS.tightWindowDays} days
          </FilterChip>
        </FilterGroup>
      </div>

      <div className="space-y-4 rounded-xl border bg-card p-5">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Score</p>
          <p className="font-mono text-4xl font-semibold tabular-nums">
            {score}
            <span className="text-base font-normal text-muted-foreground">/100</span>
          </p>
          <div className="mt-2">
            <SignalTypeBadge type={type} />
          </div>
        </div>

        <ol className="space-y-1 border-t pt-3 text-sm">
          {breakdown.map(([key, pts]) => (
            <li key={key} className="flex items-baseline justify-between gap-3">
              <span className="truncate text-muted-foreground">{factorMeta(key).label}</span>
              <span
                className={cn(
                  "font-mono tabular-nums",
                  pts > 0 ? "text-success" : "text-destructive",
                )}
              >
                {pts > 0 ? "+" : ""}
                {pts}
              </span>
            </li>
          ))}
          {breakdown.length === 0 && (
            <li className="text-muted-foreground">Nothing this combination scores on.</li>
          )}
        </ol>

        <p className="border-t pt-3 text-xs text-muted-foreground text-pretty">
          {explain(type, score, isCluster, cap)}
        </p>
      </div>
    </div>
  );
}

function explain(type: string, score: number, isCluster: boolean, cap: string): string {
  if (isCluster && cap === "large") {
    return "Large-cap clusters are downgraded to WATCH whatever they score: over 90 days they hit 0% of the time and averaged −16% against SPY.";
  }
  if (type === "CLUSTER_BUY") {
    return `The cluster average clears ${THRESHOLDS.clusterAvg} and either the window is tight or one buyer scored at least ${THRESHOLDS.clusterMaxScore}. This is the strongest signal the model produces, and the only one besides BUY that triggers an alert.`;
  }
  if (type === "BUY") {
    return `At or above ${THRESHOLDS.buy} a lone purchase becomes a BUY. Reaching it takes three or four strong factors — there is deliberately no cheap route via dollar value, which backtested negative and was removed.`;
  }
  if (isCluster) {
    return `The cluster does not qualify: the average is under ${THRESHOLDS.clusterAvg}, or the window is loose and no single buyer reached ${THRESHOLDS.clusterMaxScore}. It is surfaced as WATCH but never alerted on.`;
  }
  if (type === "WATCH") {
    return `Between ${THRESHOLDS.watch} and ${THRESHOLDS.buy - 1} a signal is worth seeing but not acting on. WATCH signals hit around 35% of the time against 55%+ for BUY and CLUSTER_BUY.`;
  }
  return `Under ${THRESHOLDS.watch} the purchase is recorded but never surfaced as a signal.`;
}
