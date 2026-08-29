"use client";

import useSWR from "swr";

export type Quote = { ticker: string; price: number | null; currency?: string };

const fetcher = async (url: string): Promise<Quote> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error("price unavailable");
  return res.json();
};

/**
 * Current price for a ticker, fetched on demand.
 *
 * Deliberately not part of any server render: prices move continuously while
 * the rest of the page changes once a day, so mixing them would either make the
 * whole page uncacheable or show a stale quote as though it were live.
 */
export function usePrice(ticker: string | null) {
  const { data, error, isLoading } = useSWR<Quote>(
    ticker ? `/api/price/${encodeURIComponent(ticker)}` : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300_000, shouldRetryOnError: false },
  );

  return { price: data?.price ?? null, isLoading, failed: !!error };
}
