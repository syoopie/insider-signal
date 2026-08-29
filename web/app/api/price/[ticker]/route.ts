import { NextResponse } from "next/server";

/**
 * Current price for one ticker, proxied from the Yahoo Finance chart API.
 *
 * A proxy rather than a direct browser call for three reasons: Yahoo sends no
 * CORS headers, the upstream wants a browser-ish User-Agent, and routing through
 * here lets the CDN hold one response for five minutes instead of every visitor
 * hitting Yahoo. Prices are never stored — the decision on this project is that
 * quotes are fetched on demand and nothing else.
 *
 * Mirrors `_fetch_current_price()` in the Streamlit app it replaces.
 */

const UPSTREAM = "https://query1.finance.yahoo.com/v8/finance/chart";
const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

export const revalidate = 300;

export async function GET(
  _request: Request,
  { params }: RouteContext<"/api/price/[ticker]">,
) {
  const { ticker } = await params;
  const symbol = ticker.toUpperCase();

  // The symbol goes into an outbound URL, so it is validated rather than escaped.
  if (!TICKER_RE.test(symbol)) {
    return NextResponse.json({ error: "invalid ticker" }, { status: 400 });
  }

  try {
    const res = await fetch(`${UPSTREAM}/${symbol}?interval=1d&range=5d`, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible)" },
      next: { revalidate: 300 },
    });

    if (!res.ok) {
      return NextResponse.json({ error: "upstream error", price: null }, { status: 502 });
    }

    const json = (await res.json()) as {
      chart?: { result?: Array<{ meta?: { regularMarketPrice?: number; currency?: string } }> };
    };
    const meta = json.chart?.result?.[0]?.meta;
    const price = typeof meta?.regularMarketPrice === "number" ? meta.regularMarketPrice : null;

    return NextResponse.json(
      { ticker: symbol, price, currency: meta?.currency ?? "USD", fetchedAt: new Date().toISOString() },
      { headers: { "Cache-Control": "public, s-maxage=300, stale-while-revalidate=600" } },
    );
  } catch {
    // A quote is a nicety; the page must render fine without one.
    return NextResponse.json({ error: "unreachable", price: null }, { status: 502 });
  }
}
