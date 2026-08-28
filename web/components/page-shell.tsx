import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type PageShellProps = {
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  actions?: ReactNode;
  children: ReactNode;
  /** Narrower measure for text-heavy pages. Defaults to the app-wide 1600px. */
  maxWidth?: "default" | "prose";
};

/**
 * Every page's frame: bounded, centred content column with a consistent header.
 * The header icon matches the nav icon for the same route.
 */
export function PageShell({
  title,
  subtitle,
  icon: Icon,
  actions,
  children,
  maxWidth = "default",
}: PageShellProps) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-4 py-6 sm:px-6 sm:py-8",
        maxWidth === "prose" ? "max-w-3xl" : "max-w-[1600px]",
      )}
    >
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          {Icon && (
            <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Icon className="size-[18px]" />
            </div>
          )}
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
            {subtitle && (
              <p className="mt-0.5 text-sm text-muted-foreground text-pretty">{subtitle}</p>
            )}
          </div>
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}
