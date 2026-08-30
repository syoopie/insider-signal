"""
Scoring a window of purchases, in memory.

This was inside `scripts/backfill_signals.py`, where the research tooling could
not reach it. Two callers now need identical answers: the backfill, which writes
the signals the dashboard and Telegram read, and the research dataset builder,
which needs every purchase scored including the ones that classify LOW and are
never stored. A second implementation of "how a purchase is scored" would make
the research disagree with production in ways nobody would notice, which is the
same failure `src/db/purchases.py` exists to prevent for the rollup.

Nothing here reads the network or the database. Callers supply the rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence

from src.signals.scorer import score_transaction

# Purchases disclosed within this many days of each other are scored as one
# window. Mirrors run_ingest, which scores the P transactions from the last week.
SCORING_WINDOW_DAYS = 7


@dataclass
class ScoredWindow:
    """
    What a window of purchases for one ticker came to.

    `aggregate_score` is the highest individual score, which is what the BUY
    threshold is applied to. `participant_scores` is every eligible score, which
    is what the cluster average is taken over. `breakdown` belongs to the single
    highest-scoring purchase — a real limitation, since a five-buyer day is
    stored describing one buyer, but changing it changes the stored signal shape
    and belongs with the model rework, not here.
    """
    aggregate_score: int = 0
    breakdown: dict = field(default_factory=dict)
    scored_txs: list = field(default_factory=list)
    participant_scores: list = field(default_factory=list)


def owner_of(tx_row: dict) -> dict:
    """The insider block `score_transaction` expects, from a rollup row."""
    return {
        "name": tx_row.get("insider_name"),
        "role_raw": tx_row.get("insider_role"),
        "role_category": tx_row.get("role_category"),
    }


def score_purchase(tx_row: dict, prior_for_insider: list[dict],
                   history_start: Optional[date] = None,
                   discount_reference: Optional[Sequence[float]] = None) -> Optional[dict]:
    """
    Score one purchase against that insider's earlier ones.

    `market_data` carries only the cap tier. Historical backfill has no live
    quote, and a factor one path can compute and the other cannot is what made
    the same purchase score twelve points apart depending on which entry point
    saw it. See the 52-week-low note in `scorer.py`.

    The price context the score is built on does not arrive that way. It rides
    on `tx_row` out of `purchase_rollup()`, having been fetched once at ingest
    and stored on the transaction, so this path and the live one read the same
    number rather than each measuring their own.
    """
    cap_tier = tx_row.get("cap_tier") or "unknown"
    return score_transaction(
        tx_row,
        owner_of(tx_row),
        {"cap_tier": cap_tier},
        {"cap_tier": cap_tier},
        prior_for_insider,
        history_start=history_start,
        discount_reference=discount_reference,
    )


def score_window(tx_rows: list[dict], all_prior: list[dict],
                 history_start: Optional[date] = None,
                 discount_reference: Optional[Sequence[float]] = None) -> ScoredWindow:
    """Score every purchase disclosed in one window for one ticker."""
    window = ScoredWindow()

    for tx_row in tx_rows:
        owner = owner_of(tx_row)
        prior_for_insider = [p for p in all_prior if p.get("insider_name") == owner["name"]]

        result = score_purchase(tx_row, prior_for_insider, history_start,
                                discount_reference)
        if result and result.get("eligible"):
            window.scored_txs.append(
                {"owner": owner, "transaction": tx_row, "score_result": result}
            )
            window.participant_scores.append(result["score"])
            if result["score"] > window.aggregate_score:
                window.aggregate_score = result["score"]
                window.breakdown = result["breakdown"]

    return window


def window_start_for(filed_date: date) -> date:
    """First disclosure date that would be scored alongside a filing on `filed_date`."""
    return filed_date - timedelta(days=SCORING_WINDOW_DAYS - 1)


def priors_before_window(ticker_txs: list[dict], insider_name: str,
                         filed_date: date) -> list[dict]:
    """
    That insider's purchases disclosed before this purchase's scoring window.

    The backfill splits a ticker's rows into a window and everything disclosed
    before it, then filters the latter by insider. Research has to reproduce the
    same split or its timing factors will disagree with the stored signals.
    """
    cutoff = window_start_for(filed_date)
    return [
        tx for tx in ticker_txs
        if tx.get("insider_name") == insider_name
        and tx.get("filed_date") is not None
        and tx["filed_date"] < cutoff
    ]
