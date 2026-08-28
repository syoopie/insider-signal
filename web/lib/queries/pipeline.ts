import { unstable_cache } from "next/cache";
import { query, queryOne } from "@/lib/db";

/**
 * Pipeline health, for the always-on freshness bar.
 *
 * "Show clearly how the entire app functions for full transparency" starts here:
 * every number below is read straight from the tables the Python pipeline writes,
 * so the bar can never disagree with reality.
 */
export type PipelineStatus = {
  lastFilingFetchedAt: string | null; // MAX(form4_filings.fetched_at)
  lastFiledDate: string | null; // MAX(form4_filings.filed_date)
  lastBacktestRunDate: string | null; // MAX(backtest_runs.run_date)
  latestSignalDate: string | null; // MAX(signals.signal_date)
  counts: {
    filings: number;
    transactions: number;
    purchaseTransactions: number;
    signals: number;
    buySignals: number;
    clusterBuySignals: number;
    companies: number;
    companiesWithCap: number;
  };
  coverageStart: string | null; // MIN(form4_filings.filed_date)
};

const CACHE_TAGS = ["pipeline"];

export const getPipelineStatus = unstable_cache(
  async (): Promise<PipelineStatus> => {
    const [meta, counts] = await Promise.all([
      queryOne<{
        last_fetched: string | null;
        last_filed: string | null;
        coverage_start: string | null;
        last_backtest: string | null;
        latest_signal: string | null;
      }>(`
        SELECT
          (SELECT MAX(fetched_at)::text FROM form4_filings)          AS last_fetched,
          (SELECT MAX(filed_date)::text FROM form4_filings)          AS last_filed,
          (SELECT MIN(filed_date)::text FROM form4_filings)          AS coverage_start,
          (SELECT MAX(run_date)::text   FROM backtest_runs)          AS last_backtest,
          (SELECT MAX(signal_date)::text FROM signals)               AS latest_signal
      `),
      queryOne<{
        filings: number;
        transactions: number;
        purchase_transactions: number;
        signals: number;
        buy_signals: number;
        cluster_buy_signals: number;
        companies: number;
        companies_with_cap: number;
      }>(`
        SELECT
          (SELECT COUNT(*) FROM form4_filings)                                              AS filings,
          (SELECT COUNT(*) FROM transactions)                                               AS transactions,
          (SELECT COUNT(*) FROM transactions WHERE transaction_code = 'P')                  AS purchase_transactions,
          (SELECT COUNT(*) FROM signals)                                                    AS signals,
          (SELECT COUNT(*) FROM signals WHERE signal_type = 'BUY')                          AS buy_signals,
          (SELECT COUNT(*) FROM signals WHERE signal_type = 'CLUSTER_BUY')                  AS cluster_buy_signals,
          (SELECT COUNT(*) FROM companies)                                                  AS companies,
          (SELECT COUNT(*) FROM companies WHERE market_cap IS NOT NULL)                     AS companies_with_cap
      `),
    ]);

    return {
      lastFilingFetchedAt: meta?.last_fetched ?? null,
      lastFiledDate: meta?.last_filed ?? null,
      lastBacktestRunDate: meta?.last_backtest ?? null,
      latestSignalDate: meta?.latest_signal ?? null,
      coverageStart: meta?.coverage_start ?? null,
      counts: {
        filings: Number(counts?.filings ?? 0),
        transactions: Number(counts?.transactions ?? 0),
        purchaseTransactions: Number(counts?.purchase_transactions ?? 0),
        signals: Number(counts?.signals ?? 0),
        buySignals: Number(counts?.buy_signals ?? 0),
        clusterBuySignals: Number(counts?.cluster_buy_signals ?? 0),
        companies: Number(counts?.companies ?? 0),
        companiesWithCap: Number(counts?.companies_with_cap ?? 0),
      },
    };
  },
  ["pipeline-status"],
  { tags: CACHE_TAGS, revalidate: 900 },
);

/**
 * The next scheduled GitHub Actions run, computed from the cron schedules in
 * `.github/workflows/`. Kept here rather than read from a table because Actions
 * has no "next run" API and the crons are fixed.
 *   daily_ingest.yml  -> weekdays 11:00 UTC
 *   weekly_backtest.yml -> Sundays 12:00 UTC
 */
export function nextScheduledRuns(now = new Date()): {
  nextIngest: Date;
  nextBacktest: Date;
} {
  const nextIngest = new Date(now);
  nextIngest.setUTCHours(11, 0, 0, 0);
  if (nextIngest <= now) nextIngest.setUTCDate(nextIngest.getUTCDate() + 1);
  // Skip Sat (6) and Sun (0).
  while (nextIngest.getUTCDay() === 0 || nextIngest.getUTCDay() === 6) {
    nextIngest.setUTCDate(nextIngest.getUTCDate() + 1);
  }

  const nextBacktest = new Date(now);
  nextBacktest.setUTCHours(12, 0, 0, 0);
  const daysUntilSunday = (7 - nextBacktest.getUTCDay()) % 7;
  if (daysUntilSunday === 0 && nextBacktest <= now) {
    nextBacktest.setUTCDate(nextBacktest.getUTCDate() + 7);
  } else {
    nextBacktest.setUTCDate(nextBacktest.getUTCDate() + daysUntilSunday);
  }

  return { nextIngest, nextBacktest };
}

/** Distinct tickers that have at least one signal, for the ticker search. */
export const getSignalTickers = unstable_cache(
  async (): Promise<string[]> => {
    const rows = await query<{ ticker: string }>(
      `SELECT DISTINCT ticker FROM signals ORDER BY ticker`,
    );
    return rows.map((r) => r.ticker);
  },
  ["signal-tickers"],
  { tags: ["signals"], revalidate: 900 },
);
