import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type EmptyStateProps = {
  icon?: LucideIcon;
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
  /** Distinguishes "the query failed" from "there is genuinely nothing here". */
  variant?: "empty" | "error";
};

export function EmptyState({
  icon: Icon,
  title,
  description,
  children,
  className,
  variant = "empty",
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-16 text-center",
        variant === "error" && "border-destructive/40 bg-destructive/5",
        className,
      )}
    >
      {Icon && (
        <div
          className={cn(
            "mb-3 flex size-11 items-center justify-center rounded-full",
            variant === "error" ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground",
          )}
        >
          <Icon className="size-5" />
        </div>
      )}
      <p className="font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-muted-foreground text-pretty">{description}</p>
      )}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
