"""
Persistence for the signals table.

`replace_signals` is the only thing that writes signal rows, and it does so in
one transaction: every signal keyed to a filing disclosed in a date range is
deleted and rebuilt. A key that a rule change moved leaves no orphan behind, and
a signal already sent to Telegram stays marked as sent. Signals used to be
upserted by (ticker, signal_date), which left rows scored under three different
models coexisting in the table, and the first delete-and-rebuild re-armed every
alert it had already sent.
"""

import decimal
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from psycopg2.extras import RealDictCursor

from src.db.connection import get_conn
from src.signals.batch import ALERT_TYPES, Signal

COOLDOWN_DAYS = 7   # suppress follow-up signals within this window
SCORE_JUMP = 10     # unless score increased by at least this much
_TYPE_RANK = {"CLUSTER_BUY": 3, "BUY": 2, "WATCH": 1, "LOW": 0}

_FILED = "(evidence->>'filed_date')::date"


class _JSONEncoder(json.JSONEncoder):
    """Handles types that come out of psycopg2 rows: Decimal, date, datetime."""
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_JSONEncoder)


@dataclass(frozen=True)
class Replaced:
    deleted: int
    written: int
    suppressed: int
    deduped: int
    alerts_kept: int


def _is_suppressed(signal: Signal, recent: dict) -> bool:
    """
    Whether a nearby signal for the same ticker already covers this episode.

    abs(days_apart), so an existing signal dated after this one suppresses it
    too. A score jump of SCORE_JUMP or a type upgrade is never suppressed.

    recent: {ticker: (signal_date, score, signal_type)}.
    """
    prev = recent.get(signal.ticker)
    if prev is None:
        return False
    prev_date, prev_score, prev_type = prev
    if abs((signal.signal_date - prev_date).days) >= COOLDOWN_DAYS:
        return False
    if signal.score >= prev_score + SCORE_JUMP:
        return False
    if _TYPE_RANK.get(signal.signal_type, 0) > _TYPE_RANK.get(prev_type, 0):
        return False
    return True


def _insert(cur, signals: list[Signal]) -> tuple[int, int]:
    """Insert in (ticker, signal_date) order, so an earlier signal anchors the cooldown for later ones."""
    if not signals:
        return 0, 0
    signals = sorted(signals, key=lambda s: (s.ticker, s.signal_date))
    min_date = signals[0].signal_date if len(signals) == 1 else min(s.signal_date for s in signals)
    cur.execute(
        """
        SELECT DISTINCT ON (ticker) ticker, signal_date, score, signal_type
        FROM signals
        WHERE ticker = ANY(%s) AND signal_date >= %s AND signal_date < %s
        ORDER BY ticker, signal_date DESC
        """,
        (sorted({s.ticker for s in signals}), min_date - timedelta(days=COOLDOWN_DAYS), min_date),
    )
    recent = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    rows, suppressed = [], 0
    for s in signals:
        if _is_suppressed(s, recent):
            suppressed += 1
            continue
        rows.append((s.ticker, s.signal_date, s.score, s.signal_type, s.cluster_flag,
                     _dumps(s.score_breakdown), _dumps(s.evidence)))
        recent[s.ticker] = (s.signal_date, s.score, s.signal_type)

    cur.executemany(
        """
        INSERT INTO signals
            (ticker, signal_date, score, signal_type, cluster_flag, score_breakdown, evidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, signal_date) DO UPDATE SET
            score           = EXCLUDED.score,
            signal_type     = EXCLUDED.signal_type,
            cluster_flag    = EXCLUDED.cluster_flag,
            score_breakdown = EXCLUDED.score_breakdown,
            evidence        = EXCLUDED.evidence
        """,
        rows,
    )
    return len(rows), suppressed


def _dedup(cur, since: date, until: date) -> int:
    """
    Remove duplicate signals within the cooldown window. Two passes:

    Pass 1 removes an inferior later signal: S2 goes when an earlier S1 exists
    for the same ticker with equal or higher type rank and no score jump.

    Pass 2 removes a superseded earlier signal: S1 goes when a later S2 is
    strictly better, which handles a WATCH that became a CLUSTER_BUY once more
    insiders filed.
    """
    rank = """
        CASE {col}
            WHEN 'CLUSTER_BUY' THEN 3
            WHEN 'BUY'         THEN 2
            WHEN 'WATCH'       THEN 1
            ELSE 0
        END
    """
    r1 = rank.format(col="s1.signal_type")
    r2 = rank.format(col="s2.signal_type")

    cur.execute(f"""
        WITH prev AS (
            SELECT DISTINCT ON (s2.id)
                s2.id,
                ({r2}) AS s2_rank, s2.score AS s2_score,
                ({r1}) AS s1_rank, s1.score AS s1_score
            FROM signals s2
            JOIN signals s1
              ON  s1.ticker      = s2.ticker
              AND s1.signal_date < s2.signal_date
              AND s2.signal_date - s1.signal_date < {COOLDOWN_DAYS}
              AND s1.signal_type != 'LOW'
            WHERE s2.signal_date BETWEEN %s AND %s
            ORDER BY s2.id, ({r1}) DESC, s1.score DESC
        )
        DELETE FROM signals
        WHERE id IN (
            SELECT id FROM prev
            WHERE s2_rank <= s1_rank
              AND s2_score < s1_score + {SCORE_JUMP}
        )
    """, (since, until))
    removed = cur.rowcount

    cur.execute(f"""
        WITH nxt AS (
            SELECT DISTINCT ON (s1.id)
                s1.id,
                ({r1}) AS s1_rank, s1.score AS s1_score,
                ({r2}) AS s2_rank, s2.score AS s2_score
            FROM signals s1
            JOIN signals s2
              ON  s2.ticker      = s1.ticker
              AND s2.signal_date > s1.signal_date
              AND s2.signal_date - s1.signal_date < {COOLDOWN_DAYS}
              AND s2.signal_type != 'LOW'
            WHERE s1.signal_date BETWEEN %s AND %s
            ORDER BY s1.id, ({r2}) DESC, s2.score DESC
        )
        DELETE FROM signals
        WHERE id IN (
            SELECT id FROM nxt
            WHERE s2_rank  > s1_rank
               OR s2_score >= s1_score + {SCORE_JUMP}
        )
    """, (since, until))
    return removed + cur.rowcount


def replace_signals(start: date, end: date, signals: list[Signal]) -> Replaced:
    """
    Make the stored signals for filings disclosed in [start, end] exactly `signals`.

    Every signal passed must have a filed_date inside the range. A signal keeps
    `alerted` when its (ticker, signal_date) survives the rebuild; one whose key
    moved is a different signal and starts unalerted.
    """
    outside = [s for s in signals if not start <= s.filed_date <= end]
    if outside:
        raise ValueError(f"{len(outside)} signal(s) filed outside {start}..{end}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT ticker, signal_date FROM signals WHERE alerted AND {_FILED} BETWEEN %s AND %s",
                (start, end),
            )
            alerted = set(cur.fetchall())

            cur.execute(f"DELETE FROM signals WHERE {_FILED} BETWEEN %s AND %s", (start, end))
            deleted = cur.rowcount

            written, suppressed = _insert(cur, signals)
            deduped = 0
            if signals:
                deduped = _dedup(cur, min(s.signal_date for s in signals),
                                 max(s.signal_date for s in signals))

            kept = 0
            if alerted:
                cur.execute(
                    "UPDATE signals SET alerted = TRUE WHERE (ticker, signal_date) IN %s",
                    (tuple(alerted),),
                )
                kept = cur.rowcount

    return Replaced(deleted, written, suppressed, deduped, kept)


def unsent_alerts_filed_since(since: date) -> list[dict]:
    """
    BUY and CLUSTER_BUY signals not yet sent, whose newest filing landed on or after `since`.

    The filed_date floor is what stops a rebuild from re-sending history: a
    signal whose filings all predate this run's fetch window was already
    evaluated by an earlier run.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, ticker, signal_date, signal_type, evidence FROM signals
                WHERE NOT alerted AND signal_type = ANY(%s) AND {_FILED} >= %s
                ORDER BY signal_date, ticker
                """,
                (sorted(ALERT_TYPES), since),
            )
            return [dict(r) for r in cur.fetchall()]


def mark_signal_alerted(signal_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE signals SET alerted = TRUE WHERE id = %s", (signal_id,))
