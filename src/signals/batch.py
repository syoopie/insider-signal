"""
Turning stored purchases into signals, in memory.

Every signal is built by `build_signal`, whichever entrypoint asked for it: the
daily ingest and the backfill both go through `src/signals/rebuild.py`, and the
research dataset builder scores through `score_purchase`. The two entrypoints
used to assemble signals separately, and they disagreed on the scoring window,
the cluster's as-of date, which prior purchases the routine check could see, and
which filing date the backtest traded on.

Nothing here reads the network or the database. Callers supply the rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional, Sequence

from src.signals.cluster import cluster_from_transactions
from src.signals.formatter import build_evidence
from src.signals.scorer import classify_signal, score_transaction

# Purchases disclosed within this many days of each other are scored as one window.
SCORING_WINDOW_DAYS = 7

ALERT_TYPES = frozenset({"BUY", "CLUSTER_BUY"})

ReferenceFor = Callable[[date], Sequence[float]]


@dataclass
class ScoredWindow:
    """
    What a window of purchases for one ticker came to.

    `aggregate_score` is the highest individual score, which is what the BUY
    threshold is applied to. `participant_scores` is every eligible score, which
    is what the cluster average is taken over.
    """
    aggregate_score: int = 0
    breakdown: dict = field(default_factory=dict)
    scored_txs: list = field(default_factory=list)
    participant_scores: list = field(default_factory=list)


@dataclass(frozen=True)
class Signal:
    ticker: str
    signal_date: date
    filed_date: date
    score: int
    signal_type: str
    cluster_flag: bool
    score_breakdown: dict
    evidence: dict


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
    """Score one purchase against that insider's earlier ones."""
    return score_transaction(
        tx_row,
        owner_of(tx_row),
        prior_for_insider,
        history_start=history_start,
        discount_reference=discount_reference,
    )


def score_window(tx_rows: list[dict], all_prior: list[dict],
                 history_start: Optional[date] = None,
                 reference_for: Optional[ReferenceFor] = None) -> ScoredWindow:
    """
    Score every purchase disclosed in one window for one ticker.

    `reference_for` maps a filing date to the discounts of purchases disclosed
    before it. It is a callable rather than one array because a window spans
    seven days of filings, and each purchase is ranked against what had been
    disclosed when *it* was filed.
    """
    window = ScoredWindow()

    for tx_row in tx_rows:
        owner = owner_of(tx_row)
        prior_for_insider = [p for p in all_prior if p.get("insider_name") == owner["name"]]

        reference = reference_for(tx_row["filed_date"]) if reference_for else None
        result = score_purchase(tx_row, prior_for_insider, history_start, reference)
        if result and result.get("eligible"):
            window.scored_txs.append(
                {"owner": owner, "transaction": tx_row, "score_result": result}
            )
            window.participant_scores.append(result["score"])
            if result["score"] > window.aggregate_score:
                window.aggregate_score = result["score"]
                window.breakdown = result["breakdown"]

    if window.scored_txs and not window.breakdown:
        window.breakdown = window.scored_txs[0]["score_result"]["breakdown"]
    return window


def window_start_for(filed_date: date) -> date:
    """First disclosure date that would be scored alongside a filing on `filed_date`."""
    return filed_date - timedelta(days=SCORING_WINDOW_DAYS - 1)


def priors_before_window(ticker_txs: list[dict], insider_name: str,
                         filed_date: date) -> list[dict]:
    """That insider's purchases disclosed before this purchase's scoring window."""
    cutoff = window_start_for(filed_date)
    return [
        tx for tx in ticker_txs
        if tx.get("insider_name") == insider_name
        and tx.get("filed_date") is not None
        and tx["filed_date"] < cutoff
    ]


def split_window(ticker_txs: list[dict], filed_date: date) -> tuple[list[dict], list[dict]]:
    """
    (purchases disclosed in the scoring window ending `filed_date`,
     purchases disclosed before it). Order is preserved.
    """
    start = window_start_for(filed_date)
    window, prior = [], []
    for tx in ticker_txs:
        filed = tx.get("filed_date")
        if filed is None or tx.get("transaction_date") is None:
            continue
        if start <= filed <= filed_date:
            window.append(tx)
        elif filed < start:
            prior.append(tx)
    return window, prior


def disclosed_by(ticker_txs: list[dict], as_of: date) -> list[dict]:
    """Rows whose filing had landed by `as_of`. A cluster must not form before it is public."""
    return [tx for tx in ticker_txs if tx.get("filed_date") is not None
            and tx["filed_date"] <= as_of]


def build_signal(ticker: str, ticker_txs: list[dict], filed_date: date,
                 history_start: Optional[date],
                 reference_for: Optional[ReferenceFor]) -> Optional[Signal]:
    """
    The signal for `ticker` as of the filings disclosed on `filed_date`.

    `ticker_txs` is every rolled-up purchase for the ticker, 10b5-1 trades
    excluded, newest transaction first. None when no purchase in the window is
    eligible. A LOW signal is returned rather than dropped; the caller decides
    whether to store it.

    `filed_date` is also what the backtest keys its entry off, so it is the date
    by which everything in the signal had been disclosed, never earlier.
    """
    tx_rows, prior = split_window(ticker_txs, filed_date)
    if not tx_rows:
        return None

    window = score_window(tx_rows, prior, history_start, reference_for)
    if not window.scored_txs:
        return None

    cluster = cluster_from_transactions(disclosed_by(ticker_txs, filed_date), filed_date)
    cap_tier = tx_rows[0].get("cap_tier") or "unknown"
    signal_type = classify_signal(window.aggregate_score, cluster["is_cluster"],
                                  window.participant_scores, cluster["tight_cluster"],
                                  cap_tier)
    signal_date = tx_rows[0]["transaction_date"]
    evidence = build_evidence(
        ticker=ticker,
        company_name=tx_rows[0].get("company_name") or ticker,
        score=window.aggregate_score,
        signal_type=signal_type,
        score_breakdown=window.breakdown,
        cluster_info=cluster,
        transactions=window.scored_txs,
        cap_tier=cap_tier,
        filed_date=filed_date,
        signal_date=signal_date,
    )
    return Signal(
        ticker=ticker,
        signal_date=signal_date,
        filed_date=filed_date,
        score=window.aggregate_score,
        signal_type=signal_type,
        cluster_flag=cluster["is_cluster"],
        score_breakdown=window.breakdown,
        evidence=evidence,
    )
