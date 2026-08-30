/**
 * Metadata for every key the Python model can emit into `signals.score_breakdown`.
 *
 * One key carries the score. `discount_rank` is the percentile of how far below
 * its 52-week high the stock sat on the day the insider bought, and it is the
 * whole model. Everything else in this table is `descriptive`: it says something
 * true about the filing and contributes zero points.
 *
 * That is not an oversight. Measured walk-forward across 18 months and 6,690
 * out-of-sample purchases, the old additive factor table returned +0.78
 * percentage points of selection alpha with a permutation p-value of 0.27,
 * which is a coin flip. The discount returns +11.13pp with a median of +7.39pp,
 * above all 5,000 random rankings. Adding the old factors back as a tiebreak
 * was measured too, and drops the result to +7.62.
 *
 * Source of truth is `src/signals/discount.py` and
 * `docs/scoring-improvement-plan.md` section 7b.
 */
export type ScoringFactor = {
  label: string;
  /** Points this factor contributes. Must match `src/signals/scorer.py`. */
  points: number;
  /** Why this factor moves the score, in one sentence. */
  reason: string;
  /** The empirical basis, short. */
  research?: string;
  group: "rank" | "role" | "size" | "conviction" | "timing" | "penalty" | "descriptive";
};

/** The keys that actually move the score. Everything else is context. */
export const SCORING_KEYS = ["discount_rank"] as const;

const descriptive = (label: string, reason: string): ScoringFactor => ({
  label,
  points: 0,
  reason,
  group: "descriptive",
});

export const SCORING_FACTORS: Record<string, ScoringFactor> = {
  discount_rank: {
    label: "Discount to 52-week high",
    points: 100,
    reason:
      "How far below its 52-week high the stock sat on the day the insider bought, " +
      "as a percentile. This is the score.",
    research:
      "Top decile: +11.13pp above same-month, same-volatility peers, median +7.39pp, " +
      "over 18 months out of sample. The same screen without an insider buying has a " +
      "median of −1.30pp.",
    group: "rank",
  },
  price_context_missing: {
    label: "No price history",
    points: 0,
    reason:
      "The stock has under a year of trading history, so it has no 52-week high to " +
      "measure against. Scored zero and never alerted rather than guessed at.",
    group: "descriptive",
  },

  role_cfo: descriptive("CFO purchase", "The CFO filed this purchase."),
  role_director: descriptive("Director purchase", "A board member filed this purchase."),
  role_coo: descriptive("COO purchase", "The COO filed this purchase."),
  role_officer: descriptive("Officer purchase", "A named officer filed this purchase."),
  role_chairman: descriptive("Chairman purchase", "The chairman filed this purchase."),
  role_ceo: descriptive("CEO purchase", "The CEO filed this purchase."),
  role_other: descriptive("Other role", "The filer's role did not classify."),

  cap_small: descriptive("Small-cap (<$2B)", "Market cap under $2B."),
  cap_mid: descriptive("Mid-cap ($2B–$10B)", "Market cap between $2B and $10B."),
  cap_large: descriptive("Large-cap (>$10B)", "Market cap above $10B."),
  cap_unknown: descriptive("Cap tier unknown", "Shares outstanding could not be resolved."),

  holdings_increase_5pct: descriptive(
    "Holdings up ≥5%",
    "The purchase added at least 5% to the position the insider already held.",
  ),
  indirect_purchase: descriptive(
    "Indirect purchase",
    "Bought through an LLC, trust, or family entity rather than a personal account.",
  ),
  prior_purchase_31_365d: descriptive(
    "Prior buy 31–365 days ago",
    "This insider also bought earlier in the year.",
  ),
  sequenced_buying_30d: descriptive(
    "Sequenced buying (≤30 days)",
    "This insider bought again within a month.",
  ),
  first_purchase_12mo: descriptive(
    "First purchase in 12 months",
    "No prior buy on record in the year before, and the database covers that year.",
  ),
  first_purchase_unverifiable: descriptive(
    "Purchase history not observable",
    "No prior buy on record, but the database does not reach back a full year before " +
      "this trade, so the absence is not evidence.",
  ),
};

export function factorMeta(key: string): ScoringFactor {
  return (
    SCORING_FACTORS[key] ?? {
      label: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      points: 0,
      reason: "Recorded on the filing.",
      group: "descriptive",
    }
  );
}

/** Whether a breakdown key moves the score or only describes the filing. */
export function isScoringKey(key: string): boolean {
  return (SCORING_KEYS as readonly string[]).includes(key);
}
