/**
 * The scoring model, mirrored from `src/signals/discount.py` for the explainer.
 *
 * The knots are the empirical distribution of "how far below its 52-week high
 * was this stock on the day the insider bought", over the 8,289 eligible,
 * labelled purchases in the research sample. The score is that value's
 * percentile, so 90 is the top decile.
 *
 * Keep the table byte-identical to the Python. It is the whole model.
 */

export const DISCOUNT_KNOTS: ReadonlyArray<readonly [number, number]> = [
  [0.0, 0],
  [0.67, 5],
  [3.32, 10],
  [6.42, 15],
  [9.27, 20],
  [12.14, 25],
  [14.87, 30],
  [17.0, 35],
  [19.54, 40],
  [22.06, 45],
  [24.87, 50],
  [28.01, 55],
  [31.55, 60],
  [35.24, 65],
  [39.07, 70],
  [43.0, 75],
  [47.61, 80],
  [52.47, 85],
  [60.12, 90],
  [69.66, 95],
  [99.15, 100],
];

/** The tenth decile. Everything the model measures lives at or beyond here. */
export const DEEP_DISCOUNT_PCT = DISCOUNT_KNOTS[DISCOUNT_KNOTS.length - 3][0];

/** Percentile of a 52-week discount, 0 to 100. Mirrors `discount_score`. */
export function discountScore(pctBelow52wkHigh: number): number {
  if (!Number.isFinite(pctBelow52wkHigh)) return 0;
  const first = DISCOUNT_KNOTS[0];
  const last = DISCOUNT_KNOTS[DISCOUNT_KNOTS.length - 1];
  if (pctBelow52wkHigh <= first[0]) return first[1];
  if (pctBelow52wkHigh >= last[0]) return last[1];

  for (let i = 0; i < DISCOUNT_KNOTS.length - 1; i += 1) {
    const [loValue, loScore] = DISCOUNT_KNOTS[i];
    const [hiValue, hiScore] = DISCOUNT_KNOTS[i + 1];
    if (pctBelow52wkHigh <= hiValue) {
      if (hiValue <= loValue) return hiScore;
      const fraction = (pctBelow52wkHigh - loValue) / (hiValue - loValue);
      return Math.round(loScore + fraction * (hiScore - loScore));
    }
  }
  return last[1];
}

/**
 * What the top decile bought, measured out of sample.
 *
 * Walk-forward across 18 months and 6,690 purchases, each pick charged against
 * the other purchases of its own month and its own volatility quintile.
 */
export const DISCOUNT_EVIDENCE = {
  alpha: 11.13,
  median: 7.39,
  tStat: 2.29,
  months: 18,
  observations: 6690,
  hitRate: 57.7,
  /** The same screen on stocks nobody bought, same dates, same holding windows. */
  placeboAlpha: 5.55,
  placeboMedian: -1.3,
  placeboHitRate: 49.3,
  /** The additive factor table this replaced, measured the same way. */
  previousAlpha: 0.78,
  previousP: 0.27,
} as const;
