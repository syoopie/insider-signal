/**
 * Metadata for the keys the Python model writes into `signals.score_breakdown`,
 * and labels for the facts it records on each buyer in `evidence.insiders[]`.
 *
 * The breakdown holds only what moved the score: `discount_rank`, the
 * percentile of how far below its 52-week high the stock sat on the day the
 * insider bought, or `price_context_missing` when there was no year of history
 * to measure against. Role, ownership form and buying history are facts about
 * the buyer, recorded and never scored, because measured out of sample none of
 * them ranked purchases better than chance.
 *
 * Source of truth is `src/signals/scorer.py` and `docs/findings.md`.
 */
export type ScoringFactor = {
  label: string;
  /** Why this key is on the signal, in one sentence. */
  reason: string;
  /** The empirical basis, short. */
  research?: string;
};

export const SCORING_FACTORS = {
  discount_rank: {
    label: "Discount to 52-week high",
    reason:
      "How far below its 52-week high the stock sat on the day the insider bought, " +
      "as a percentile. This is the score.",
    research:
      "Top decile: +11.13pp above same-month, same-volatility peers, median +7.39pp, " +
      "over 18 months out of sample. The same screen without an insider buying has a " +
      "median of −1.30pp.",
  },
  price_context_missing: {
    label: "No price history",
    reason:
      "The stock has under a year of trading history, so it has no 52-week high to " +
      "measure against. Scored zero and never alerted rather than guessed at.",
  },
} satisfies Record<string, ScoringFactor>;

/** Mirrors the TIMING_* constants in `src/signals/scorer.py`. */
export const TIMING_LABELS: Record<string, string> = {
  sequenced_30d: "Bought again within 30 days",
  prior_31_365d: "Also bought earlier this year",
  first_12mo: "First purchase in 12 months",
  unverifiable: "Purchase history not observable",
};

export const ROLE_LABELS: Record<string, string> = {
  director: "Director",
  cfo: "CFO",
  coo: "COO",
  officer: "Officer",
  chairman: "Chairman",
  ceo: "CEO",
  other: "Other",
};

export const CAP_LABELS: Record<string, { label: string; reason: string }> = {
  small: { label: "Small-cap (<$2B)", reason: "Market cap under $2B." },
  mid: { label: "Mid-cap ($2B–$10B)", reason: "Market cap between $2B and $10B." },
  large: {
    label: "Large-cap (>$10B)",
    reason: "Market cap above $10B. A large-cap cluster is shown as WATCH, not CLUSTER_BUY.",
  },
  unknown: { label: "Cap tier unknown", reason: "Shares outstanding could not be resolved." },
};
