"use client";

import { DataTable, type Column } from "@/components/data-table";
import { Money } from "@/components/money";
import type { Insider } from "@/lib/types";
import { fmtDate, fmtInt, fmtPctSigned, titleCase } from "@/lib/format";

const columns: Column<Insider>[] = [
  {
    key: "name",
    header: "Insider",
    width: "minmax(0, 1.6fr)",
    cell: (r) => (
      <span className="truncate" title={r.name}>
        {r.name}
      </span>
    ),
    sortValue: (r) => r.name,
  },
  {
    key: "role",
    header: "Role",
    width: "minmax(0, 1fr)",
    cell: (r) => (
      <span className="text-muted-foreground">{titleCase(r.role_raw ?? r.role ?? "")}</span>
    ),
    sortValue: (r) => r.role ?? "",
  },
  {
    key: "date",
    header: "Date",
    width: "96px",
    cell: (r) => fmtDate(r.transaction_date, { withYear: true }),
    sortValue: (r) => r.transaction_date ?? "",
  },
  {
    key: "shares",
    header: "Shares",
    width: "88px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{fmtInt(r.shares_bought)}</span>,
    sortValue: (r) => r.shares_bought ?? 0,
  },
  {
    key: "price",
    header: "Price",
    width: "72px",
    align: "end",
    cell: (r) => (
      <span className="tabular-nums">
        {r.price != null ? `$${r.price.toFixed(2)}` : "—"}
      </span>
    ),
    sortValue: (r) => r.price ?? 0,
  },
  {
    key: "value",
    header: "Value",
    width: "88px",
    align: "end",
    cell: (r) => <Money value={r.total_value} emphasizeAbove={250_000} />,
    sortValue: (r) => r.total_value ?? 0,
  },
  {
    key: "increase",
    header: "Position",
    width: "80px",
    align: "end",
    cell: (r) => (
      <span className="tabular-nums text-muted-foreground">
        {r.pct_increase != null ? fmtPctSigned(r.pct_increase, 0) : "—"}
      </span>
    ),
    sortValue: (r) => r.pct_increase ?? 0,
  },
];

export function InsiderTable({ insiders }: { insiders: Insider[] }) {
  return (
    <DataTable
      rows={insiders}
      columns={columns}
      getRowKey={(r, i) => `${r.name}-${i}`}
      initialSort={{ key: "value", dir: "desc" }}
      dense
      emptyMessage="No insider detail on this signal."
    />
  );
}
