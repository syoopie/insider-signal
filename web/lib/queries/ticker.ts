import { unstable_cache } from "next/cache";
import { query, queryOne } from "@/lib/db";
import { capTier, type CapTier, type SignalType } from "@/lib/types";

/**
 * Per-ticker research reads. Ports the Streamlit `tab_history` queries.
 *
 * Everything here keys off `companies.ticker`. That column is not unique — a CIK
 * can be superseded — so the company lookup picks one deterministically and the
 * transaction query joins through every matching CIK, which is what a reader
 * researching "ACME" actually wants to see.
 */

export type TickerCompany = {
  ticker: string;
  name: string;
  ciks: string[];
  capTier: CapTier;
  marketCap: number | null;
  sicCode: string | null;
};

export type TickerTransaction = {
  id: number;
  insiderName: string;
  roleCategory: string | null;
  insiderRole: string | null;
  transactionDate: string;
  transactionCode: string;
  shares: number | null;
  pricePerShare: number | null;
  totalValue: number | null;
  is10b51: boolean | null;
  isDirect: boolean | null;
  isRoutine: boolean | null;
  filedDate: string | null;
};

export type TickerSignal = {
  id: number;
  signalDate: string;
  score: number;
  signalType: SignalType;
  clusterFlag: boolean;
};

export const getTickerCompany = unstable_cache(
  async (ticker: string): Promise<TickerCompany | null> => {
    const row = await queryOne<{
      ticker: string;
      name: string | null;
      ciks: string[];
      cap_tier: string | null;
      market_cap: string | number | null;
      sic_code: string | null;
    }>(
      `
        SELECT
          ticker,
          (ARRAY_AGG(name       ORDER BY market_cap DESC NULLS LAST, cik))[1] AS name,
          (ARRAY_AGG(cap_tier   ORDER BY market_cap DESC NULLS LAST, cik))[1] AS cap_tier,
          (ARRAY_AGG(sic_code   ORDER BY market_cap DESC NULLS LAST, cik))[1] AS sic_code,
          MAX(market_cap)                                                     AS market_cap,
          ARRAY_AGG(cik ORDER BY cik)                                         AS ciks
        FROM companies
        WHERE ticker = $1
        GROUP BY ticker
      `,
      [ticker],
    );

    if (!row) return null;
    return {
      ticker: row.ticker,
      name: row.name ?? row.ticker,
      ciks: row.ciks ?? [],
      capTier: capTier(row.cap_tier),
      marketCap: row.market_cap == null ? null : Number(row.market_cap),
      sicCode: row.sic_code,
    };
  },
  ["ticker-company"],
  { tags: ["pipeline"], revalidate: 900 },
);

/** Every stored transaction for the ticker, newest first. */
export const getTickerTransactions = unstable_cache(
  async (ticker: string, limit = 250): Promise<TickerTransaction[]> => {
    const rows = await query<{
      id: number;
      insider_name: string;
      role_category: string | null;
      insider_role: string | null;
      transaction_date: string;
      transaction_code: string;
      shares: string | null;
      price_per_share: string | null;
      total_value: string | null;
      is_10b51: boolean | null;
      is_direct: boolean | null;
      is_routine: boolean | null;
      filed_date: string | null;
    }>(
      `
        SELECT
          t.id, t.insider_name, t.role_category, t.insider_role,
          t.transaction_date::text AS transaction_date,
          t.transaction_code, t.shares, t.price_per_share, t.total_value,
          t.is_10b51, t.is_direct, t.is_routine,
          f.filed_date::text AS filed_date
        FROM transactions t
        JOIN form4_filings f ON f.id = t.filing_id
        JOIN companies c     ON c.cik = f.cik
        WHERE c.ticker = $1
        ORDER BY t.transaction_date DESC, t.id DESC
        LIMIT $2::int
      `,
      [ticker, limit],
    );

    return rows.map((r) => ({
      id: Number(r.id),
      insiderName: r.insider_name,
      roleCategory: r.role_category,
      insiderRole: r.insider_role,
      transactionDate: r.transaction_date,
      transactionCode: r.transaction_code,
      shares: numOrNull(r.shares),
      pricePerShare: numOrNull(r.price_per_share),
      totalValue: numOrNull(r.total_value),
      is10b51: r.is_10b51,
      isDirect: r.is_direct,
      isRoutine: r.is_routine,
      filedDate: r.filed_date,
    }));
  },
  ["ticker-transactions"],
  { tags: ["pipeline"], revalidate: 900 },
);

export const getTickerSignals = unstable_cache(
  async (ticker: string, limit = 40): Promise<TickerSignal[]> => {
    const rows = await query<{
      id: number;
      signal_date: string;
      score: number;
      signal_type: string;
      cluster_flag: boolean;
    }>(
      `
        SELECT id, signal_date::text AS signal_date, score, signal_type, cluster_flag
        FROM signals
        WHERE ticker = $1
        ORDER BY signal_date DESC
        LIMIT $2::int
      `,
      [ticker, limit],
    );
    return rows.map((r) => ({
      id: Number(r.id),
      signalDate: r.signal_date,
      score: Number(r.score),
      signalType: r.signal_type as SignalType,
      clusterFlag: !!r.cluster_flag,
    }));
  },
  ["ticker-signals"],
  { tags: ["signals"], revalidate: 900 },
);

/** Tickers that have any stored activity, for the search box. */
export const getAllTickers = unstable_cache(
  async (): Promise<{ ticker: string; name: string; signals: number }[]> => {
    const rows = await query<{ ticker: string; name: string | null; signals: string | number }>(`
      SELECT
        c.ticker,
        (ARRAY_AGG(c.name ORDER BY c.market_cap DESC NULLS LAST, c.cik))[1] AS name,
        (SELECT COUNT(*) FROM signals s WHERE s.ticker = c.ticker)          AS signals
      FROM companies c
      WHERE c.ticker IS NOT NULL AND c.ticker <> ''
      GROUP BY c.ticker
      ORDER BY c.ticker
    `);
    return rows.map((r) => ({
      ticker: r.ticker,
      name: r.name ?? r.ticker,
      signals: Number(r.signals),
    }));
  },
  ["all-tickers"],
  { tags: ["pipeline"], revalidate: 900 },
);

function numOrNull(v: string | number | null | undefined): number | null {
  return v === null || v === undefined || v === "" ? null : Number(v);
}

