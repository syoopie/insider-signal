"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A pressable filter chip. Selection is carried by border, weight and a filled
 * ground — never by colour alone — so the state survives a greyscale print and
 * reads the same to a colour-blind user.
 */
export function FilterChip({
  selected,
  onClick,
  children,
  className,
  title,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      title={title}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
        selected
          ? "border-primary/40 bg-primary/10 font-medium text-foreground"
          : "border-border bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
        className,
      )}
    >
      {children}
    </button>
  );
}

/** A labelled row of chips. */
export function FilterGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}
