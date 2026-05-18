"""
SEC EDGAR API client for Form 4 filings.

Rate limits: 10 req/sec max. We use 8 in normal mode.
Required User-Agent header on every request — missing it causes IP block.
"""

import re
import time
import threading
import requests
from datetime import date
from typing import Iterator, Optional, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"

USER_AGENT = "InsiderSignal sunyupei19992@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

_last_request_time = 0.0
_throttle_lock = threading.Lock()

# Cache: filer_cik → {accession_number: primary_document_path}
# Avoids re-fetching the submissions JSON for the same insider across multiple filings.
_submissions_cache: Dict[str, Dict[str, str]] = {}
_submissions_lock = threading.Lock()


def _throttle(req_per_sec: float = 8.0):
    """Global rate limiter shared across all threads. Lock is held only for the
    timestamp check/update — sleeping happens outside so threads can overlap."""
    global _last_request_time
    min_gap = 1.0 / req_per_sec
    while True:
        with _throttle_lock:
            now = time.time()
            elapsed = now - _last_request_time
            if elapsed >= min_gap:
                _last_request_time = now
                return  # this thread may now make its request
            sleep_for = min_gap - elapsed
        time.sleep(sleep_for)  # sleep outside the lock so other threads can proceed


@retry(
    retry=retry_if_exception_type((requests.HTTPError, requests.Timeout, requests.ConnectionError)),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
)
def _get(url: str, params: dict = None, req_per_sec: float = 8.0, timeout: int = 30) -> dict:
    _throttle(req_per_sec)
    resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
    if resp.status_code == 429 or resp.status_code == 403:
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.json()


def _get_primary_doc(filer_cik: str, accession_number: str, req_per_sec: float = 8.0) -> Optional[str]:
    """
    Returns the primary document path for a filing via the SEC submissions API.
    Result is cached per filer CIK so subsequent filings from the same insider
    are free (no extra request).
    """
    cik_padded = str(filer_cik).zfill(10)

    with _submissions_lock:
        already_cached = cik_padded in _submissions_cache
    if not already_cached:
        try:
            # Single best-effort attempt, short timeout. The submissions API is
            # a speed optimisation only — on failure we fall back to the index
            # page, so burning time on retries here is counterproductive.
            _throttle(req_per_sec)
            resp = requests.get(
                f"{EDGAR_SUBMISSIONS}/CIK{cik_padded}.json",
                headers=HEADERS, timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            accessions = recent.get("accessionNumber", [])
            docs = recent.get("primaryDocument", [])
            mapping = {}
            for acc, doc in zip(accessions, docs):
                # Strip xslFXXXXXX/ prefix — that path serves styled HTML, not raw XML.
                clean_doc = re.sub(r'^xsl[^/]+/', '', doc) if doc else doc
                mapping[acc.replace("-", "")] = clean_doc
            with _submissions_lock:
                _submissions_cache[cik_padded] = mapping
        except Exception:
            with _submissions_lock:
                _submissions_cache[cik_padded] = {}

    acc_no_dashes = accession_number.replace("-", "")
    with _submissions_lock:
        return _submissions_cache[cik_padded].get(acc_no_dashes)


def fetch_form4_index(start_date: date, end_date: date = None, req_per_sec: float = 8.0) -> Iterator[dict]:
    """
    Yield filing index records for all Form 4s in the date range.
    Each record has: accession_number, cik_raw, filer_cik, entity_name, filed_date, period_date.
    """
    if end_date is None:
        end_date = date.today()

    page_size = 100
    offset = 0

    while True:
        params = {
            "forms": "4",
            "dateRange": "custom",
            "startdt": start_date.isoformat(),
            "enddt": end_date.isoformat(),
            "from": offset,
            "size": page_size,
        }
        data = _get(EDGAR_BASE, params=params, req_per_sec=req_per_sec)
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            accession = src.get("adsh", "")
            ciks = src.get("ciks", [])
            filer_cik = ciks[0].lstrip("0") if ciks else ""
            issuer_cik = ciks[-1].lstrip("0") if len(ciks) > 1 else filer_cik
            display_names = src.get("display_names", [])
            entity_name = display_names[-1].split("(CIK")[0].strip() if display_names else ""
            yield {
                "accession_number": accession,
                "cik_raw": issuer_cik,
                "filer_cik": filer_cik,
                "entity_name": entity_name,
                "filed_date": src.get("file_date", ""),
                "period_date": src.get("period_ending", ""),
            }

        offset += page_size
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        if offset >= total:
            break


def fetch_filing_xml(accession_number: str, filer_cik: str, req_per_sec: float = 8.0) -> Optional[str]:
    """
    Fetch the raw XML content of a Form 4 filing.

    Strategy (fastest to slowest):
    1. Look up the primary document filename from the SEC submissions API
       (cached per filer CIK — free on subsequent calls for the same insider).
    2. Fall back to fetching the filing index page and parsing it for .xml links.
    """
    acc_no_dashes = accession_number.replace("-", "")
    cik_padded = str(filer_cik).zfill(10)
    base_dir = f"{EDGAR_ARCHIVES}/data/{cik_padded}/{acc_no_dashes}"

    # Path 1: submissions API (1 cached request, then free)
    primary_doc = _get_primary_doc(filer_cik, accession_number, req_per_sec)
    if primary_doc:
        _throttle(req_per_sec)
        try:
            resp = requests.get(f"{base_dir}/{primary_doc}", headers=HEADERS, timeout=15)
            if resp.status_code == 200 and resp.text.strip().startswith("<?xml") or "<ownershipDocument" in resp.text[:500]:
                return resp.text
        except requests.RequestException:
            pass

    # Path 2: fetch filing index page and scrape .xml link
    for index_suffix in [f"{accession_number}-index.htm", f"{accession_number}-index.html"]:
        _throttle(req_per_sec)
        try:
            resp = requests.get(f"{base_dir}/{index_suffix}", headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                matches = re.findall(r'href="[^"]*?/([^"/?]+\.xml)"', resp.text, re.IGNORECASE)
                if matches:
                    _throttle(req_per_sec)
                    resp2 = requests.get(f"{base_dir}/{matches[0]}", headers=HEADERS, timeout=15)
                    if resp2.status_code == 200 and resp2.text.strip().startswith("<"):
                        return resp2.text
                    break
        except requests.RequestException:
            pass

    return None


def fetch_cik_ticker_map(req_per_sec: float = 8.0) -> Dict[str, str]:
    """
    Returns {ticker: cik_str} mapping from SEC's official JSON.
    CIKs are returned as zero-padded 10-digit strings.
    """
    data = _get(EDGAR_TICKERS, req_per_sec=req_per_sec)
    result = {}
    for entry in data.values():
        ticker = entry.get("ticker", "").upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        if ticker and cik:
            result[ticker] = cik
    return result
