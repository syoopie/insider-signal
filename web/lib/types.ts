import { z } from "zod";

/**
 * The pipeline writes `evidence` and `score_breakdown` as free-form JSONB. These
 * schemas are the trust boundary: parse once at the query layer, hand typed data
 * to components. `.catch`/`.optional` everywhere because old rows predate newer
 * fields and the app must still render them.
 */

export const SIGNAL_TYPES = ["BUY", "CLUSTER_BUY", "WATCH", "LOW"] as const;
export type SignalType = (typeof SIGNAL_TYPES)[number];

export const CAP_TIERS = ["small", "mid", "large", "unknown"] as const;
export type CapTier = (typeof CAP_TIERS)[number];

export const capTier = (v: unknown): CapTier =>
  CAP_TIERS.includes(v as CapTier) ? (v as CapTier) : "unknown";

/** One insider as aggregated for the signal (may combine several transactions). */
export const insiderSchema = z.object({
  name: z.string(),
  role: z.string().optional().nullable(),
  role_raw: z.string().optional().nullable(),
  price: z.number().optional().nullable(),
  total_value: z.number().optional().nullable(),
  pct_increase: z.number().optional().nullable(),
  shares_after: z.number().optional().nullable(),
  shares_bought: z.number().optional().nullable(),
  purchase_count: z.number().optional().nullable(),
  transaction_date: z.string().optional().nullable(),
  is_10b51: z.boolean().optional().nullable(),
  in_scoring_window: z.boolean().optional().nullable(),
});
export type Insider = z.infer<typeof insiderSchema>;

/** One raw transaction inside the cluster window. */
export const clusterTxnSchema = z.object({
  shares: z.number().optional().nullable(),
  is_direct: z.boolean().optional().nullable(),
  total_value: z.number().optional().nullable(),
  insider_name: z.string().optional().nullable(),
  role_category: z.string().optional().nullable(),
  price_per_share: z.number().optional().nullable(),
  transaction_date: z.string().optional().nullable(),
});
export type ClusterTxn = z.infer<typeof clusterTxnSchema>;

export const clusterSchema = z.object({
  is_cluster: z.boolean().optional().nullable(),
  insider_count: z.number().optional().nullable(),
  tight_cluster: z.boolean().optional().nullable(),
  executive_cluster: z.boolean().optional().nullable(),
  window_start: z.string().optional().nullable(),
  window_end: z.string().optional().nullable(),
  insiders: z.array(clusterTxnSchema).optional().nullable(),
});
export type Cluster = z.infer<typeof clusterSchema>;

export const evidenceSchema = z.object({
  company_name: z.string().optional().nullable(),
  cap_tier: z.string().optional().nullable(),
  market_cap: z.number().optional().nullable(),
  filed_date: z.string().optional().nullable(),
  signal_date: z.string().optional().nullable(),
  current_price: z.number().optional().nullable(),
  near_52wk_low: z.boolean().optional().nullable(),
  pct_above_52wk_low: z.number().optional().nullable(),
  price_52wk_low: z.number().optional().nullable(),
  insiders: z.array(insiderSchema).optional().nullable(),
  cluster: clusterSchema.optional().nullable(),
  research_basis: z.array(z.string()).optional().nullable(),
});
export type Evidence = z.infer<typeof evidenceSchema>;

export const scoreBreakdownSchema = z.record(z.string(), z.number());
export type ScoreBreakdown = z.infer<typeof scoreBreakdownSchema>;

/** Tolerant parse: bad JSON returns an empty object rather than throwing. */
export function parseEvidence(raw: unknown): Evidence {
  const obj = typeof raw === "string" ? safeJson(raw) : raw;
  const parsed = evidenceSchema.safeParse(obj ?? {});
  return parsed.success ? parsed.data : {};
}

export function parseScoreBreakdown(raw: unknown): ScoreBreakdown {
  const obj = typeof raw === "string" ? safeJson(raw) : raw;
  const parsed = scoreBreakdownSchema.safeParse(obj ?? {});
  return parsed.success ? parsed.data : {};
}

function safeJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

// ── Backtest ────────────────────────────────────────────────────────────────

export const bucketSchema = z.object({
  n: z.number().optional().nullable(),
  hit_rate: z.number().optional().nullable(),
  avg_return: z.number().optional().nullable(),
  median_return: z.number().optional().nullable(),
  p25_return: z.number().optional().nullable(),
  p75_return: z.number().optional().nullable(),
  max_gain: z.number().optional().nullable(),
  max_loss: z.number().optional().nullable(),
});
export type Bucket = z.infer<typeof bucketSchema>;

export const detailRowSchema = z.object({
  ticker: z.string(),
  signal_type: z.string(),
  score: z.number(),
  cap_tier: z.string(),
  exec_date: z.string(),
  ticker_return: z.number(),
  spy_return: z.number(),
  excess_return: z.number(),
});
export type DetailRow = z.infer<typeof detailRowSchema>;

export const backtestMetricsSchema = z.object({
  distribution: z
    .object({
      p25: z.number().optional().nullable(),
      p75: z.number().optional().nullable(),
      median: z.number().optional().nullable(),
      max_gain: z.number().optional().nullable(),
      max_loss: z.number().optional().nullable(),
    })
    .optional()
    .nullable(),
  by_score_band: z.record(z.string(), bucketSchema.nullable()).optional().nullable(),
  by_cap_tier: z.record(z.string(), bucketSchema.nullable()).optional().nullable(),
  by_signal_type: z.record(z.string(), bucketSchema.nullable()).optional().nullable(),
  risk: z
    .object({
      pct_loss_gt20: z.number().optional().nullable(),
      worst_outcome: z.number().optional().nullable(),
      max_consecutive_losses: z.number().optional().nullable(),
      n_no_spy_data: z.number().optional().nullable(),
    })
    .optional()
    .nullable(),
  cluster_5064: bucketSchema.optional().nullable(),
  iwm_small_cap: bucketSchema.optional().nullable(),
  rolling_hit_rate_90d: z
    .array(z.object({ date: z.string(), hit_rate: z.number(), n: z.number() }))
    .optional()
    .nullable(),
  detail: z.array(detailRowSchema).optional().nullable(),
});
export type BacktestMetrics = z.infer<typeof backtestMetricsSchema>;

export function parseBacktestMetrics(raw: unknown): BacktestMetrics {
  const obj = typeof raw === "string" ? safeJson(raw) : raw;
  const parsed = backtestMetricsSchema.safeParse(obj ?? {});
  return parsed.success ? parsed.data : {};
}
