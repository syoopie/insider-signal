/**
 * The classification rules, mirrored from `src/signals/scorer.py` for the
 * interactive explainer on /how-it-works.
 *
 * Kept in one place and quoted on the page so a reader can check the numbers
 * against the Python. If scorer.py changes, this changes with it.
 */

export const THRESHOLDS = {
  /**
   * classify_signal(): score >= 90 -> BUY. The score is the purchase's discount
   * percentile among filings from the preceding 60 days, so this is the top
   * decile of what insiders are currently buying rather than a fixed price cut.
   */
  buy: 90,
  /** classify_signal(): score >= 70 -> WATCH */
  watch: 70,
  /** classify_signal(): mean of participant scores must reach this for CLUSTER_BUY */
  clusterAvg: 80,
  /** classify_signal(): ...and either a tight window, or a max individual score this high */
  clusterMaxScore: 85,
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
    cite: "This system's own walk-forward test (2026-08-30)",
    finding:
      "Among insider purchases, the tenth of them in the most beaten-down stocks returned +11.13 percentage points more than the other purchases of the same month and comparable volatility, with a median of +7.39pp, across 18 months and 6,690 out-of-sample observations. Above all 5,000 random rankings on both.",
    used: "The entire score. A purchase's rank is its 52-week discount percentile.",
  },
  {
    cite: "Placebo control, same study",
    finding:
      "The same screen applied to stocks nobody bought, on the same dates with the same holding windows, returned +5.55pp on the mean but −1.30pp on the median, at a 49.3% hit rate. Insider purchases in the same bucket hit 57.7%.",
    used: "Why the Form 4 remains the gate. Discounted stocks alone are a lottery; the typical one loses money.",
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
    cite: "Lakonishok & Lee (2001), and what happened to it here",
    finding:
      "Small-cap insider buys earned +7.4% abnormal return at twelve months, which is why small-cap purchases used to score +15.",
    used:
      "Nothing, now. Restricting the ranking to small caps lowers it from +11.13pp to +6.08pp, and the size factor measured the opposite sign to its old weight.",
  },
  {
    cite: "Cluster research, and what happened to it here",
    finding: "Three or more insiders buying together were reported to carry roughly twice the alpha of one.",
    used:
      "Clusters are still detected and shown, but no longer promoted on size alone. Inside the most discounted third of purchases, the number of cluster buyers points the wrong way at −4.53pp with t=−1.85.",
  },
] as const;

export const LIMITATIONS = [
  "The score is one number: how far below its 52-week high the stock sat when the insider bought, ranked against the purchases disclosed in the preceding 60 days. Everything else the model records — role, company size, purchase size, buying history — is shown because it describes the filing, and scores nothing, because measured out of sample none of it ranked.",
  "Because the ranking is relative, a BUY means \"among the most beaten-down things insiders are buying right now\", not \"below some fixed price\". A fixed cutoff was tried first and gave away more than half the effect, selecting 2% of one month’s purchases and 24% of another’s.",
  "That one number rests on 18 months and a t-statistic of 2.29. It is a real effect by every control applied to it, and it is not a large sample.",
  "A stock with under a year of trading history has no 52-week high, so its purchases score zero and are never alerted. That is about 7% of purchases, mostly recent listings.",
  "Signals are dated the day after the filing reaches EDGAR, never the transaction date. Insiders have two business days to file, so acting on the transaction date would assume knowledge nobody had.",
  "The backtest models entry at the filing date plus four days. Fills at a different price, and any slippage or commission, are not modelled.",
  "Delisted tickers are scored as a 50% loss rather than dropped. That is a blunt correction for survivorship bias and may be too harsh or too kind in any individual case.",
  "Coverage begins in April 2024, and the first weeks are thin. The universe is the S&P 500 plus Russell 2000; insider buying outside it is invisible here.",
  "Backtested performance is not a forecast.",
] as const;
