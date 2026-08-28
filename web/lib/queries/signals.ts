import { unstable_cache } from "next/cache";
import { query } from "@/lib/db";
import {
  capTier,
  parseEvidence,
  parseScoreBreakdown,
  type CapTier,
  type Evidence,
  type ScoreBreakdown,
  type SignalType,
} from "@/lib/types";
import type { SignalFilters } from "@/lib/signal-filters";

/**
 * Signal triage reads. Ports the Streamlit `tab_signals` query with two
 * corrections:
 *
 * 1. The join to `companies` is a LATERAL pick-one. `companies` is keyed by CIK,
 *    so a plain `ON c.ticker = s.ticker` duplicates a signal row whenever two
 *    CIKs share a ticker (a re-incorporation, a delisted predecessor).
 * 2. The cap-tier filter runs in SQL against the same expression the UI shows,
 *    so what is filtered and what is displayed can never disagree.
 */

/** One signal, with its JSONB blobs already parsed and the header figures derived. */
export type Signal = {
  id: number;
  ticker: string;
  signalDate: string;
  score: number;
  signalType: SignalType;
  clusterFlag: boolean;
  capTier: CapTier;
  companyName: string;
  marketCap: number | null;
  evidence: Evidence;
  breakdown: ScoreBreakdown;
  /** Insiders credited on this signal (cluster participants, or the single buyer). */
  insiderCount: number;
  /** Sum of `evidence.insiders[].total_value`. Null when no insider detail was stored. */
  totalValue: number | null;
  /** `evidence.filed_date` — the EDGAR receipt date the signal is derived from. */
  filedDate: string | null;
};

type SignalRow = {
  id: number;
  ticker: string;
  signal_date: string;
  score: number;
  signal_type: string;
  cluster_flag: boolean;
  cap_tier: string | null;
  company_name: string | null;
  market_cap: string | number | null;
  evidence: unknown;
  score_breakdown: unknown;
};

/**
 * A generous ceiling rather than a page size. The whole signals table is ~2.5k
 * rows over two years, so even the widest filter fits comfortably; the cap only
 * exists so a pathological query can't stream unbounded JSONB.
 */
const ROW_LIMIT = 1000;

const SIGNALS_SQL = `
  SELECT
    s.id,
    s.ticker,
    s.signal_date::text                                            AS signal_date,
    s.score,
    s.signal_type,
    s.cluster_flag,
    s.evidence,
    s.score_breakdown,
    c.name                                                         AS company_name,
    c.market_cap,
    COALESCE(c.cap_tier, s.evidence->>'cap_tier', 'unknown')       AS cap_tier
  FROM signals s
  LEFT JOIN LATERAL (
    SELECT co.name, co.cap_tier, co.market_cap
    FROM companies co
    WHERE co.ticker = s.ticker
    ORDER BY co.market_cap DESC NULLS LAST, co.cik
    LIMIT 1
  ) c ON TRUE
  WHERE s.signal_date >= (CURRENT_DATE - ($1::int - 1))
    AND s.score >= $2::int
    AND s.signal_type = ANY($3::text[])
    AND COALESCE(c.cap_tier, s.evidence->>'cap_tier', 'unknown') = ANY($4::text[])
  ORDER BY s.score DESC, s.signal_date DESC, s.id DESC
  LIMIT ${ROW_LIMIT}
`;

/**
 * Cached on the four bounded filters only, so the cache key space stays small
 * and predictable. The day pin is applied in memory by `applyDayFilter`; free
 * text never reaches here at all (see `matchesQuery`).
 */
export const getSignals = unstable_cache(
  async (days: number, minScore: number, types: string[], caps: string[]): Promise<Signal[]> => {
    const rows = await query<SignalRow>(SIGNALS_SQL, [days, minScore, types, caps]);
    return rows.map(toSignal).sort(compareSignals);
  },
  ["signals-list"],
  { tags: ["signals"], revalidate: 900 },
);

function toSignal(row: SignalRow): Signal {
  const evidence = parseEvidence(row.evidence);
  const insiders = evidence.insiders ?? [];
  const values = insiders.map((i) => i.total_value).filter((v): v is number => v != null);

  return {
    id: Number(row.id),
    ticker: row.ticker,
    signalDate: row.signal_date,
    score: Number(row.score),
    signalType: (row.signal_type as SignalType) ?? "LOW",
    clusterFlag: !!row.cluster_flag,
    capTier: capTier(row.cap_tier),
    companyName: row.company_name ?? evidence.company_name ?? row.ticker,
    marketCap: row.market_cap == null ? null : Number(row.market_cap),
    evidence,
    breakdown: parseScoreBreakdown(row.score_breakdown),
    insiderCount: evidence.cluster?.insider_count ?? insiders.length,
    totalValue: values.length > 0 ? values.reduce((a, b) => a + b, 0) : null,
    filedDate: evidence.filed_date ?? null,
  };
}

/**
 * Quality ordering, mirroring `_qkey()` in the Streamlit app: cluster signals
 * lead, ranked by how much the cluster's shape supports it (a tight window and
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

function compareSignals(a: Signal, b: Signal): number {
  return (
    clusterRank(a) - clusterRank(b) ||
    b.score - a.score ||
    b.signalDate.localeCompare(a.signalDate) ||
    b.id - a.id
  );
}

/**
 * The day pin, applied in memory rather than in SQL so clicking a day in the
 * calendar reuses the cached window instead of issuing a new query.
 */
export function applyDayFilter(signals: Signal[], filters: SignalFilters): Signal[] {
  if (!filters.day) return signals;
  return signals.filter((s) => s.signalDate === filters.day);
}

// ── Calendar ────────────────────────────────────────────────────────────────

export type SignalDay = {
  date: string;
  total: number;
  clusterBuy: number;
  buy: number;
  watch: number;
};

/**
 * Signals per day across the lookback window, for the density strip.
 *
 * Deliberately answers only to the window — not to the score/type/cap filters —
 * so it stays an orientation device: it shows what the pipeline produced, which
 * is the context you need to judge whether a filter is hiding something.
 */
export const getSignalCalendar = unstable_cache(
  async (days: number): Promise<SignalDay[]> => {
    const rows = await query<{
      d: string;
      total: string | number;
      cluster_buy: string | number;
      buy: string | number;
      watch: string | number;
    }>(
      `
        SELECT
          signal_date::text                                        AS d,
          COUNT(*)                                                 AS total,
          COUNT(*) FILTER (WHERE signal_type = 'CLUSTER_BUY')      AS cluster_buy,
          COUNT(*) FILTER (WHERE signal_type = 'BUY')              AS buy,
          COUNT(*) FILTER (WHERE signal_type = 'WATCH')            AS watch
        FROM signals
        WHERE signal_date >= (CURRENT_DATE - ($1::int - 1))
          AND signal_type <> 'LOW'
        GROUP BY 1
        ORDER BY 1
      `,
      [days],
    );

    return rows.map((r) => ({
      date: r.d,
      total: Number(r.total),
      clusterBuy: Number(r.cluster_buy),
      buy: Number(r.buy),
      watch: Number(r.watch),
    }));
  },
  ["signal-calendar"],
  { tags: ["signals"], revalidate: 900 },
);
