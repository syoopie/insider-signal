"""
The routine-buyer disqualifier, decided over the archive's decade.

Cohen, Malloy & Pomorski (2012): a purchase in the same calendar month in 2 of
the 3 prior years is routine and carries no alpha. `store._compute_is_routine`
answers that against Neon, which retains 48 months and so cannot reliably reach
the 3 years the check itself looks back. It returns None rather than guess, so a
large share of stored purchases carry NULL. The stored flag is therefore a fact
about the ingest window, not about the filing.

`data/form4/` reaches back to 2016, so the same rule decided over it leaves far
fewer rows undetermined. That is the whole reason this exists.

None (pd.NA) means undetermined: no prior year had history old enough to judge.
It is not False and must not be filled in as False.
"""

from __future__ import annotations

import pandas as pd


def routine_flags(purchases: pd.DataFrame) -> pd.Series:
    """
    A `boolean` Series aligned to `purchases.index`, from `cik`, `insider_name`
    and `transaction_date`.

    Pass the *whole* archive. Each row is judged against every purchase the
    frame holds for its (issuer, insider), so a subset silently shortens the
    history every row can see and turns decided rows into pd.NA.
    """
    dates = pd.to_datetime(purchases["transaction_date"], errors="coerce")
    names = purchases["insider_name"].fillna("").astype(str)
    usable = dates.notna() & names.ne("")

    known = dates[usable]
    known_ciks = purchases["cik"][usable]
    known_names = names[usable]

    # A (year, month) key has no month end to get wrong. The database version
    # needs calendar.monthrange because hardcoding day 28 skipped purchases on
    # the 29th-31st and undercounted routine traders in 11 months of 12.
    bought_in = set(zip(known_ciks.to_numpy(), known_names.to_numpy(),
                        known.dt.year.to_numpy(), known.dt.month.to_numpy()))
    earliest = known.groupby([known_ciks, known_names]).min().to_dict()

    flags = []
    for cik, name, when, ok in zip(purchases["cik"], names, dates, usable):
        if not ok:
            flags.append(pd.NA)
            continue
        oldest = earliest.get((cik, name))
        routine = determined = 0
        for years_back in (1, 2, 3):
            year = when.year - years_back
            # The database asks `oldest > date(year, 12, 31)`, which is this.
            if oldest is None or oldest.year > year:
                continue
            determined += 1
            routine += (cik, name, year, when.month) in bought_in
        flags.append(routine >= 2 if determined else pd.NA)

    return pd.Series(pd.array(flags, dtype="boolean"), index=purchases.index,
                     name="is_routine")
