"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ChartCard, PurchaseScatter, type PurchasePoint } from "@/components/charts";
import { DataTable, type Column } from "@/components/data-table";
import { Money } from "@/components/money";
import { SignalTypeBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtCurrency, fmtDate, fmtInt, titleCase } from "@/lib/format";
import { TRANSACTION_CODES } from "@/lib/transaction-codes";
import type { TickerSignal, TickerTransaction } from "@/lib/queries/ticker";
import { Inbox } from "lucide-react";
import { cn } from "@/lib/utils";

export function TickerViews({
  ticker,
  transactions,
  signals,
}: {
  ticker: string;
  transactions: TickerTransaction[];
  signals: TickerSignal[];
}) {
  const { opportunistic, routine } = useMemo(() => {
    const buys = transactions.filter(
      (t) => t.transactionCode === "P" && t.pricePerShare != null && t.pricePerShare > 0,
    );
    const toPoint = (t: TickerTransaction): PurchasePoint => ({
      t: new Date(t.transactionDate).getTime(),
      price: t.pricePerShare!,
      shares: t.shares ?? 0,
      label: `${t.insiderName} · ${fmtInt(t.shares)} sh`,
    });
    return {
      opportunistic: buys.filter((t) => t.isRoutine !== true).map(toPoint),
      routine: buys.filter((t) => t.isRoutine === true).map(toPoint),
    };
  }, [transactions]);

  const purchaseCount = opportunistic.length + routine.length;

  return (
    <div className="space-y-6">
      {purchaseCount > 0 && (
        <ChartCard
          title="Open-market purchases"
          qualifier={`${purchaseCount} buy${purchaseCount === 1 ? "" : "s"} · dot size = shares`}
        >
          <PurchaseScatter
            opportunistic={opportunistic}
            routine={routine}
            xFormat={(t) => fmtDate(new Date(t), { withYear: true })}
          />
          <p className="mt-3 text-xs text-muted-foreground text-pretty">
            A <strong>routine</strong> buy is one the same insider has made in the same calendar
            month in at least two of the last three years — a standing plan rather than a decision,
            so the model scores it zero. Everything else is treated as opportunistic.
          </p>
        </ChartCard>
      )}

      <Tabs defaultValue="transactions">
        <TabsList variant="line">
          <TabsTrigger value="transactions">
            Transactions
            <span className="ml-1.5 text-xs text-muted-foreground">{transactions.length}</span>
          </TabsTrigger>
          <TabsTrigger value="signals">
            Signals
            <span className="ml-1.5 text-xs text-muted-foreground">{signals.length}</span>
          </TabsTrigger>
          <TabsTrigger value="codes">Code reference</TabsTrigger>
        </TabsList>

        <TabsContent value="transactions" className="pt-4">
          {transactions.length > 0 ? (
            <DataTable
              rows={transactions}
              columns={txColumns}
              getRowKey={(r) => String(r.id)}
              initialSort={{ key: "date", dir: "desc" }}
              dense
            />
          ) : (
            <EmptyState
              icon={Inbox}
              title={`No Form 4 transactions stored for ${ticker}`}
              description="Either nothing has been filed in the ingested window, or the ticker is not one the pipeline tracks."
            />
          )}
        </TabsContent>

        <TabsContent value="signals" className="pt-4">
          {signals.length > 0 ? (
            <DataTable
              rows={signals}
              columns={signalColumns}
              getRowKey={(r) => String(r.id)}
              initialSort={{ key: "date", dir: "desc" }}
              dense
            />
          ) : (
            <EmptyState
              icon={Inbox}
              title={`${ticker} has never produced a signal`}
              description="Transactions can exist without a signal: sales, awards and option exercises are stored but never scored, and a purchase still has to clear the thresholds."
            />
          )}
        </TabsContent>

        <TabsContent value="codes" className="pt-4">
          <dl className="divide-y rounded-lg border">
            {Object.entries(TRANSACTION_CODES).map(([code, meta]) => (
              <div key={code} className="grid grid-cols-[40px_140px_1fr] items-baseline gap-3 px-3 py-2">
                <dt className="font-mono text-sm font-semibold">{code}</dt>
                <dd className="text-sm font-medium">{meta.label}</dd>
                <dd className="text-sm text-muted-foreground text-pretty">{meta.note}</dd>
              </div>
            ))}
          </dl>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function TransactionValue({ tx }: { tx: TickerTransaction }) {
  if (tx.totalValue == null) return <span className="text-muted-foreground">—</span>;
  if (tx.transactionCode === "P") return <Money value={tx.totalValue} />;
  if (tx.transactionCode === "S") {
    return (
      <span className="tabular-nums text-destructive">
        {"\u2212"}
        {fmtCurrency(tx.totalValue)}
      </span>
    );
  }
  // Awards, exercises, gifts and withholdings move shares without an
  // open-market decision behind them, so they get no direction at all.
  return <span className="tabular-nums text-muted-foreground">{fmtCurrency(tx.totalValue)}</span>;
}

const txColumns: Column<TickerTransaction>[] = [
  {
    key: "date",
    header: "Date",
    width: "104px",
    cell: (r) => fmtDate(r.transactionDate, { withYear: true }),
    sortValue: (r) => r.transactionDate,
  },
  {
    key: "code",
    header: "Code",
    width: "56px",
    cell: (r) => (
      <span
        className={cn(
          "font-mono text-xs font-semibold",
          r.transactionCode === "P" && "text-success",
          r.transactionCode === "S" && "text-destructive",
        )}
        title={TRANSACTION_CODES[r.transactionCode]?.label ?? "Other"}
      >
        {r.transactionCode}
      </span>
    ),
    sortValue: (r) => r.transactionCode,
  },
  {
    key: "insider",
    header: "Insider",
    width: "minmax(0, 1.5fr)",
    cell: (r) => (
      <span className="truncate" title={r.insiderRole ?? undefined}>
        {r.insiderName}
      </span>
    ),
    sortValue: (r) => r.insiderName,
  },
  {
    key: "role",
    header: "Role",
    width: "minmax(0, 1fr)",
    cell: (r) => <span className="text-muted-foreground">{titleCase(r.roleCategory)}</span>,
    sortValue: (r) => r.roleCategory ?? "",
  },
  {
    key: "shares",
    header: "Shares",
    width: "88px",
    align: "end",
    cell: (r) => <span className="tabular-nums">{fmtInt(r.shares)}</span>,
    sortValue: (r) => r.shares ?? 0,
  },
  {
    key: "price",
    header: "Price",
    width: "80px",
    align: "end",
    cell: (r) => (
      <span className="tabular-nums">
        {r.pricePerShare != null ? `$${r.pricePerShare.toFixed(2)}` : "—"}
      </span>
    ),
    sortValue: (r) => r.pricePerShare ?? 0,
  },
  {
    key: "value",
    header: "Value",
    width: "92px",
    align: "end",
    // `total_value` is always stored positive, so the direction has to come from
    // the code. A sale rendered as "+$4.9M" in green reads as money coming in.
    cell: (r) => <TransactionValue tx={r} />,
    sortValue: (r) => (r.transactionCode === "S" ? -(r.totalValue ?? 0) : r.totalValue ?? 0),
  },
  {
    key: "flags",
    header: "Flags",
    width: "minmax(0, 1fr)",
    cell: (r) => {
      const flags: string[] = [];
      if (r.transactionCode === "P") {
        if (r.isRoutine === true) flags.push("routine");
        else if (r.isRoutine === false) flags.push("opportunistic");
      }
      if (r.is10b51) flags.push("10b5-1");
      if (r.isDirect === false) flags.push("indirect");
      return (
        <span className="truncate text-xs text-muted-foreground">
          {flags.length > 0 ? flags.join(" · ") : "—"}
        </span>
      );
    },
  },
];

const signalColumns: Column<TickerSignal>[] = [
  {
    key: "date",
    header: "Signal date",
    width: "minmax(0, 1fr)",
    cell: (r) => (
      <Link href={`/?day=${r.signalDate}`} className="underline-offset-2 hover:underline">
        {fmtDate(r.signalDate, { withYear: true })}
      </Link>
    ),
    sortValue: (r) => r.signalDate,
  },
  {
    key: "type",
    header: "Type",
    width: "minmax(0, 1fr)",
    cell: (r) => <SignalTypeBadge type={r.signalType} />,
    sortValue: (r) => r.signalType,
  },
  {
    key: "score",
    header: "Score",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => <span className="font-mono tabular-nums">{r.score}/100</span>,
    sortValue: (r) => r.score,
  },
  {
    key: "cluster",
    header: "Cluster",
    width: "minmax(0, 1fr)",
    align: "end",
    cell: (r) => (
      <span className="text-muted-foreground">{r.clusterFlag ? "yes" : "—"}</span>
    ),
    sortValue: (r) => (r.clusterFlag ? 1 : 0),
  },
];
