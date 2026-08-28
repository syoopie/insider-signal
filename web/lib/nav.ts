import type { LucideIcon } from "lucide-react";
import { Activity, LayoutGrid, LineChart, Network, PieChart, Search } from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
};

/**
 * Single source of truth for primary navigation. Each page's header icon is the
 * same icon used here, so the rail and the page never look like different places.
 */
export const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    label: "Signals",
    icon: LayoutGrid,
    description: "New insider-buy signals to triage today",
  },
  {
    href: "/backtest",
    label: "Backtest",
    icon: LineChart,
    description: "Hit rate, excess return, and alpha decay over 730 days",
  },
  {
    href: "/clusters",
    label: "Clusters",
    icon: Network,
    description: "Active 14-day windows with 3+ insiders buying",
  },
  {
    href: "/sectors",
    label: "Sectors",
    icon: PieChart,
    description: "Which industries insiders are buying into",
  },
  {
    href: "/ticker",
    label: "Research",
    icon: Search,
    description: "Full insider transaction and signal history for one ticker",
  },
  {
    href: "/how-it-works",
    label: "How it works",
    icon: Activity,
    description: "The pipeline, the scoring model, and the research behind it",
  },
];

export function navItemForPath(pathname: string): NavItem | undefined {
  if (pathname === "/") return NAV_ITEMS[0];
  return NAV_ITEMS.find((i) => i.href !== "/" && pathname.startsWith(i.href));
}
