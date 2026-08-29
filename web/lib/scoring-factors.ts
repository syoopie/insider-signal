/**
 * Metadata for every scoring factor the Python model can emit, keyed by the
 * exact key that appears in `signals.score_breakdown`.
 *
 * Source of truth for the points is `src/signals/scorer.py`; this table mirrors
 * it for display (label, plain-English reason, research citation) so the UI can
 * explain any factor a signal shows. Keep in sync when scorer.py changes.
 */
export type ScoringFactor = {
  label: string;
  /** Points this factor contributes. Must match `src/signals/scorer.py`. */
  points: number;
  /** Why this factor moves the score, in one sentence. */
  reason: string;
  /** The empirical basis, short. */
  research?: string;
  group: "role" | "size" | "conviction" | "timing" | "price" | "penalty";
};

export const SCORING_FACTORS: Record<string, ScoringFactor> = {
  role_cfo: {
    label: "CFO purchase",
    points: 15,
    reason: "The CFO knows the numbers before anyone. +15.",
    research: "TipRanks: CFO buys average the highest annual return of any role (21.5%).",
    group: "role",
  },
  role_director: {
    label: "Director purchase",
    points: 16,
    reason: "Board-level view of the business. +16.",
    research: "TipRanks: director buys average 20.7% annual return.",
    group: "role",
  },
  role_coo: {
    label: "COO purchase",
    points: 15,
    reason: "Operational insight into demand and margins. +15.",
    group: "role",
  },
  role_officer: {
    label: "Officer purchase",
    points: 12,
    reason: "Named executive officer, direct knowledge of the unit. +12.",
    research: "+20.8% at 60 days in this system's own backtest (small sample).",
    group: "role",
  },
  role_chairman: {
    label: "Chairman purchase",
    points: 0,
    reason: "Neutral. Sample too small to weight either way. +0.",
    group: "role",
  },
  role_ceo: {
    label: "CEO purchase",
    points: -5,
    reason: "Counterintuitively the weakest role signal; often symbolic. −5.",
    research: "−17.3% at 60 days, −13.4% at 90 days in backtest.",
    group: "penalty",
  },
  role_other: {
    label: "Other role",
    points: 0,
    reason: "Not a scored role. +0.",
    group: "role",
  },
  cap_small: {
    label: "Small-cap (<$2B)",
    points: 15,
    reason: "Where insider information asymmetry pays the most. +15.",
    research: "Lakonishok & Lee (2001): +7.4% abnormal return at 12 months.",
    group: "size",
  },
  cap_mid: {
    label: "Mid-cap ($2B–$10B)",
    points: 0,
    reason: "Neutral. +0.",
    group: "size",
  },
  cap_large: {
    label: "Large-cap (>$10B)",
    points: 0,
    reason: "Near-zero alpha in the research. +0.",
    group: "size",
  },
  cap_unknown: {
    label: "Cap tier unknown",
    points: 5,
    reason: "Scored conservatively; some unknowns turn out to be large-caps. +5.",
    group: "size",
  },
  holdings_increase_5pct: {
    label: "Holdings up ≥5%",
    points: 15,
    reason: "A meaningful add to an existing position. +15.",
    research: "+9.2% at 60 days, +9.3% at 90 days in backtest.",
    group: "conviction",
  },
  indirect_purchase: {
    label: "Indirect purchase",
    points: -15,
    reason: "Bought through an LLC, trust, or family entity. Less conviction. −15.",
    research: "−18% at 60 days, −36% at 90 days empirically.",
    group: "penalty",
  },
  prior_purchase_31_365d: {
    label: "Prior buy 31–365 days ago",
    points: 15,
    reason: "Sustained conviction across quarters. +15.",
    research: "+2.4% at 60 days in backtest.",
    group: "timing",
  },
  sequenced_buying_30d: {
    label: "Sequenced buying (≤30 days)",
    points: 10,
    reason: "A rapid second purchase; the thesis is still developing. +10.",
    group: "timing",
  },
  first_purchase_12mo: {
    label: "First purchase in 12 months",
    points: -10,
    reason: "No prior buy in a year. Weaker than a sustained pattern. −10.",
    research: "−4.2% at 60 days in backtest.",
    group: "penalty",
  },
  near_52wk_low_5pct: {
    label: "Within 5% of 52-week low",
    points: 12,
    reason: "Buying into weakness signals real conviction. +12.",
    group: "price",
  },
  near_52wk_low_10pct: {
    label: "Within 10% of 52-week low",
    points: 7,
    reason: "Buying near the lows. +7.",
    group: "price",
  },
};

export function factorMeta(key: string): ScoringFactor {
  return (
    SCORING_FACTORS[key] ?? {
      label: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      points: 0,
      reason: "Contributes to the score.",
      group: "conviction",
    }
  );
}
