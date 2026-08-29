"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type TickerOption = { ticker: string; name: string; signals: number };

/**
 * Ticker lookup. The whole list is handed over at build/render time — a few
 * thousand short rows — so matching happens locally and every keystroke is
 * instant. A remote autocomplete would add a round trip per character to search
 * a list small enough to hold in memory.
 */
export function TickerSearch({
  options,
  autoFocus = false,
  className,
}: {
  options: TickerOption[];
  autoFocus?: boolean;
  className?: string;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    const scored = options
      .filter(
        (o) =>
          o.ticker.toLowerCase().includes(needle) || o.name.toLowerCase().includes(needle),
      )
      // Exact and prefix ticker matches first, then companies with more signals.
      .sort((a, b) => rank(a, needle) - rank(b, needle) || b.signals - a.signals);
    return scored.slice(0, 8);
  }, [q, options]);

  const go = (ticker: string) => router.push(`/ticker/${encodeURIComponent(ticker)}`);

  return (
    <div className={cn("relative", className)}>
      <Search
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden
      />
      <Input
        value={q}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((i) => Math.min(i + 1, matches.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            const pick = matches[active] ?? matches[0];
            if (pick) go(pick.ticker);
            else if (q.trim()) go(q.trim().toUpperCase());
          }
        }}
        placeholder="Search ticker or company…"
        aria-label="Search ticker or company"
        role="combobox"
        aria-expanded={matches.length > 0}
        aria-controls="ticker-search-results"
        className="h-11 pl-9 text-base"
      />

      {matches.length > 0 && (
        <ul
          id="ticker-search-results"
          className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border bg-popover shadow-lg"
        >
          {matches.map((o, i) => (
            <li key={o.ticker}>
              <Link
                href={`/ticker/${encodeURIComponent(o.ticker)}`}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex items-baseline justify-between gap-3 px-3 py-2 text-sm",
                  i === active && "bg-muted",
                )}
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="font-mono font-semibold">{o.ticker}</span>
                  <span className="truncate text-muted-foreground">{o.name}</span>
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {o.signals > 0 ? `${o.signals} signal${o.signals === 1 ? "" : "s"}` : "no signals"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function rank(o: TickerOption, needle: string): number {
  const t = o.ticker.toLowerCase();
  if (t === needle) return 0;
  if (t.startsWith(needle)) return 1;
  if (t.includes(needle)) return 2;
  return 3;
}
