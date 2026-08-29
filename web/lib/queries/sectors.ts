import { unstable_cache } from "next/cache";
import { query } from "@/lib/db";
import { sectorForSic, UNKNOWN_SECTOR } from "@/lib/sic";

/**
 * Sector reads.
 *
 * `companies.sic_code` is populated by `scripts/backfill_sic.py`, not by the
 * daily ingest — Form 4 XML carries no industry classification. Until that has
 * run, every company lands in "Unclassified", which the page says out loud
 * rather than rendering an empty chart.
 */

export type SectorRow = {
  sector: string;
  signals: number;
  clusterBuys: number;
  buys: number;
  companies: number;
  avgScore: number;
  /** Most-signalled tickers in the sector, for the drill-down line. */
  topTickers: { ticker: string; signals: number }[];
};

export type SectorSummary = {
  rows: SectorRow[];
  totalSignals: number;
  /** How much of the company table has a SIC code at all. */
  classifiedCompanies: number;
  totalCompanies: number;
};

export const getSectors = unstable_cache(
  async (days: number): Promise<SectorSummary> => {
    const [signalRows, coverage] = await Promise.all([
      query<{
        ticker: string;
        sic_code: string | null;
        sic_description: string | null;
        signal_type: string;
        score: number;
      }>(
        `
          SELECT s.ticker, s.signal_type, s.score, c.sic_code, c.sic_description
          FROM signals s
          LEFT JOIN LATERAL (
            SELECT co.sic_code, co.sic_description FROM companies co
            WHERE co.ticker = s.ticker
            ORDER BY co.market_cap DESC NULLS LAST, co.cik
            LIMIT 1
          ) c ON TRUE
          WHERE s.signal_date >= (CURRENT_DATE - ($1::int - 1))
            AND s.signal_type IN ('BUY', 'CLUSTER_BUY', 'WATCH')
        `,
        [days],
      ),
      query<{ classified: string | number; total: string | number }>(`
        SELECT
          COUNT(*) FILTER (WHERE sic_code IS NOT NULL) AS classified,
          COUNT(*)                                     AS total
        FROM companies
      `),
    ]);

    const bySector = new Map<
      string,
      { signals: number; clusterBuys: number; buys: number; scoreSum: number; tickers: Map<string, number> }
    >();

    for (const row of signalRows) {
      const sector = sectorForSic(row.sic_code);
      let acc = bySector.get(sector);
      if (!acc) {
        acc = { signals: 0, clusterBuys: 0, buys: 0, scoreSum: 0, tickers: new Map() };
        bySector.set(sector, acc);
      }
      acc.signals++;
      acc.scoreSum += Number(row.score);
      if (row.signal_type === "CLUSTER_BUY") acc.clusterBuys++;
      if (row.signal_type === "BUY") acc.buys++;
      acc.tickers.set(row.ticker, (acc.tickers.get(row.ticker) ?? 0) + 1);
    }

    const rows: SectorRow[] = [...bySector.entries()]
      .map(([sector, acc]) => ({
        sector,
        signals: acc.signals,
        clusterBuys: acc.clusterBuys,
        buys: acc.buys,
        companies: acc.tickers.size,
        avgScore: Math.round(acc.scoreSum / acc.signals),
        topTickers: [...acc.tickers.entries()]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .slice(0, 5)
          .map(([ticker, signals]) => ({ ticker, signals })),
      }))
      // Unclassified is an artefact of missing data, not a sector, so it sorts last.
      .sort((a, b) =>
        a.sector === UNKNOWN_SECTOR ? 1 : b.sector === UNKNOWN_SECTOR ? -1 : b.signals - a.signals,
      );

    return {
      rows,
      totalSignals: signalRows.length,
      classifiedCompanies: Number(coverage[0]?.classified ?? 0),
      totalCompanies: Number(coverage[0]?.total ?? 0),
    };
  },
  ["sectors"],
  { tags: ["signals"], revalidate: 900 },
);
