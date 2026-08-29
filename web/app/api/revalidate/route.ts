import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

/**
 * Cache bust, called by the ingest workflow after a successful run.
 *
 * Query results are cached for 15 minutes, which is invisible on a pipeline that
 * changes once a weekday — except right after it changes, when the dashboard
 * would keep serving yesterday's signals for a further quarter of an hour. This
 * closes that window.
 *
 * Authenticated with a shared secret in the Authorization header. Without
 * REVALIDATE_SECRET set the route refuses every request rather than defaulting
 * open: an unauthenticated cache-buster is a free way to hammer the database.
 */

const TAGS = ["pipeline", "signals", "backtest"] as const;

export async function POST(request: Request) {
  const secret = process.env.REVALIDATE_SECRET;

  if (!secret) {
    return NextResponse.json({ error: "revalidation is not configured" }, { status: 503 });
  }

  const provided = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? "";

  if (!timingSafeEqual(provided, secret)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  // "max": the next request for each tag is served stale while the refresh runs
  // in the background, so a cache bust can never make a visitor wait on the
  // database. Correctness here is measured in minutes, not milliseconds.
  for (const tag of TAGS) revalidateTag(tag, "max");

  return NextResponse.json({ revalidated: TAGS, at: new Date().toISOString() });
}

/**
 * Constant-time comparison. `===` on secrets leaks their length and prefix
 * through timing; this is cheap enough that there is no reason not to.
 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
