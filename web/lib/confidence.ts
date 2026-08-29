/**
 * How far to trust a bucket, from its sample size.
 *
 * Lives in its own module rather than in `lib/queries/backtest.ts` because the
 * `SampleSize` component is a client component: importing a *value* from a
 * query module would pull `lib/db.ts` — and the database client — into the
 * browser bundle.
 */
export type Confidence = "none" | "low" | "medium" | "high";

export function confidenceFor(n: number): Confidence {
  if (!n) return "none";
  if (n < 10) return "low";
  if (n < 30) return "medium";
  return "high";
}
