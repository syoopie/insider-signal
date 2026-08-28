"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type Column<T> = {
  key: string;
  header: ReactNode;
  /** CSS grid track, e.g. "1fr", "120px", "minmax(0,2fr)". */
  width: string;
  align?: "start" | "end" | "center";
  cell: (row: T) => ReactNode;
  /** Return a comparable value to enable sorting on this column. */
  sortValue?: (row: T) => string | number | null | undefined;
};

type SortState = { key: string; dir: "asc" | "desc" } | null;

const PAGE = 100;

export function DataTable<T>({
  rows,
  columns,
  getRowKey,
  initialSort,
  emptyMessage = "Nothing to show.",
  className,
  dense,
}: {
  rows: T[];
  columns: Column<T>[];
  getRowKey: (row: T, index: number) => string;
  initialSort?: SortState;
  emptyMessage?: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  const [sort, setSort] = useState<SortState>(initialSort ?? null);
  const [limit, setLimit] = useState(PAGE);

  const gridTemplateColumns = columns.map((c) => c.width).join(" ");

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = col.sortValue!(a);
      const bv = col.sortValue!(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }, [rows, sort, columns]);

  // A fresh sort resets pagination so a stale larger page can't leak rows.
  const visible = sorted.slice(0, limit);
  const remaining = sorted.length - visible.length;

  const toggleSort = (key: string) => {
    setLimit(PAGE);
    setSort((prev) => {
      if (prev?.key !== key) return { key, dir: "desc" };
      if (prev.dir === "desc") return { key, dir: "asc" };
      return null;
    });
  };

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed px-6 py-10 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className={cn("overflow-hidden rounded-lg border", className)}>
      <div className="overflow-x-auto">
        <div className="min-w-full" style={{ minWidth: "max-content" }}>
          {/* Header */}
          <div
            className="grid items-center gap-x-3 border-b bg-muted/50 px-3 py-2 text-xs font-medium text-muted-foreground"
            style={{ gridTemplateColumns }}
            role="row"
          >
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const SortIcon = !col.sortValue
                ? null
                : active
                  ? sort!.dir === "asc"
                    ? ArrowUp
                    : ArrowDown
                  : ChevronsUpDown;
              return (
                <div
                  key={col.key}
                  role="columnheader"
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  className={cn(
                    "flex items-center gap-1",
                    col.align === "end" && "justify-end",
                    col.align === "center" && "justify-center",
                  )}
                >
                  {col.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(col.key)}
                      className="flex items-center gap-1 rounded hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                    >
                      {col.header}
                      {SortIcon && <SortIcon className={cn("size-3", !active && "opacity-40")} />}
                    </button>
                  ) : (
                    col.header
                  )}
                </div>
              );
            })}
          </div>

          {/* Rows */}
          {visible.map((row, i) => (
            <div
              key={getRowKey(row, i)}
              role="row"
              className={cn(
                "grid items-center gap-x-3 border-b px-3 text-sm last:border-b-0",
                dense ? "py-1.5" : "py-2.5",
              )}
              style={{ gridTemplateColumns }}
            >
              {columns.map((col) => (
                <div
                  key={col.key}
                  role="cell"
                  className={cn(
                    "min-w-0 truncate",
                    col.align === "end" && "text-right",
                    col.align === "center" && "text-center",
                  )}
                >
                  {col.cell(row)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      {remaining > 0 && (
        <div className="border-t bg-muted/30 p-2 text-center">
          <Button variant="ghost" size="sm" onClick={() => setLimit((l) => l + PAGE)}>
            Load more ({remaining} remaining)
          </Button>
        </div>
      )}
    </div>
  );
}
