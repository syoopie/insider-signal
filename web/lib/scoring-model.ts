/**
 * The classification rules, mirrored from `src/signals/scorer.py` for the
 * interactive explainer on /how-it-works.
 *
 * Kept in one place and quoted on the page so a reader can check the numbers
 * against the Python. If scorer.py changes, this changes with it.
 */

export const THRESHOLDS = {
  /** classify_signal(): score >= 60 -> BUY */
  buy: 60,
  /** classify_signal(): score >= 45 -> WATCH */
  watch: 45,
  /** classify_signal(): mean of participant scores must reach this for CLUSTER_BUY */
  clusterAvg: 22,
  /** classify_signal(): ...and either a tight window, or a max individual score this high */
  clusterMaxScore: 30,
  /** cluster.py: distinct insiders needed */
  clusterMinInsiders: 3,
  /** cluster.py: rolling window, in days */
  clusterWindowDays: 14,
  /** cluster.py: a tight cluster fits 3+ buyers into this many days */
  tightWindowDays: 5,
  /** cluster.py: minimum purchase value to count toward a cluster */
  clusterMinValue: 25_000,
  /** scorer.py: purchases below this are discarded as noise */
  minValue: 2_000,
} as const;

export const DISQUALIFIERS = [
  {
    title: "Not an open-market purchase",
    detail:
      "Only Form 4 transaction code P is scored. Awards, option exercises, gifts and tax withholdings move shares without anyone deciding to buy.",
  },
  {
    title: "Pre-arranged 10b5-1 plan",
    detail:
      "The trade was scheduled months earlier, so it says nothing about what the insider knows today.",
    research: "Cohen, Malloy & Pomorski (2012): routine trades show approximately zero alpha.",
  },
  {
    title: "Under $2,000",
    detail:
      "Dividend reinvestment, 401(k) contributions and fractional share purchases — automatic, not deliberate.",
  },
  {
    title: "Routine trader",
    detail:
      "The insider bought in the same calendar month in at least two of the three prior years. That is a standing plan, not a judgement about price.",
    research: "Cohen et al.: opportunistic trades earn 82 bps/month; routine trades earn nothing.",
  },
] as const;

export type SignalClass = "CLUSTER_BUY" | "BUY" | "WATCH" | "LOW";

/**
 * Mirrors `classify_signal()` plus the large-cap downgrade its callers apply.
 *
 * The downgrade lives in `run_ingest.py` and `backfill_signals.py` rather than
 * in `classify_signal()` itself; it is folded in here because from the outside
 * it is part of how a signal gets its type.
 */
export function classifySignal(input: {
  score: number;
  isCluster: boolean;
  clusterAvg?: number;
  tightCluster?: boolean;
  capTier?: string;
}): SignalClass {
  const { score, isCluster, clusterAvg = score, tightCluster = false, capTier } = input;

  if (isCluster) {
    const qualifies =
      clusterAvg >= THRESHOLDS.clusterAvg &&
      (tightCluster || score >= THRESHOLDS.clusterMaxScore);
    if (!qualifies) return "WATCH";
    // Large-cap clusters backtested at a 0% hit rate over 90 days, −16% average
    // excess return. They are surfaced but never alerted on.
    return capTier === "large" ? "WATCH" : "CLUSTER_BUY";
  }

  if (score >= THRESHOLDS.buy) return "BUY";
  if (score >= THRESHOLDS.watch) return "WATCH";
  return "LOW";
}

export const RESEARCH = [
  {
    cite: "Lakonishok & Lee (2001)",
    finding: "Small-cap insider buys earned +7.4% abnormal return at twelve months.",
    used: "Small-cap purchases score +15; larger companies score nothing.",
  },
  {
    cite: "Cohen, Malloy & Pomorski (2012)",
    finding:
      "Opportunistic insider trades earned 82 bps/month. Routine trades — same person, same month, every year — earned approximately zero.",
    used: "Routine traders and 10b5-1 plan trades are disqualified outright.",
  },
  {
    cite: "Jeng, Metrick & Zeckhauser (2003)",
    finding: "A portfolio of insider purchases earned roughly 6% annualised alpha.",
    used: "Purchases are the only transaction type scored; sales are stored but ignored.",
  },
  {
    cite: "TipRanks role study",
    finding:
      "CFO buys returned 21.5% annually, directors 20.7%, officers 19.8%, CEOs 19.3% — the CEO the weakest of the four.",
    used: "Role weighting, including a penalty for CEO-only purchases.",
  },
  {
    cite: "Cluster research",
    finding: "Three or more insiders buying together carried roughly twice the alpha of one.",
    used: "The whole cluster detection layer.",
  },
] as const;

export const LIMITATIONS = [
  "Signals are dated the day after the filing reaches EDGAR, never the transaction date. Insiders have two business days to file, so acting on the transaction date would assume knowledge nobody had.",
  "The backtest models entry at the filing date plus four days. Fills at a different price, and any slippage or commission, are not modelled.",
  "Delisted tickers are scored as a 50% loss rather than dropped. That is a blunt correction for survivorship bias and may be too harsh or too kind in any individual case.",
  "Market caps are refreshed weekly, so a company's tier can be stale by up to a week, and roughly a quarter of tracked companies have no resolvable cap at all.",
  "Coverage begins in April 2024, and the first weeks are thin. Anything before that does not exist here.",
  "The universe is the S&P 500 plus Russell 2000. Insider buying outside it is invisible to this system.",
  "Backtested performance is not a forecast. Sample sizes in the stratified breakdowns are small, and the model has been recalibrated against this same data — which is a real risk of overfitting.",
] as const;
