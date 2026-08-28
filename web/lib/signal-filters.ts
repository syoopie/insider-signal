import {
  createLoader,
  parseAsArrayOf,
  parseAsInteger,
  parseAsString,
  parseAsStringLiteral,
} from "nuqs/server";
import { CAP_TIERS, SIGNAL_TYPES, type CapTier, type SignalType } from "@/lib/types";

/**
 * Triage filters live in the URL so a filtered view is shareable and survives a
 * reload. Defined once here and consumed from both sides: `loadSignalFilters`
 * on the server page, `signalFilterParsers` by `useQueryStates` on the client.
 */

/** Offered lookback windows, in days. The daily ingest makes a 14-day default the natural triage window. */
export const LOOKBACK_OPTIONS = [7, 14, 30, 90] as const;

/**
 * Score cut-offs worth offering. A slider invites false precision — the score is
 * a sum of fixed integer factors, so only a handful of thresholds mean anything.
 * 50 is the default: it cuts roughly 60% of the noise (see the Streamlit caption
 * this replaces) without hiding downgraded clusters.
 */
export const MIN_SCORE_OPTIONS = [0, 45, 50, 60, 70] as const;

export const DEFAULT_LOOKBACK = 14;
export const DEFAULT_MIN_SCORE = 50;
export const DEFAULT_TYPES: SignalType[] = ["CLUSTER_BUY", "BUY"];
/** Large-cap is off by default: 0% hit rate at 90d, −16% avg excess return. */
export const DEFAULT_CAPS: CapTier[] = ["small", "mid", "unknown"];

export const signalFilterParsers = {
  days: parseAsInteger.withDefault(DEFAULT_LOOKBACK),
  min: parseAsInteger.withDefault(DEFAULT_MIN_SCORE),
  types: parseAsArrayOf(parseAsStringLiteral(SIGNAL_TYPES)).withDefault(DEFAULT_TYPES),
  caps: parseAsArrayOf(parseAsStringLiteral(CAP_TIERS)).withDefault(DEFAULT_CAPS),
  /** Free-text match on ticker or company name. */
  q: parseAsString.withDefault(""),
  /** ISO date; set by clicking a day in the calendar strip. */
  day: parseAsString.withDefault(""),
};

export const loadSignalFilters = createLoader(signalFilterParsers);

export type SignalFilters = {
  days: number;
  min: number;
  types: SignalType[];
  caps: CapTier[];
  q: string;
  day: string;
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The URL is user input. Clamp everything to the offered ranges before it
 * reaches a query, and drop an empty type/cap selection back to the default so
 * an unselectable-all state can't render a permanently empty page.
 */
export function normalizeFilters(raw: {
  days: number;
  min: number;
  types: SignalType[];
  caps: CapTier[];
  q: string;
  day: string;
}): SignalFilters {
  const maxLookback = LOOKBACK_OPTIONS[LOOKBACK_OPTIONS.length - 1];
  return {
    days: Math.min(maxLookback, Math.max(1, Math.round(raw.days) || DEFAULT_LOOKBACK)),
    min: Math.min(100, Math.max(0, Math.round(raw.min) || 0)),
    types: raw.types.length > 0 ? raw.types : DEFAULT_TYPES,
    caps: raw.caps.length > 0 ? raw.caps : DEFAULT_CAPS,
    q: raw.q.trim().slice(0, 64),
    day: ISO_DATE.test(raw.day) ? raw.day : "",
  };
}

/** True when nothing has been changed from the defaults — used to show/hide "Reset". */
export function isDefaultFilters(f: SignalFilters): boolean {
  return (
    f.days === DEFAULT_LOOKBACK &&
    f.min === DEFAULT_MIN_SCORE &&
    f.q === "" &&
    f.day === "" &&
    sameSet(f.types, DEFAULT_TYPES) &&
    sameSet(f.caps, DEFAULT_CAPS)
  );
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && [...a].sort().join() === [...b].sort().join();
}

/**
 * Free-text match on ticker or company name.
 *
 * Lives here rather than in the query module because both sides run it: the
 * server applies it once so a shared `?q=` link is correct on first paint, and
 * the list component re-runs it on every keystroke against rows it already has,
 * so typing never touches the database.
 */
export function matchesQuery(
  signal: { ticker: string; companyName: string },
  q: string,
): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return (
    signal.ticker.toLowerCase().includes(needle) ||
    signal.companyName.toLowerCase().includes(needle)
  );
}
