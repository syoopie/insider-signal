"""
Rebuild the stored signals for every filing disclosed in a date range.

The daily ingest rebuilds the last week and `scripts/backfill_signals.py`
rebuilds whatever range it is given. Both call `rebuild_signals`, so a stored
signal is the same row whichever of them wrote it.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.db.purchases import purchases_by_ticker, work_items
from src.db.signals import Replaced, replace_signals
from src.db.store import get_discount_reference, get_history_start
from src.signals.batch import Signal, build_signal


@dataclass(frozen=True)
class Rebuild:
    signals: list[Signal]
    ineligible: int
    replaced: Optional[Replaced]

    def counts(self) -> Counter:
        return Counter(s.signal_type for s in self.signals)


def rebuild_signals(start: date, end: date, dry_run: bool = False) -> Rebuild:
    """
    Score every (filed_date, ticker) with a purchase disclosed in [start, end] and,
    unless `dry_run`, replace the stored signals for that range with the result.

    LOW signals are built and counted but never stored.
    """
    items = work_items(start, end)
    by_ticker = purchases_by_ticker(sorted({ticker for _, ticker in items}))
    history_start = get_history_start()

    signals, ineligible = [], 0
    for filed_date, ticker in items:
        signal = build_signal(ticker, by_ticker.get(ticker, []), filed_date,
                              history_start, get_discount_reference)
        if signal is None:
            ineligible += 1
        else:
            signals.append(signal)

    replaced = None
    if not dry_run:
        replaced = replace_signals(start, end, [s for s in signals if s.signal_type != "LOW"])
    return Rebuild(signals, ineligible, replaced)
