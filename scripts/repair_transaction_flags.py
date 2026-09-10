"""
Re-derive `transactions.is_10b51` and `transactions.is_routine` from the source.

Two of the scorer's four hard disqualifiers read stored columns that do not
describe the filings they claim to. Both are written once at ingest and never
revisited, so every row carries whatever answer the pipeline could give on the
day it was written.

**`is_10b51` predates the parser that computes it.** `parser._tx_is_10b51`
landed on 2026-08-29 in 301ef74. Before that the parser read
`transactionFormType` (whose value is "4") and `transactionTimeliness` (a code
like "E"), so both checks were dead and the only working mechanism was a
substring scan of the whole document. That scan is wrong in both directions.
Accession 0001140361-24-043157 (ANGO) carries `<aff10b5One>true</aff10b5One>`
and is stored False; 0000950170-25-081785 (TKO) has a plan footnote on its
*sales* and its $250M open-market purchase is stored True.

**`is_routine` is a fact about the ingest window.** `store._compute_is_routine`
looks back three years and returns None rather than guess when the database
cannot see that far. `prune_old_data` then deletes the history it read, so a
stored False can no longer be reproduced by the function that wrote it.
`src/research/routine.py` answers the same rule over `data/form4/`, which
reaches back to 2016.

**The stored routine flag is merged, not replaced, and the reason is measured.**
Answered over the database alone today, 8,949 of 13,433 purchases come back
undetermined. Adding the archive brings that to 5,565 and reproduces 411 of the
528 stored True values against 14. But 3,831 stored False values still land on
undetermined, because the DB decided them at ingest against months that pruning
has since deleted and DERA's universe filter never held. Writing that verbatim
would leave the column *less* determined than it is now. The rule is monotone
instead: a same-month purchase, once found, cannot be un-found, and a narrower
window can only under-count it. So `merge_routine` keeps True over False and
either over undetermined, and the archive's contribution is only ever addition.

**`bootstrap.py --force` cannot fix either.** `store.write_filing` runs
`INSERT INTO form4_filings ... ON CONFLICT DO NOTHING RETURNING id` and returns
`(0, 0)` when the filing already exists, so it re-fetches the XML and throws it
away without touching a transaction row.

Nothing is written without `--apply`. The dry run prints the eligibility delta
so a human can see what changes before Neon does.

  uv run python scripts/repair_transaction_flags.py            # dry run
  uv run python scripts/repair_transaction_flags.py --limit 50 # short fetch pass
  uv run python scripts/repair_transaction_flags.py --apply    # write it

The EDGAR pass caches each filing's re-parsed transactions in
`data/repair/form4_reparse.jsonl`, appended and flushed per filing. An
interrupted run loses nothing, a re-run skips what it already has, and `--apply`
replays the same cache the dry run reported from rather than re-fetching and
possibly writing something the human never saw. `--refetch` discards it.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from src.db.connection import get_conn
from src.ingest.common import fmt_elapsed, log, phase, setup_log_tee
from src.ingest.edgar import EdgarBlockedError, EdgarRateLimitError, fetch_filing_xml
from src.ingest.parser import parse_form4
from src.research.archive import connect
from src.research.routine import routine_flags
from src.signals.scorer import score_transaction

setup_log_tee("repair_transaction_flags")

CACHE = Path("data/repair/form4_reparse.jsonl")

# 8 req/sec is the ingest budget; a filing aged out of the submissions API costs
# three requests, so the pool has to be wide enough to keep that budget spent at
# EDGAR's several-hundred-millisecond latency.
RATE = 8.0
WORKERS = 12

SAMPLE_ROWS = 10


@dataclass(frozen=True)
class StoredRow:
    tx_id: int
    ticker: Optional[str]
    insider_name: Optional[str]
    transaction_date: Optional[date]
    transaction_code: str
    shares: Optional[float]
    price_per_share: Optional[float]
    total_value: Optional[float]
    is_direct: bool
    is_10b51: bool
    is_routine: Optional[bool]


@dataclass(frozen=True)
class Filing:
    """A stored filing and every Table I row it holds, in insertion order.

    All of them, not just the purchases: `parse_form4` returns the whole table,
    and matching a purchase against it means knowing what sits either side.
    """
    accession: str
    cik: str
    rows: tuple[StoredRow, ...]


# ── The row key ───────────────────────────────────────────────────────────────

def _num(value) -> Optional[float]:
    """psycopg2 hands back Decimal and the parser hands back float."""
    if value is None:
        return None
    return round(float(value), 6)


def _day(value) -> Optional[str]:
    return None if value is None else str(value)[:10]


def stored_key(row: StoredRow) -> tuple:
    return (_day(row.transaction_date), (row.transaction_code or "").upper(),
            _num(row.shares), _num(row.price_per_share), bool(row.is_direct))


def parsed_key(tx: dict) -> tuple:
    return (_day(tx.get("transaction_date")), (tx.get("transaction_code") or "").upper(),
            _num(tx.get("shares")), _num(tx.get("price_per_share")),
            bool(tx.get("is_direct", True)))


def match_rows(stored_keys: list, parsed_keys: list) -> Optional[list]:
    """
    For each stored row, the index of the parsed row it is, or None for no match.

    Both sides come out of the same parser, so they should correspond one to
    one. Should is not proof, and a wrong correspondence writes a plan flag onto
    somebody else's purchase, so this refuses rather than guesses.

    Where the key is unique on both sides it identifies the row outright, and a
    parsed row with no stored counterpart is fine: `purge_debt_transactions.py`
    deleted rows the parser now skips anyway. Where a key repeats, only order
    can separate the duplicates, and order is trustworthy only if both sequences
    agree element for element.
    """
    unique_both = (len(set(stored_keys)) == len(stored_keys)
                   and len(set(parsed_keys)) == len(parsed_keys))
    if unique_both:
        position = {key: i for i, key in enumerate(parsed_keys)}
        if all(key in position for key in stored_keys):
            return [position[key] for key in stored_keys]
        return None
    if stored_keys == parsed_keys:
        return list(range(len(stored_keys)))
    return None


# ── Eligibility ───────────────────────────────────────────────────────────────

def is_eligible(row: StoredRow) -> bool:
    """
    Whether `score_transaction` would let this row through its disqualifiers.

    Calls the scorer rather than restating its ladder, so the delta this script
    reports cannot drift from the rule the pipeline applies. Nothing but the
    disqualified fields is supplied: the price context decides the rank, and the
    rank is not what this measures.
    """
    verdict = score_transaction(
        {
            "transaction_code": row.transaction_code,
            "transaction_date": row.transaction_date,
            "is_10b51": row.is_10b51,
            "total_value": row.total_value,
            "is_routine": row.is_routine,
            "pct_below_52wk_high": None,
        },
        owner={}, company={}, market_data={}, prior_purchases=[],
    )
    return verdict is not None and not verdict["disqualified"]


# ── Reading what is stored ────────────────────────────────────────────────────

_STORED_SQL = """
SELECT f.accession_number, f.cik, c.ticker,
       t.id, t.insider_name, t.transaction_date, t.transaction_code,
       t.shares, t.price_per_share, t.total_value,
       t.is_direct, t.is_10b51, t.is_routine
FROM transactions t
JOIN form4_filings f ON f.id = t.filing_id
LEFT JOIN companies c ON c.cik = f.cik
WHERE t.filing_id IN (SELECT filing_id FROM transactions WHERE transaction_code = 'P')
ORDER BY f.accession_number, t.id
"""


def load_filings() -> list:
    """Every filing holding at least one purchase, rows in insertion order.

    Insertion order is document order: `write_filing` inserts one filing's rows
    with a single `executemany`, so ascending id is the order `parse_form4`
    returned them in. That is what the positional fallback in `match_rows`
    rests on.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_STORED_SQL)
            rows = cur.fetchall()

    filings, current, accession, cik = [], [], None, None
    for row in rows:
        if row[0] != accession:
            if accession is not None:
                filings.append(Filing(accession, cik, tuple(current)))
            accession, cik, current = row[0], row[1], []
        current.append(StoredRow(
            tx_id=row[3], ticker=row[2], insider_name=row[4],
            transaction_date=row[5], transaction_code=row[6] or "",
            shares=_num(row[7]), price_per_share=_num(row[8]),
            total_value=_num(row[9]),
            is_direct=bool(row[10]), is_10b51=bool(row[11]), is_routine=row[12],
        ))
    if accession is not None:
        filings.append(Filing(accession, cik, tuple(current)))
    return filings


# ── The EDGAR pass ────────────────────────────────────────────────────────────

def load_cache(path: Path) -> dict:
    """{accession: [parsed transaction, ...]} from previous passes."""
    if not path.exists():
        return {}
    cached = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cached[record["accession"]] = record["transactions"]
    return cached


def _reparse(filing: Filing) -> Optional[list]:
    """This filing's Table I as EDGAR serves it today, or None if unreachable.

    The issuer CIK is what `form4_filings` stores and EDGAR serves an accession
    under every CIK on it, so the archive path resolves without the filer's.
    """
    xml = fetch_filing_xml(filing.accession, filing.cik, req_per_sec=RATE)
    if not xml:
        return None
    parsed = parse_form4(xml, {"accession_number": filing.accession})
    if not parsed:
        return None
    return parsed.get("transactions", [])


def fetch_missing(filings: list, cached: dict, limit: Optional[int]) -> tuple:
    """
    Re-parse every filing not already cached. Returns (cached, aborted, failed).

    A 429 means EDGAR has had enough, and an earlier bulk fetch that kept going
    lost the twenty-two minutes it had already spent. Everything finished is on
    disk, so stopping costs only the filings in flight.

    A filing that will not fetch is not cached as a failure. A re-run retries a
    handful of them for a few seconds; a cached 404 would silently leave those
    rows unrepaired for good.
    """
    todo = [f for f in filings if f.accession not in cached]
    if limit is not None:
        todo = todo[:limit]
    log(f"{len(cached):,} filings already re-parsed, {len(todo):,} to fetch")
    if not todo:
        return cached, False, 0

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    started, done, failed, aborted = time.time(), 0, 0, False

    with CACHE.open("a", encoding="utf-8") as sink:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_reparse, f): f for f in todo}
            for future in as_completed(futures):
                filing = futures[future]
                try:
                    transactions = future.result()
                except (EdgarRateLimitError, EdgarBlockedError) as error:
                    log(f"STOPPING: {error}")
                    aborted = True
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                except Exception as error:
                    log(f"  {filing.accession}: {type(error).__name__}: {error}")
                    failed += 1
                    continue
                if transactions is None:
                    failed += 1
                    continue
                sink.write(json.dumps({"accession": filing.accession,
                                       "transactions": transactions}) + "\n")
                sink.flush()
                cached[filing.accession] = transactions
                done += 1
                if done % 250 == 0:
                    rate = done / max(time.time() - started, 1e-9)
                    left = (len(todo) - done - failed) / max(rate, 1e-9)
                    log(f"  {done:,}/{len(todo):,} re-parsed  "
                        f"{rate:.1f}/s  eta {fmt_elapsed(left)}")

    log(f"re-parsed {done:,}, unreachable {failed:,}"
        + ("  (ABORTED — re-run to finish)" if aborted else ""))
    return cached, aborted, failed


# ── The two repairs ───────────────────────────────────────────────────────────

def repair_10b51(filings: list, cached: dict) -> tuple:
    """({tx_id: is_10b51}, filings matched, filings refused)."""
    flags, matched, unmatched = {}, 0, []
    for filing in filings:
        parsed = cached.get(filing.accession)
        if parsed is None:
            continue
        mapping = match_rows([stored_key(r) for r in filing.rows],
                             [parsed_key(t) for t in parsed])
        if mapping is None:
            unmatched.append(filing)
            continue
        matched += 1
        for row, index in zip(filing.rows, mapping):
            flags[row.tx_id] = bool(parsed[index].get("is_10b51", False))
    return flags, matched, unmatched


_ARCHIVE_SQL = """
SELECT f.accession_number, f.cik, t.insider_name, t.transaction_date
FROM transactions t
JOIN form4_filings f ON f.id = t.filing_id
WHERE t.transaction_code = 'P'
"""


def _routine_key(cik, name, when):
    """The grain the rule is defined at, normalised so both sources join.

    DERA and the parser read the same names out of the same filings but not
    always in the same case, and a name that fails to join shortens the history
    its own purchases are judged against.
    """
    return (str(cik).lstrip("0"), str(name or "").strip().upper(), when)


def repair_routine(filings: list) -> dict:
    """
    {tx_id: is_routine} decided over the archive's decade plus the recent months.

    DERA stops a quarter in arrears and the database runs to today, so neither
    source can decide the rule alone. `routine_flags` wants the whole frame:
    judging a subset shortens the history every row can see and turns decided
    rows into pd.NA, which is the failure this repair exists to undo.
    """
    stored = pd.DataFrame(
        [(f.accession, f.cik, r.insider_name, r.transaction_date)
         for f in filings for r in f.rows if r.transaction_code.upper() == "P"],
        columns=["accession_number", "cik", "insider_name", "transaction_date"],
    )
    archived = connect().execute(_ARCHIVE_SQL).fetch_df()
    archived = archived[~archived["accession_number"].isin(set(stored["accession_number"]))]
    log(f"  {len(stored):,} stored purchases, {len(archived):,} archived purchases "
        "under an accession the database does not hold")

    union = pd.concat([stored, archived], ignore_index=True)
    union["cik"] = union["cik"].astype(str).str.lstrip("0")
    union["insider_name"] = union["insider_name"].fillna("").astype(str).str.strip().str.upper()
    union["transaction_date"] = pd.to_datetime(union["transaction_date"], errors="coerce").dt.date

    flags = routine_flags(union)
    decided = {}
    for key, flag in zip(zip(union["cik"], union["insider_name"], union["transaction_date"]),
                         flags):
        decided[key] = None if flag is pd.NA else bool(flag)

    return {row.tx_id: decided.get(_routine_key(filing.cik, row.insider_name,
                                                row.transaction_date))
            for filing in filings for row in filing.rows
            if row.transaction_code.upper() == "P"}


def merge_routine(stored: Optional[bool], recomputed: Optional[bool]) -> Optional[bool]:
    """
    Two windows onto one rule, combined as evidence rather than as answers.

    "Bought this calendar month in 2 of the 3 prior years" is monotone in the
    purchases you can see: more history finds more same-month years, never
    fewer. So a True from either window stands, a False beats undetermined
    whichever window supplied it, and undetermined survives only when neither
    window could reach a prior year at all.
    """
    if stored is True or recomputed is True:
        return True
    if stored is False or recomputed is False:
        return False
    return None


def repaired(filings: list, flags_10b51: dict, flags_routine: dict) -> list:
    """Every stored purchase as it would be after the repair."""
    out = []
    for filing in filings:
        for row in filing.rows:
            if row.transaction_code.upper() != "P":
                continue
            out.append(replace(
                row,
                is_10b51=flags_10b51.get(row.tx_id, row.is_10b51),
                is_routine=merge_routine(row.is_routine, flags_routine.get(row.tx_id)),
            ))
    return out


# ── The report ────────────────────────────────────────────────────────────────

def _label(value) -> str:
    return "NULL" if value is None else str(value)


def _confusion(before: list, after: list, attribute: str) -> None:
    counts = {}
    for old, new in zip(before, after):
        key = (getattr(old, attribute), getattr(new, attribute))
        counts[key] = counts.get(key, 0) + 1
    for (old, new), n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        marker = "" if old == new else "   <-- changed"
        log(f"    {_label(old):>5} -> {_label(new):<5} {n:>7,}{marker}")


def _signals_at_risk(changed: list) -> None:
    """
    Signals dated to a purchase whose eligibility moves.

    `signal_date` is `tx_rows[0].transaction_date`, so a signal and the purchase
    it was built from meet on (ticker, date). A cluster signal covers a 14-day
    window and is dated to one member of it, so this counts the signals the
    change lands on directly, not every signal it could ripple into.
    """
    keys = sorted({(r.ticker, r.transaction_date) for r in changed if r.ticker})
    if not keys:
        log("    no signal sits on a purchase whose eligibility changes")
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT signal_type, count(*), count(*) FILTER (WHERE alerted)
                FROM signals
                WHERE (ticker, signal_date) IN (
                    SELECT * FROM unnest(%s::text[], %s::date[])
                )
                GROUP BY signal_type ORDER BY signal_type
                """,
                ([k[0] for k in keys], [k[1] for k in keys]),
            )
            rows = cur.fetchall()
    if not rows:
        log("    no signal sits on a purchase whose eligibility changes")
        return
    for signal_type, n, alerted in rows:
        log(f"    {signal_type:<12} {n:>5,} signals, {alerted:,} already alerted")


def _sample(before: list, after: list) -> None:
    changed = [(old, new) for old, new in zip(before, after)
               if (old.is_10b51, old.is_routine) != (new.is_10b51, new.is_routine)]
    if not changed:
        return
    changed.sort(key=lambda pair: (pair[0].ticker or "", pair[0].transaction_date or date.min,
                                   pair[0].tx_id))
    step = max(len(changed) // SAMPLE_ROWS, 1)
    log(f"    {'ticker':<8}{'insider':<28}{'date':<12}"
        f"{'is_10b51':<20}{'is_routine':<20}")
    for old, new in changed[::step][:SAMPLE_ROWS]:
        log(f"    {(old.ticker or '?'):<8}{(old.insider_name or '?')[:26]:<28}"
            f"{_label(old.transaction_date):<12}"
            f"{_label(old.is_10b51) + ' -> ' + _label(new.is_10b51):<20}"
            f"{_label(old.is_routine) + ' -> ' + _label(new.is_routine):<20}")


def report(filings: list, before: list, after: list, flags_10b51: dict,
           flags_routine: dict, unmatched: list, unreachable: int) -> list:
    """Print every table the dry run owes a reviewer. Returns the changed rows."""
    rows = sum(len(f.rows) for f in filings)
    purchases = len(before)
    derived = sum(1 for r in before if r.tx_id in flags_10b51)

    phase("SCOPE")
    log(f"  {len(filings):,} stored filings hold at least one purchase, "
        f"{rows:,} Table I rows between them, {purchases:,} of them purchases")
    log(f"  {derived:,} purchases re-derived from EDGAR, "
        f"{purchases - derived:,} left as stored")
    log(f"  {len(unmatched):,} filings could not be matched row for row, "
        f"{unreachable:,} could not be fetched this pass")
    if unmatched:
        log("  unmatched filings:")
        for filing in unmatched:
            log(f"    {filing.accession}  {filing.rows[0].ticker or '?':<8}"
                f"{len(filing.rows)} stored rows")

    phase("IS_10B51")
    log("  over the re-derived purchases only:")
    _confusion([r for r in before if r.tx_id in flags_10b51],
               [r for r in after if r.tx_id in flags_10b51], "is_10b51")

    phase("IS_ROUTINE")
    log("  what the database and the archive together can decide, on their own:")
    _confusion(before, [replace(r, is_routine=flags_routine.get(r.tx_id))
                        for r in before], "is_routine")
    log("  merged with what is stored, which is what would be written:")
    _confusion(before, after, "is_routine")

    phase("ELIGIBILITY")
    was = [is_eligible(r) for r in before]
    now = [is_eligible(r) for r in after]
    gained = sum(1 for a, b in zip(was, now) if not a and b)
    lost = sum(1 for a, b in zip(was, now) if a and not b)
    log(f"  eligible before {sum(was):,} of {purchases:,}   "
        f"after {sum(now):,} of {purchases:,}")
    log(f"  become eligible     {gained:,}")
    log(f"  become disqualified {lost:,}")
    log(f"  net                 {gained - lost:+,}")

    changed = [new for a, b, new in zip(was, now, after) if a != b]

    phase("SIGNALS SITTING ON A PURCHASE WHOSE ELIGIBILITY CHANGES")
    _signals_at_risk(changed)

    phase("SAMPLE OF CHANGED ROWS")
    _sample(before, after)
    return changed


# ── Writing ───────────────────────────────────────────────────────────────────

_UPDATE_SQL = """
UPDATE transactions t
SET is_10b51 = v.is_10b51::boolean, is_routine = v.is_routine::boolean
FROM (VALUES %s) AS v(id, is_10b51, is_routine)
WHERE t.id = v.id::int
"""


def write_repair(before: list, after: list) -> int:
    """Write every purchase whose flags moved. Returns the rows updated."""
    from psycopg2.extras import execute_values

    rows = [(new.tx_id, new.is_10b51, new.is_routine)
            for old, new in zip(before, after)
            if (old.is_10b51, old.is_routine) != (new.is_10b51, new.is_routine)]
    if not rows:
        log("  nothing to write")
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, _UPDATE_SQL, rows, page_size=500)
    log(f"  wrote {len(rows):,} rows")
    return len(rows)


def _disqualifier_counts() -> dict:
    """The ladder counted straight out of the database, for the after-apply check."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT is_10b51, is_routine, count(*)
                FROM transactions WHERE transaction_code = 'P'
                GROUP BY 1, 2
                """
            )
            return {(row[0], row[1]): row[2] for row in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--apply", action="store_true",
                        help="write the repair. Without it nothing is mutated.")
    parser.add_argument("--refetch", action="store_true",
                        help="discard the re-parse cache and fetch every filing again")
    parser.add_argument("--limit", type=int,
                        help="fetch at most this many filings this pass")
    args = parser.parse_args()

    if args.refetch and CACHE.exists():
        CACHE.unlink()
        log(f"discarded {CACHE}")

    phase("STORED")
    filings = load_filings()
    log(f"{len(filings):,} filings, {len({f.cik for f in filings}):,} issuers, "
        f"{sum(len(f.rows) for f in filings):,} Table I rows")

    phase("EDGAR")
    cached, aborted, unreachable = fetch_missing(filings, load_cache(CACHE), args.limit)

    phase("IS_10B51 FROM THE SOURCE")
    flags_10b51, matched, unmatched = repair_10b51(filings, cached)
    log(f"  {len(flags_10b51):,} rows re-derived across {matched:,} filings")

    phase("IS_ROUTINE OVER THE ARCHIVE")
    flags_routine = repair_routine(filings)
    undetermined = sum(1 for v in flags_routine.values() if v is None)
    log(f"  {len(flags_routine):,} purchases judged, {undetermined:,} undetermined "
        "before merging with what is stored")

    before = [r for f in filings for r in f.rows if r.transaction_code.upper() == "P"]
    after = repaired(filings, flags_10b51, flags_routine)
    changed = report(filings, before, after, flags_10b51, flags_routine,
                     unmatched, unreachable)

    if not args.apply:
        phase("DRY RUN")
        log(f"  nothing written. {len(changed):,} purchases would change eligibility.")
        log("  re-run with --apply to write it.")
        return

    if aborted or args.limit is not None:
        raise SystemExit(
            "Refusing to write: the EDGAR pass did not finish, so this delta is "
            "not the whole repair. Re-run without --limit until it completes."
        )

    phase("APPLY")
    predicted = {}
    for row in after:
        predicted[(row.is_10b51, row.is_routine)] = \
            predicted.get((row.is_10b51, row.is_routine), 0) + 1
    write_repair(before, after)

    phase("AS WRITTEN")
    actual = _disqualifier_counts()
    for key in sorted(set(predicted) | set(actual), key=lambda k: (str(k[0]), str(k[1]))):
        want, got = predicted.get(key, 0), actual.get(key, 0)
        log(f"  is_10b51={_label(key[0]):<5} is_routine={_label(key[1]):<5} "
            f"predicted {want:>7,}   in the database {got:>7,}"
            + ("" if want == got else "   <-- MISMATCH"))
    if predicted != actual:
        raise SystemExit("The database does not match what the dry run predicted.")
    log("  the database matches the delta that was reviewed.")


if __name__ == "__main__":
    main()
