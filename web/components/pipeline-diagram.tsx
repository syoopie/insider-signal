/**
 * The pipeline, end to end.
 *
 * Inline SVG rather than an image so it inherits the theme's colours and stays
 * sharp; every label names a real file in the repository, so the diagram is
 * checkable against the code rather than decorative.
 */
const STAGES = [
  { file: "SEC EDGAR", label: "Form 4 filings", note: "Filed within 2 business days of the trade" },
  { file: "src/ingest/edgar.py", label: "Fetch", note: "8 requests/second, retried with backoff" },
  { file: "src/ingest/parser.py", label: "Parse", note: "Table I only; roles normalised from free text" },
  { file: "src/ingest/store.py", label: "Store", note: "Companies, filings, transactions" },
  { file: "src/signals/scorer.py", label: "Score", note: "Open-market purchases only, 0–100" },
  { file: "src/signals/cluster.py", label: "Cluster", note: "3+ insiders inside 14 days" },
  { file: "signals table", label: "Signal", note: "Dated filing date + 1" },
];

export function PipelineDiagram() {
  return (
    <div className="overflow-x-auto rounded-xl border bg-card p-5">
      <ol className="flex min-w-max items-stretch gap-2">
        {STAGES.map((s, i) => (
          <li key={s.file} className="flex items-stretch gap-2">
            <div className="flex w-44 flex-col rounded-lg border bg-background p-3">
              <span className="text-xs font-semibold">{s.label}</span>
              <code className="mt-0.5 truncate text-[11px] text-muted-foreground" title={s.file}>
                {s.file}
              </code>
              <span className="mt-1.5 text-[11px] leading-snug text-muted-foreground text-pretty">
                {s.note}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <div className="flex items-center" aria-hidden>
                <svg width="14" height="10" viewBox="0 0 14 10" className="text-muted-foreground">
                  <path
                    d="M0 5h10M8 2l3 3-3 3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
            )}
          </li>
        ))}
      </ol>

      <div className="mt-4 grid gap-3 border-t pt-4 sm:grid-cols-3">
        <Branch
          title="Telegram alert"
          detail="BUY and CLUSTER_BUY only, once per signal."
          file="src/alerts/telegram.py"
        />
        <Branch
          title="This dashboard"
          detail="Read-only. It never writes to the database."
          file="web/"
        />
        <Branch
          title="Weekly backtest"
          detail="Sundays 12:00 UTC. Re-scores 730 days of signals against SPY."
          file="src/backtest/engine.py"
        />
      </div>
    </div>
  );
}

function Branch({ title, detail, file }: { title: string; detail: string; file: string }) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <p className="text-xs font-semibold">{title}</p>
      <code className="text-[11px] text-muted-foreground">{file}</code>
      <p className="mt-1 text-[11px] leading-snug text-muted-foreground text-pretty">{detail}</p>
    </div>
  );
}
