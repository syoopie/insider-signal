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
from typing import Set

from src.ingest.edgar import fetch_cik_ticker_map, fetch_filing_xml
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


def fetch_and_parse(filing_meta: dict, rate: float = 8.0):
    """Fetch XML and parse a Form 4. Returns (filing_meta, parsed) or None. Thread-safe."""
    filer_cik = filing_meta.get("filer_cik", filing_meta.get("cik_raw", ""))
    xml = fetch_filing_xml(filing_meta["accession_number"], filer_cik, req_per_sec=rate)
    if not xml:
        return None
    parsed = parse_form4(xml, filing_meta)
    if not parsed or not parsed.get("transactions"):
        return None
    return filing_meta, parsed


def log_stored(ticker: str, accession: str, n_tx: int, codes: list, filed: str, cap: str = ""):
    """Standard STORED log line used by both bootstrap and run_ingest."""
    cap_part = f"  cap={cap}" if cap else ""
    log(f"  STORED  {ticker:<6}  {accession}  {n_tx} tx {codes}  filed={filed}{cap_part}")
