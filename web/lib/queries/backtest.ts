import { unstable_cache } from "next/cache";
import { query } from "@/lib/db";
import { parseBacktestMetrics, type Bucket } from "@/lib/types";

/**
 * Backtest reads.
 *
 * `backtest_runs.metrics` carries a `detail[]` array with one row per evaluated
 * signal — thousands of rows across four horizons. Everything derived from it is
 * aggregated here, on the server, so the page ships a few hundred points instead
 * of the whole array.
 */

export type HorizonSummary = {
  horizonDays: number;
  nTrades: number;
  hitRate: number | null;
  avgReturn: number | null;
  medianReturn: number | null;
  p25Return: number | null;
  p75Return: number | null;
  sharpe: number | null;
  iwmAvgReturn: number | null;
};

export type DistributionRow = {
  label: string;
  p25: number;
  median: number;
  p75: number;
  min: number;
  max: number;
};

/** One cell of a stratified table: a bucket for one group at one horizon. */
export type StratRow = {
  group: string;
  horizon: string;
  n: number;
  hitRate: number | null;
  avgReturn: number | null;
  medianReturn: number | null;
};

export type RiskRow = {
  horizon: string;
  pctLossGt20: number | null;
  maxConsecutiveLosses: number | null;
  worstOutcome: number | null;
  nNoSpyData: number | null;
};

export type ClusterRow = {
  horizon: string;
  n: number;
  hitRate: number | null;
  avgReturn: number | null;
  medianReturn: number | null;
};

/** A charting row: one x value plus one numeric field per horizon key ("30d", "60d", …). */
export type SeriesPoint = { x: string } & Record<string, string | number>;

export type Backtest = {
  runDate: string;
  horizons: HorizonSummary[];
  /** Horizon keys in ascending order, e.g. ["30d","60d","90d","180d"]. */
  horizonKeys: string[];
  distribution: DistributionRow[];
  byScoreBand: StratRow[];
  byCapTier: StratRow[];
  bySignalType: StratRow[];
  risk: RiskRow[];
  cluster5064: ClusterRow[];
  /** Monthly mean excess return per horizon, from `detail[].exec_date`. */
  monthlyExcess: SeriesPoint[];
  rollingHitRate: SeriesPoint[];
  /** Total signals behind `monthlyExcess`, for the chart qualifier. */
  detailCount: number;
};

type RunRow = {
  run_date: string;
  horizon_days: number;
  n_trades: number | null;
  hit_rate: string | number | null;
  avg_return: string | number | null;
  median_return: string | number | null;
  p25_return: string | number | null;
  p75_return: string | number | null;
  sharpe: string | number | null;
  iwm_avg_return: string | number | null;
  metrics: unknown;
};

const num = (v: string | number | null | undefined): number | null =>
  v === null || v === undefined || v === "" ? null : Number(v);

/**
 * The most recent run, one row per horizon.
 *
 * `save_backtest_results()` deletes only rows matching (run_date, threshold), so
 * two thresholds can coexist on one date. DISTINCT ON keeps the highest
 * threshold per horizon; without it the page would render each horizon twice.
 */
export const getBacktest = unstable_cache(
  async (): Promise<Backtest | null> => {
    const rows = await query<RunRow>(`
      SELECT DISTINCT ON (horizon_days)
        run_date::text AS run_date,
        horizon_days, n_trades, hit_rate, avg_return, median_return,
        p25_return, p75_return, sharpe, iwm_avg_return, metrics
      FROM backtest_runs
      WHERE run_date = (SELECT MAX(run_date) FROM backtest_runs)
      ORDER BY horizon_days, threshold DESC
    `);

    if (rows.length === 0) return null;
    return buildBacktest(rows);
  },
  ["backtest-latest"],
  { tags: ["backtest"], revalidate: 900 },
);

function buildBacktest(rows: RunRow[]): Backtest {
  const sorted = [...rows].sort((a, b) => a.horizon_days - b.horizon_days);
  const horizonKeys = sorted.map((r) => `${r.horizon_days}d`);
  const parsed = sorted.map((r) => ({ row: r, key: `${r.horizon_days}d`, m: parseBacktestMetrics(r.metrics) }));

  const horizons: HorizonSummary[] = sorted.map((r) => ({
    horizonDays: r.horizon_days,
    nTrades: Number(r.n_trades ?? 0),
    hitRate: num(r.hit_rate),
    avgReturn: num(r.avg_return),
    medianReturn: num(r.median_return),
    p25Return: num(r.p25_return),
    p75Return: num(r.p75_return),
    sharpe: num(r.sharpe),
    iwmAvgReturn: num(r.iwm_avg_return),
  }));

  const distribution: DistributionRow[] = [];
  for (const { key, m } of parsed) {
    const d = m.distribution;
    if (!d) continue;
    const { p25, median, p75, max_loss, max_gain } = d;
    if ([p25, median, p75, max_loss, max_gain].some((v) => v == null)) continue;
    distribution.push({
      label: key,
      p25: p25!,
      median: median!,
      p75: p75!,
      min: max_loss!,
      max: max_gain!,
    });
  }

  const strat = (field: "by_score_band" | "by_cap_tier" | "by_signal_type"): StratRow[] => {
    const out: StratRow[] = [];
    for (const { key, m } of parsed) {
      for (const [group, bucket] of Object.entries(m[field] ?? {})) {
        if (!bucket) continue;
        out.push({ group, horizon: key, ...bucketFields(bucket) });
      }
    }
    // Group-major so a reader compares horizons within one band, which is the
    // question the table exists to answer.
    return out.sort(
      (a, b) =>
        a.group.localeCompare(b.group) ||
        horizonKeys.indexOf(a.horizon) - horizonKeys.indexOf(b.horizon),
    );
  };

  const risk: RiskRow[] = parsed
    .filter(({ m }) => m.risk)
    .map(({ key, m }) => ({
      horizon: key,
      pctLossGt20: m.risk?.pct_loss_gt20 ?? null,
      maxConsecutiveLosses: m.risk?.max_consecutive_losses ?? null,
      worstOutcome: m.risk?.worst_outcome ?? null,
      nNoSpyData: m.risk?.n_no_spy_data ?? null,
    }));

  const cluster5064: ClusterRow[] = parsed
    .filter(({ m }) => m.cluster_5064)
    .map(({ key, m }) => ({ horizon: key, ...bucketFields(m.cluster_5064!) }));

  // Monthly mean excess return per horizon, keyed by the month the signal was
  // executed. This is what gives the chart the full lookback window: one point
  // per weekly run would only span the handful of recorded runs.
  const monthSums = new Map<string, Map<string, { sum: number; n: number }>>();
  let detailCount = 0;
  for (const { key, m } of parsed) {
    for (const d of m.detail ?? []) {
      if (!d.exec_date) continue;
      detailCount++;
      const month = `${d.exec_date.slice(0, 7)}-01`;
      let byHorizon = monthSums.get(month);
      if (!byHorizon) monthSums.set(month, (byHorizon = new Map()));
      const acc = byHorizon.get(key) ?? { sum: 0, n: 0 };
      acc.sum += d.excess_return;
      acc.n += 1;
      byHorizon.set(key, acc);
    }
  }
  const monthlyExcess: SeriesPoint[] = [...monthSums.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, byHorizon]) => {
      const point: SeriesPoint = { x: month };
      for (const [key, { sum, n }] of byHorizon) point[key] = round1(sum / n);
      return point;
    });

  const rollingByDate = new Map<string, SeriesPoint>();
  for (const { key, m } of parsed) {
    for (const item of m.rolling_hit_rate_90d ?? []) {
      const date = item.date.slice(0, 10);
      const point = rollingByDate.get(date) ?? { x: date };
      point[key] = round1(item.hit_rate);
      rollingByDate.set(date, point);
    }
  }
  const rollingHitRate = [...rollingByDate.values()].sort((a, b) => a.x.localeCompare(b.x));

  return {
    runDate: sorted[0].run_date,
    horizons,
    horizonKeys,
    distribution,
    byScoreBand: strat("by_score_band"),
    byCapTier: strat("by_cap_tier"),
    bySignalType: strat("by_signal_type"),
    risk,
    cluster5064,
    monthlyExcess,
    rollingHitRate,
    detailCount,
  };
}

function bucketFields(b: Bucket) {
  return {
    n: Number(b.n ?? 0),
    hitRate: b.hit_rate ?? null,
    avgReturn: b.avg_return ?? null,
    medianReturn: b.median_return ?? null,
  };
}

function round1(v: number): number {
  return Math.round(v * 10) / 10;
}
