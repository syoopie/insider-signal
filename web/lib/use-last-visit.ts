"use client";

import { useEffect, useSyncExternalStore } from "react";

const KEY = "insider-signal:last-visit";

/**
 * Remembers when this browser last opened the triage page, so new signals can be
 * flagged. Per-browser and purely cosmetic — a cleared store just means nothing
 * is marked new, which is why it lives in localStorage and not the database.
 *
 * localStorage is an external store, so it is read through
 * `useSyncExternalStore`: the server snapshot is `null` (nothing is new until
 * the browser says otherwise), which also makes the hydrated render match.
 */

let snapshot: string | null | undefined;
const listeners = new Set<() => void>();

function getSnapshot(): string | null {
  if (snapshot === undefined) {
    try {
      snapshot = window.localStorage.getItem(KEY);
    } catch {
      // Private mode or storage disabled: nothing is marked new.
      snapshot = null;
    }
  }
  return snapshot;
}

/** Rendered on the server and during hydration. */
function getServerSnapshot(): string | null {
  return null;
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

function stamp(): void {
  const now = new Date().toISOString();
  try {
    window.localStorage.setItem(KEY, now);
  } catch {
    // Nothing to persist; the badges just reset on the next load.
  }
  snapshot = now;
  for (const l of listeners) l();
}

export type LastVisit = {
  /** ISO timestamp of the previous visit; null on a first visit or with storage unavailable. */
  since: string | null;
  /** Treat everything currently shown as seen. */
  markAllSeen: () => void;
};

export function useLastVisit(): LastVisit {
  const since = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // Stamp the visit without disturbing `snapshot`: the render above already
  // captured the previous value, so the "new" set stays stable while you work
  // through the list and resets on the next visit.
  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, new Date().toISOString());
    } catch {
      // Storage unavailable — the badges just reset on the next load.
    }
  }, []);

  return { since, markAllSeen: stamp };
}

/**
 * A signal counts as new when the pipeline dated it on or after the day of the
 * last visit. Day granularity, because `signal_date` is a date: comparing it
 * against a timestamp would mark today's signals as new indefinitely.
 */
export function isNewSince(signalDate: string, since: string | null): boolean {
  if (!since) return false;
  return signalDate >= since.slice(0, 10);
}
