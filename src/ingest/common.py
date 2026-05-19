"""
Shared utilities for bootstrap.py and run_ingest.py.

Both scripts ingest SEC Form 4 filings but differ in strategy:
  - bootstrap.py: bulk backfill, thread pool, batched raw SQL, no market data
  - run_ingest.py: daily incremental, single-threaded, store.py helpers, market data + signals

This module holds the pieces that are truly identical between them.
"""

import os
import sys
import time
from datetime import datetime
from typing import Optional, Set

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


def _clean_ticker(ticker: str) -> Optional[str]:
    """Return the ticker uppercased, or None if it's a sentinel/missing value.
    Strips exchange prefixes like 'NASDAQ:SVC' → 'SVC'."""
    if not ticker:
        return None
    t = ticker.strip().upper()
    if ":" in t:
        t = t.split(":")[-1].strip()
    return None if t in _INVALID_TICKERS else t


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


