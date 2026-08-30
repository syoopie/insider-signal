"use client";

import { useMemo, useState } from "react";
import { SignalTypeBadge } from "@/components/badges";
import { FilterChip, FilterGroup } from "@/components/filter-chip";
import { DEEP_DISCOUNT_PCT, DISCOUNT_EVIDENCE, discountScore } from "@/lib/discount";
import { factorMeta } from "@/lib/scoring-factors";
import { THRESHOLDS, classifySignal } from "@/lib/scoring-model";
import { cn } from "@/lib/utils";

/**
 * Move the price and watch the model score it.
 *
 * The old version of this let you build a score out of role, company size and
 * buying history, because that is what the model used to add up. It does not any
 * more, and a control that changes nothing would be worse than no control. Those
 * attributes are still here, below the score, labelled as what they are: things
 * the filing says that the model does not rank on.
 */

const ROLES = ["director", "cfo", "coo", "officer", "chairman", "ceo", "other"] as const;
const CAPS = ["small", "mid", "large", "unknown"] as const;

export function ScoringExplainer() {
  const [discount, setDiscount] = useState(DEEP_DISCOUNT_PCT);
  const [role, setRole] = useState<(typeof ROLES)[number]>("director");
  const [cap, setCap] = useState<(typeof CAPS)[number]>("small");
  const [isCluster, setIsCluster] = useState(false);
  const [tight, setTight] = useState(false);

  const { score, type } = useMemo(() => {
    const total = discountScore(discount);
    return {
      score: total,
      type: classifySignal({
        score: total,
        isCluster,
        clusterAvg: total,
        tightCluster: tight,
        capTier: cap,
      }),
    };
  }, [discount, isCluster, tight, cap]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-5 rounded-xl border bg-card p-5">
        <div className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <label htmlFor="discount" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              How far below its 52-week high, on the day they bought
            </label>
            <span className="font-mono text-lg tabular-nums">{discount.toFixed(1)}%</span>
          </div>
          <input
            id="discount"
            type="range"
            min={0}
            max={99}
            step={0.5}
            value={discount}
            onChange={(event) => setDiscount(Number(event.target.value))}
            className="w-full accent-[var(--color-primary,currentColor)]"
          />
          <p className="text-xs text-muted-foreground text-pretty">
            This is the model. The score is the percentile of that discount among the
            purchases insiders disclosed in the preceding 30 days, so the same number
            means more in a calm market than in a drawdown. This slider uses a fixed
            reference drawn from two years of filings, so treat it as a typical market.
            Everything below is recorded on the signal and scores nothing, because
            measured out of sample none of it ranked.
          </p>
        </div>

        <FilterGroup label="Who bought (not scored)">
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

        <FilterGroup label="Company size (not scored)">
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

        <dl className="space-y-1 border-t pt-3 text-sm">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="truncate text-muted-foreground">Discount to 52-week high</dt>
            <dd className="font-mono tabular-nums">{discount.toFixed(1)}%</dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="truncate text-muted-foreground">Its percentile</dt>
            <dd className={cn("font-mono tabular-nums", score >= THRESHOLDS.buy && "text-success")}>
              {score}
            </dd>
          </div>
        </dl>

        <p className="border-t pt-3 text-xs text-muted-foreground text-pretty">
          {explain(type, score, isCluster)}
        </p>
      </div>
    </div>
  );
}

function explain(type: string, score: number, isCluster: boolean): string {
  const { alpha, median, hitRate, placeboMedian, placeboHitRate, months } = DISCOUNT_EVIDENCE;

  if (type === "CLUSTER_BUY") {
    return `Three or more insiders bought inside a fortnight, and the group as a whole was buying real weakness — the cluster average clears ${THRESHOLDS.clusterAvg}. Cluster size alone no longer promotes a signal, because inside the most discounted purchases the number of buyers points the wrong way.`;
  }
  if (type === "BUY") {
    return `At or above ${THRESHOLDS.buy} the purchase is in the top decile of discount, which is where the entire measured effect sits: +${alpha}pp above same-month, same-volatility peers, median +${median}pp, hitting ${hitRate}% of the time over ${months} months. Deciles one through nine are flat with a negative median in every one.`;
  }
  if (isCluster) {
    return `The cluster is detected and shown, but the group was not buying weakness — the average is under ${THRESHOLDS.clusterAvg}. Surfaced as WATCH, never alerted on.`;
  }
  if (type === "WATCH") {
    return `Between ${THRESHOLDS.watch} and ${THRESHOLDS.buy - 1} the stock is cheap relative to its own year but not in the decile that carries the effect. Worth seeing, not alerted on.`;
  }
  return `Under ${THRESHOLDS.watch} the stock was near its 52-week high when the insider bought. A deeply discounted stock nobody bought has a median of ${placeboMedian}pp and hits ${placeboHitRate}% of the time; one near its high, bought or not, is not where the evidence points.`;
}
