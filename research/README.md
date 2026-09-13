# research/

Offline research. The pipeline never imports this package, and the scheduled workflows install
without its dependency group. `uv sync` installs it locally (DuckDB and pyarrow).

Run the scripts from the repository root: `uv run python research/scripts/<name>.py`. They read
and write `data/prices/` and `data/form4/`, which are rebuilt locally and never committed.

What the runs found is in [`docs/findings.md`](../docs/findings.md).

## Rules

1. **Ask what the ruler can resolve before believing a result.** `hillclimb.py --mde` prints it.
   A result under the resolution is unmeasured, not zero.
2. **Ask about exclusions before rankings.** `gates.py` spends every row, so it resolves far
   finer than a ranking.
3. **Hypotheses go in `candidates.py` and `gates.py`.** Changing `walkforward.py` invalidates
   every number it has printed.
4. **Every definition comes from production.** The rollup is `src/db/purchases.py`, the score is
   `src/signals/batch.score_purchase`, and price context is `src/market/context.py`. A second
   copy here would make research disagree with the pipeline without anyone noticing.

## Modules

| Module | What it holds |
| --- | --- |
| `walkforward.py` | The frozen ruler: folds, rank IC, selection alpha, class alpha, permutation nulls, minimum detectable effect |
| `protocol.py` | The primary horizon, the label families, and which rows may carry a verdict |
| `candidates.py` | Ranking hypotheses, raced by `hillclimb.py` |
| `gates.py` | Exclusion hypotheses and the shipped disqualifiers, raced by `gates.py` |
| `models.py`, `estimate.py`, `features.py` | The fitted forms and feature lists the candidates use |
| `tier1.py` | Insider features: net demand, track record, averaging down, cluster intensity, roster |
| `archive.py` | `data/form4/` presented as the tables the production rollup reads, in DuckDB |
| `dera.py` | SEC DERA quarterly Form 3/4/5 datasets into archive rows |
| `routine.py` | The routine-buyer rule decided over the archive's decade |
| `sectors.py` | SIC code to SPDR sector fund |

## Build order

| Step | Script | What it does |
| --- | --- | --- |
| 1 | `build_form4_archive.py` | Form 4 history into `data/form4/` from DERA quarterly zips. Resumable through a manifest. |
| 2 | `verify_form4_archive.py`, `verify_archive_rollup.py` | Prove the archive matches the database on the window they share, raw and rolled up. Exit non-zero if not. |
| 3 | `build_price_panel.py` | Daily adjusted prices for every ticker with a purchase, plus benchmarks and sector funds, into `data/prices/panel.parquet`. Resumable. `--coverage` reports without fetching. |
| 4 | `verify_price_panel.py` | Prove the panel reproduces the network-measured backtest. |
| 5 | `build_research_dataset.py` | One labelled, scored row per insider purchase-day, including the LOW class the signals table never stores. `--source archive` builds from the decade instead of the database. |
| 6 | `verify_scoring_parity.py` | Prove the dataset scores purchases the way the stored signals were scored. Exits non-zero below 99% agreement. |
| 7 | `hillclimb.py`, `gates.py` | The rulers. `--label {spy,iwm,vol,sector}` chooses the benchmark; results append to `data/prices/`. |
| | `insider_control.py` | The placebo control: the discount screen on stocks nobody bought. |
