"""
The exclusions being raced, each one a mask over the research frame.

The same split `candidates.py` makes for rankings, made for gates. The ruler in
`walkforward.py` stays frozen; hypotheses live here.

A gate is the estimand the evidence points at. The placebo control found that a
deeply discounted stock nobody bought has a negative median and a 49.3% hit rate
while the same stock with a Form 4 on it has a positive median and 57.7%, so the
filing works as a threshold. Insider attributes have never ordered the
discounted set, and the one measured inside it, cluster buyer count, points the
wrong way. Asking instead whether excluding a class raises what is left is both
the hypothesis that fits and the cheaper measurement, because it spends every
row rather than a 37-row decile.

Every mask here is computable from a single purchase's own stored data at the
moment it is filed. A cutoff drawn from the month's own distribution would not
be, and would not survive into production.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

Mask = Callable[[pd.DataFrame], pd.Series]

# Where the firm's own insiders have to sit for a purchase to be kept. Dollar
# buys over buys plus sells across the trailing 90 days; 0.5 is the balance
# point and the value the feature takes when the firm has neither.
NET_BUYING = 0.5

# A Form 4 is due within two business days. 12,247 of 13,294 purchases file
# inside four days, so this is not a continuous variable with a tail, it is a
# rare class: 330 filings land here and some are years late.
LATE_FILING_DAYS = 30

# Everyone the scorer classifies as management rather than a plain board member
# or an unlabelled filer.
OFFICER_ROLES = frozenset({"ceo", "cfo", "coo", "chairman", "officer"})


def _bool(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = pd.Series(frame[column], index=frame.index)
    return values.map(lambda v: bool(v) if v is not None and v == v else False)


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column), errors="coerce")


def not_net_selling(frame: pd.DataFrame) -> pd.Series:
    """
    Drop purchases at firms whose insiders are net sellers over 90 days.

    91,296 stored sale rows the scorer has never read. Lakonishok and Lee's
    result is a buy-minus-sell spread and the system uses the buy half only.
    Tested once before as a continuous ranker, where it measured nothing; this
    is the other estimand.
    """
    return _num(frame, "demand_buy_ratio") >= NET_BUYING


def not_late(frame: pd.DataFrame) -> pd.Series:
    """Drop filings that took a month or more to appear."""
    return _num(frame, "filing_lag_days") < LATE_FILING_DAYS


def direct_only(frame: pd.DataFrame) -> pd.Series:
    """
    Drop purchases made through an LLC, trust or family entity.

    Already excluded from cluster counting and already a penalty in the retired
    weight table, never measured as an exclusion on the full sample.
    """
    return _bool(frame, "is_direct")


def officers_only(frame: pd.DataFrame) -> pd.Series:
    """
    Keep management and drop plain directors and unlabelled filers.

    The literature puts the effect in managers rather than large shareholders.
    The sharper cut would be officers who *also* sit on the board, and it is not
    available. `parse_form4` reads `isDirector`, `isOfficer` and
    `isTenPercentOwner` off `reportingOwnerRelationship`; `write_filing` stores
    none of the three, so all that survives is `officerTitle`, which the parser
    fills with the literal "Director" when there is no title. A director who is
    also the CFO and a CFO who is not on the board are the same stored row.
    That cut needs three columns and a re-parse, not a free gate.
    """
    roles = frame.get("role_category", pd.Series("", index=frame.index)).fillna("")
    return roles.isin(OFFICER_ROLES)


def small_roster(frame: pd.DataFrame) -> pd.Series:
    """
    Keep purchases where the buyers are a large share of a small insider roster.

    Three buyers out of five is a different statement from three out of forty.
    """
    return _num(frame, "cluster_roster_share") >= 0.25


def not_averaging_down(frame: pd.DataFrame) -> pd.Series:
    """
    Drop purchases below this insider's own last purchase price at the issuer.

    Named in CLAUDE.md as the explanation for the worst outcome in the history
    and never implemented as a filter.
    """
    return ~_bool(frame, "is_averaging_down")


def not_10b51(frame: pd.DataFrame) -> pd.Series:
    return ~_bool(frame, "is_10b51")


def not_routine(frame: pd.DataFrame) -> pd.Series:
    return ~_bool(frame, "is_routine")


def above_2k(frame: pd.DataFrame) -> pd.Series:
    return _num(frame, "total_value") >= 2_000


def keep_everything(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=frame.index)


def coin_flip(seed: int = 20260905) -> Mask:
    """The control. A mask that drops a fifth of every month at random."""
    def mask(frame: pd.DataFrame) -> pd.Series:
        rng = np.random.default_rng(seed)
        return pd.Series(rng.random(len(frame)) > 0.2, index=frame.index)
    return mask


# Gates over the purchases the pipeline already scores. These are proposals.
GATES: dict[str, Mask] = {
    "keep everything": keep_everything,
    "coin flip": coin_flip(),
    "firm not net selling": not_net_selling,
    "filed within 30 days": not_late,
    "direct only": direct_only,
    "an officer, not a plain director": officers_only,
    "buyers are 25% of the roster": small_roster,
    "not averaging down": not_averaging_down,
}

# Gates the pipeline already applies, measured on the purchases it throws away.
# These are validations, not proposals. A disqualified class with positive lift
# would be worth more than any feature on the candidate list.
DISQUALIFIERS: dict[str, Mask] = {
    "not a 10b5-1 plan trade": not_10b51,
    "not a routine buyer": not_routine,
    "at least $2,000": above_2k,
}
