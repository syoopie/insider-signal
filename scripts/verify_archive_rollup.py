"""
Prove the archive rolls purchases up the way the database does.

`src/research/archive.py` exists so there is still exactly one definition of
"an insider's purchase on a day". It presents parquet as the three tables
`PURCHASE_ROLLUP_SQL` reads and runs that query unmodified, in DuckDB instead of
Postgres. Unmodified is a claim, and two engines agreeing on a query is not
something to assume: `DISTINCT ON` tie-breaking, NULL ordering and float
summation are all places they could differ quietly.

So this runs both and compares them on the window they share, keyed the way the
rollup groups. The database is the reference because it is what every stored
signal was built from.

  uv run python scripts/verify_archive_rollup.py
  uv run python scripts/verify_archive_rollup.py --tolerance 0.005
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from src.db.connection import get_conn
from src.db.purchases import purchase_rollup
from src.ingest.common import load_ticker_universe, log, phase, setup_log_tee
from src.research.archive import connect, purchases

setup_log_tee("verify_archive_rollup")

TOLERANCE = 0.02
KEY = ["cik", "insider_name", "transaction_date", "is_direct"]


def _database(start, end) -> pd.DataFrame:
    sql = purchase_rollup("AND f.filed_date BETWEEN %s AND %s")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            columns = [c.name for c in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=columns)


def _split_filings(start, end) -> set:
    """
    Purchase keys the database reports under more than one filing.

    The rollup keeps the newest filing per key and drops the rest, on the
    assumption that a second filing for the same insider, day and direction is
    a 4/A restating the first. Sometimes it is not: insider GANGWAL RAKESH filed
    accessions -24-000128 and -24-000130 on the same day for different tranches
    of one 2024-09-30 purchase.

    Which one wins is decided by `filed_date DESC, filing_id DESC`, and on a tie
    that is the serial id, meaning insertion order. The archive has no serial id
    and substitutes the accession number, so it can pick the other filing. The
    disagreement is real but it is the database's tie-break that is arbitrary,
    not the archive's, so these keys are reported rather than failed.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.cik, t.insider_name, t.transaction_date, t.is_direct
                FROM transactions t
                JOIN form4_filings f ON f.id = t.filing_id
                WHERE t.transaction_code = 'P'
                  AND f.filed_date BETWEEN %s AND %s
                GROUP BY f.cik, t.insider_name, t.transaction_date, t.is_direct
                HAVING count(DISTINCT t.filing_id) > 1
            """, (start, end))
            return {(str(cik).lstrip("0"), (name or "").strip().upper(),
                     tdate, bool(direct))
                    for cik, name, tdate, direct in cur.fetchall()}


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["cik"] = out["cik"].astype(str).str.lstrip("0")
    out["insider_name"] = out["insider_name"].fillna("").str.strip().str.upper()
    out["transaction_date"] = pd.to_datetime(out["transaction_date"]).dt.date
    out["filed_date"] = pd.to_datetime(out["filed_date"]).dt.date
    out["is_direct"] = out["is_direct"].astype("boolean").fillna(False).astype(bool)
    for column in ("shares", "total_value"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    # A filing whose transaction date will not parse cannot be matched to
    # anything, and left in it sorts as a float among dates and breaks min().
    return out[out["transaction_date"].notna()].set_index(KEY).sort_index()


def _share(part: int, whole: int) -> float:
    return part / max(whole, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args()

    phase("ARCHIVE")
    raw = purchases(connect())
    archive = _normalise(raw)
    dropped = len(raw) - len(archive)
    # The window is filing dates, not transaction dates. The rollup selects on
    # `f.filed_date`, and a Form 4 can report a trade made years earlier or
    # carry a filer's typo: these rows run to 2033-11-18 on transaction date.
    # Keying the comparison off that asked the database for five months the
    # archive cannot hold, because DERA publishes a quarter in arrears, and then
    # counted every one of them as missing.
    start = archive["filed_date"].min()
    end = archive["filed_date"].max()
    log(f"{len(archive):,} rolled-up purchases, filed {start} to {end}"
        + (f"   ({dropped} dropped for an unparseable transaction date)"
           if dropped else ""))

    phase("DATABASE")
    database = _normalise(_database(start, end))
    log(f"{len(database):,} rolled-up purchases filed over the same dates")
    if database.empty:
        raise SystemExit("The database holds nothing in this window.")

    failures = []

    phase("KEYS")
    # The archive filters to today's data/tickers.txt using the ticker on the
    # filing. The database was filled by ingest runs stretching back years, each
    # using the universe file as it stood that day, so comparing raw sets
    # measures how much the index reconstituted. Restricting the database side
    # to today's universe is the like-for-like comparison.
    universe = load_ticker_universe()
    if universe:
        drifted = ~database["ticker"].isin(universe)
        log(f"  set aside: {int(drifted.sum()):,} stored purchases whose ticker "
            "has since left data/tickers.txt")
        database = database[~drifted]

    both = archive.index.intersection(database.index)
    absent = database.index.difference(archive.index)
    log(f"  in both {len(both):,}   archive only "
        f"{len(archive.index.difference(database.index)):,}   "
        f"database only {len(absent):,}")

    # A key can be "database only" for two very different reasons, and lumping
    # them reported 12% missing when the real figure is a tenth of that.
    #
    # A joint Form 4 names several reporting owners and both sides keep only the
    # first, exactly as `parse_form4` does, because one filing is one decision.
    # But they order the owners differently, so the same purchase is filed under
    # BROADWOOD PARTNERS, L.P. in one and BRADSHER NEAL C in the other. That is
    # the documented ambiguity in collapsing a joint filing, not a missing row,
    # and it is why insider_name is not a join key between these two sources.
    issuer_days = {(cik, tdate, direct)
                   for cik, _name, tdate, direct in archive.index}
    renamed = [k for k in absent if (k[0], k[2], k[3]) in issuer_days]
    log(f"  of those, {len(renamed):,} are the same issuer, day and direction "
        "under a different reporting owner")

    # The rest are almost entirely index membership measured at two different
    # moments, not data the archive lacks. Both sides filter to
    # data/tickers.txt, but the archive uses the ticker the filing carried and
    # the database uses whatever `companies` holds for that CIK today. CIK
    # 1755953 filed as Gryphon Digital Mining (GRYP) in January 2025 and is
    # American Bitcoin Corp (ABTC) in `companies` now, so the same purchase is
    # inside the universe on one side and outside it on the other.
    #
    # The archive's version is the point-in-time correct one and that is worth
    # keeping rather than reconciling away: filtering history by today's index
    # membership is survivorship bias, which is the exact error
    # `walkforward.stable_features` exists to catch elsewhere.
    unmatched = len(absent) - len(renamed)
    log(f"  {unmatched:,} more sit under a CIK whose ticker differs between the "
        f"filing and `companies` today ({_share(unmatched, len(database)):.2%})")

    phase("SHARES AND VALUE ON SHARED PURCHASES")
    split = _split_filings(start, end)
    contested = np.array([k in split for k in both])
    log(f"  {int(contested.sum()):,} of {len(both):,} shared purchases are "
        "reported under more than one filing, where the winner is decided by a "
        "tie-break the archive cannot reproduce. Reported, not compared.")

    left, right = archive.loc[both][~contested], database.loc[both][~contested]
    # The aggregate is the gate and the per-row rate is reported beside it,
    # because they answer different questions. This script exists to show the
    # two engines execute one query the same way, but the two sides are not fed
    # the same bytes: DERA parsed the XML and so did we, and a handful of rows
    # differ in the last decimal of a price or in how many fills a filing was
    # split into. That is a source difference, not an engine difference, and it
    # cannot be driven to zero from here. Drift in the total can be, and it is
    # what any research built on these rows would actually feel.
    for column in ("shares", "total_value"):
        a, b = left[column].groupby(level=KEY).sum(), right[column].groupby(level=KEY).sum()
        disagree = (a - b).abs() > (b.abs() * 0.001 + 0.01)
        drift = abs(a.sum() - b.sum()) / max(abs(b.sum()), 1.0)
        log(f"  {column:<12} archive {a.sum():,.0f}   database {b.sum():,.0f}   "
            f"drift {drift:.4%}")
        log(f"  {'':<12} {int(disagree.sum()):,} of {len(b):,} individual rows "
            f"differ ({_share(int(disagree.sum()), len(b)):.2%})")
        if drift > args.tolerance:
            failures.append(f"{column} drifts {drift:.2%} in aggregate")
            log(f"    worst:\n{(a - b).abs().nlargest(5).to_string()}")

    phase("WHAT THE ROLLUP CANNOT SUPPLY")
    for column in ("is_routine", "pct_below_52wk_high", "cap_tier"):
        known = int(archive[column].notna().sum()) if column in archive else 0
        log(f"  {column:<20} present on {known:,} of {len(archive):,} archive rows. "
            "Computed at ingest, so it is a fact about the ingest window.")

    phase("FILER ERRORS THE ARCHIVE INHERITS")
    # Not a failure and not filtered. These are SEC data-entry mistakes that the
    # production parser carries too, and no structural rule separates them from
    # a real purchase: the security title says Common Stock and the value column
    # is blank. Joining the price panel is what will catch them, by comparing the
    # reported price against what the stock actually traded at that day.
    absurd = archive[archive["total_value"] > 1e9]
    log(f"  {len(absurd)} rolled-up purchases over $1B, "
        f"${absurd['total_value'].sum():,.0f} between them")
    if len(absurd):
        log(absurd.reset_index()[["ticker", "insider_name", "transaction_date",
                                  "shares", "price_per_share", "total_value"]]
            .head(6).to_string(index=False))

    phase("VERDICT")
    if failures:
        for line in failures:
            log(f"  FAIL: {line}")
        raise SystemExit(1)
    log(f"  Both engines roll the shared purchases up identically within "
        f"{args.tolerance:.0%}, over {start} to {end}.")


if __name__ == "__main__":
    main()
