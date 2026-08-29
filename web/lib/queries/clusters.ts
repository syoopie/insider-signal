import { unstable_cache } from "next/cache";
import { query } from "@/lib/db";
import { capTier, parseEvidence, type CapTier, type Cluster, type SignalType } from "@/lib/types";

/**
 * Cluster reads.
 *
 * Clusters are read from the `signals` table, never recomputed here. `cluster.py`
 * applies six eligibility filters (direct-only, $25k floor, identical-block and
 * same-price-offering exclusions among them) and the repo already carries one
 * copy of that logic in `backfill_signals.py` that has to be kept in sync. A
 * third copy in TypeScript would be a third thing to drift.
 */

export type ClusterSignal = {
  id: number;
  ticker: string;
  companyName: string;
  signalDate: string;
  score: number;
  signalType: SignalType;
  capTier: CapTier;
  cluster: Cluster;
  insiderCount: number;
  tight: boolean;
  executive: boolean;
  totalValue: number | null;
  /** Distinct roles among the cluster participants, for the summary line. */
  roles: string[];
};

export const getClusters = unstable_cache(
  async (days: number): Promise<ClusterSignal[]> => {
    const rows = await query<{
      id: number;
      ticker: string;
      signal_date: string;
      score: number;
      signal_type: string;
      evidence: unknown;
      company_name: string | null;
      cap_tier: string | null;
    }>(
      `
        SELECT
          s.id, s.ticker, s.signal_date::text AS signal_date, s.score, s.signal_type, s.evidence,
          c.name AS company_name,
          COALESCE(c.cap_tier, s.evidence->>'cap_tier', 'unknown') AS cap_tier
        FROM signals s
        LEFT JOIN LATERAL (
          SELECT co.name, co.cap_tier FROM companies co
          WHERE co.ticker = s.ticker
          ORDER BY co.market_cap DESC NULLS LAST, co.cik
          LIMIT 1
        ) c ON TRUE
        WHERE s.cluster_flag = TRUE
          AND s.signal_date >= (CURRENT_DATE - ($1::int - 1))
        ORDER BY s.signal_date DESC, s.score DESC
      `,
      [days],
    );

    return rows
      .map((r) => {
        const evidence = parseEvidence(r.evidence);
        const cluster = evidence.cluster ?? {};
        const participants = cluster.insiders ?? [];
        const values = participants
          .map((p) => p.total_value)
          .filter((v): v is number => v != null);

        return {
          id: Number(r.id),
          ticker: r.ticker,
          companyName: r.company_name ?? evidence.company_name ?? r.ticker,
          signalDate: r.signal_date,
          score: Number(r.score),
          signalType: r.signal_type as SignalType,
          capTier: capTier(r.cap_tier),
          cluster,
          insiderCount: cluster.insider_count ?? participants.length,
          tight: !!cluster.tight_cluster,
          executive: !!cluster.executive_cluster,
          totalValue: values.length > 0 ? values.reduce((a, b) => a + b, 0) : null,
          roles: [
            ...new Set(participants.map((p) => p.role_category).filter((r): r is string => !!r)),
          ].sort(),
        };
      })
      .filter((c) => c.insiderCount > 0);
  },
  ["clusters"],
  { tags: ["signals"], revalidate: 900 },
);
