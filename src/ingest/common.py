"""
Shared utilities for bootstrap.py and run_ingest.py.

Both scripts ingest SEC Form 4 filings but differ in strategy:
  - bootstrap.py: bulk backfill, thread pool, batched raw SQL, no market data
  - run_ingest.py: daily incremental, single-threaded, store.py helpers, market data + signals

This module holds the pieces that are truly identical between them.
"""

import os
import re
import sys
import time
from datetime import datetime
from typing import Optional, Set

# Windows consoles default to cp1252, which cannot encode the box-drawing chars
# in phase() or the accented company names that come back from EDGAR. Force
# UTF-8 on the real streams before anything writes to them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from src.ingest.edgar import (
    fetch_cik_ticker_map, fetch_filing_xml,
    EdgarRateLimitError, EdgarBlockedError, EdgarServerError,
)
from src.ingest.parser import parse_form4

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")


# ── Logging ───────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def setup_log_tee(script_name: str) -> str:
    """Redirect stdout/stderr to both console and a timestamped log file. Returns log path."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(_LOG_DIR, f"{script_name}_{ts}.log")
    log_file = open(log_path, "w", buffering=1, encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    return log_path


def log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def phase(title: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"\n[{ts}] {'─' * 10} {title} {'─' * 10}", flush=True)


def fmt_elapsed(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


# ── Ticker helpers ────────────────────────────────────────────────────────────

_INVALID_TICKERS = {"", "NONE", "NA", "N/A", "NULL"}

# What a usable ticker looks like once cleaned. Anything else is stored as
# unknown rather than guessed at.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,5}$")


def _clean_ticker(ticker: str) -> Optional[str]:
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


# ── Universe + CIK map ────────────────────────────────────────────────────────

def load_ticker_universe() -> Set[str]:
    tickers_file = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tickers.txt")
    if not os.path.exists(tickers_file):
        log("WARNING: data/tickers.txt not found — no universe filter applied")
        return set()
    with open(tickers_file) as f:
        return {line.strip().upper() for line in f if line.strip()}


def load_cik_map(req_per_sec: float = 8.0) -> dict:
    """Fetch SEC CIK→ticker map. Returns {cik_padded: ticker}."""
    try:
        ticker_to_cik = fetch_cik_ticker_map(req_per_sec=req_per_sec)
        cik_to_ticker = {v: k for k, v in ticker_to_cik.items()}
        log(f"CIK map loaded: {len(cik_to_ticker):,} entries")
        return cik_to_ticker
    except Exception as e:
        log(f"CIK map fetch failed: {e} — continuing without ticker resolution")
        return {}


def in_universe(ticker: str, ticker_universe: Set[str]) -> bool:
    """Return True if this filing should be processed given the universe filter."""
    if not ticker_universe:
        return True
    return bool(ticker) and ticker in ticker_universe


# Sentinels returned by fetch_and_parse to distinguish outcomes without exceptions.
# EdgarRateLimitError / EdgarBlockedError / EdgarServerError propagate as exceptions.
DERIV_ONLY  = object()  # filing parsed cleanly but only has Table II (options/warrants)
XML_MISSING = object()  # XML fetch returned nothing (404, timeout, server error)
PARSE_ERROR = object()  # XML fetched but parse_form4 returned None (malformed XML)


def fetch_and_parse(filing_meta: dict, rate: float = 8.0):
    """
    Fetch XML and parse a Form 4.
    Returns:
      (filing_meta, parsed)  — success, has non-derivative transactions
      DERIV_ONLY             — filing has only derivative transactions (Table II)
      XML_MISSING            — XML fetch returned nothing (404, timeout, server error)
      PARSE_ERROR            — XML fetched but parse_form4 returned None
    Raises EdgarRateLimitError / EdgarBlockedError / EdgarServerError — callers must
    handle these as fatal; they must not be silently counted as parse errors.
    Thread-safe.
    """
    filer_cik = filing_meta.get("filer_cik", filing_meta.get("cik_raw", ""))
    xml = fetch_filing_xml(filing_meta["accession_number"], filer_cik, req_per_sec=rate)
    # EdgarRateLimitError / EdgarBlockedError / EdgarServerError propagate naturally.
    if not xml:
        return XML_MISSING
    parsed = parse_form4(xml, filing_meta)
    if not parsed:
        return PARSE_ERROR
    if not parsed.get("transactions"):
        return DERIV_ONLY
    return filing_meta, parsed


