"""The ticker universe, and what counts as a usable ticker."""

import os
import re
from typing import Optional, Set

from src.log import log

_INVALID_TICKERS = {"", "NONE", "NA", "N/A", "NULL"}

# What a usable ticker looks like once cleaned. Anything else is stored as
# unknown rather than guessed at.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,5}$")

TICKERS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")


def clean_ticker(ticker: str) -> Optional[str]:
    """
    Normalise a ticker as filers write it, or None when it cannot be trusted.

    Filers type this field by hand and EDGAR accepts whatever they type. Stored
    values include '(CALX)', 'N O G', 'NYSE/TRN' and 'BFA, BFB'. Each of those
    means the company has no price data at all, because nothing downstream can
    look them up.

    Unambiguous noise is stripped: wrapping brackets, an exchange prefix, and
    the spaces in 'N O G'. Genuine ambiguity is refused. 'BFA, BFB' is
    Brown-Forman's two share classes, and taking the first would file its
    insider purchases under BFA, which is an unrelated ETF. A missing ticker
    costs one company's signals; a wrong one corrupts another company's.
    """
    if not ticker:
        return None

    t = ticker.strip().upper()
    # Before any splitting: 'N/A' cut on the slash leaves 'A', which is a real
    # ticker and would file a company with no ticker under Agilent.
    if t in _INVALID_TICKERS:
        return None
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    for sep in (":", "/"):
        if sep in t:
            t = t.split(sep)[-1].strip()
    # 'N O G' is one ticker written with spaces; 'BFA, BFB' is two tickers.
    if "," not in t:
        t = t.replace(" ", "")

    if t in _INVALID_TICKERS or not _TICKER_RE.match(t):
        return None
    return t


def resolve_ticker(filing_meta: dict, cik_to_ticker: dict) -> str:
    """Map a filing's raw CIK to a ticker using the SEC CIK→ticker map."""
    raw_cik = filing_meta.get("cik_raw", "").lstrip("0")
    return cik_to_ticker.get(raw_cik.zfill(10), "").upper()


def load_ticker_universe() -> Set[str]:
    if not os.path.exists(TICKERS_FILE):
        log("WARNING: data/tickers.txt not found — no universe filter applied")
        return set()
    with open(TICKERS_FILE) as f:
        return {line.strip().upper() for line in f if line.strip()}


def in_universe(ticker: str, ticker_universe: Set[str]) -> bool:
    """Return True if this filing should be processed given the universe filter."""
    if not ticker_universe:
        return True
    return bool(ticker) and ticker in ticker_universe
