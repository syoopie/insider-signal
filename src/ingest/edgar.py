"""
SEC EDGAR API client for Form 4 filings.

Rate limits: 10 req/sec max. We use 8 in normal mode, 3 in backfill mode.
Required User-Agent header on every request — missing it causes IP block.
"""

import re
import time
import requests
from datetime import date, timedelta
from typing import Iterator, Optional, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar"
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"

USER_AGENT = "InsiderSignal sunyupei19992@gmail.com"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Token bucket: track last request time to stay under rate limit
_last_request_time = 0.0


def _throttle(req_per_sec: float = 8.0):
    global _last_request_time
    min_gap = 1.0 / req_per_sec
    elapsed = time.time() - _last_request_time
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    _last_request_time = time.time()


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
)
def _get(url: str, params: dict = None, req_per_sec: float = 8.0) -> dict:
    _throttle(req_per_sec)
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if resp.status_code == 429 or resp.status_code == 403:
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.json()


def fetch_form4_index(start_date: date, end_date: date = None, req_per_sec: float = 8.0) -> Iterator[dict]:
    """
    Yield filing index records for all Form 4s in the date range.
    Each record has: accession_number, cik, filed_date, primary_document.
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
            # adsh is the dashed accession number e.g. "0001234567-26-000001"
            accession = src.get("adsh", "")
            # ciks is a list; first entry is the filer (insider), last is issuer (company)
            ciks = src.get("ciks", [])
            filer_cik = ciks[0].lstrip("0") if ciks else ""
            issuer_cik = ciks[-1].lstrip("0") if len(ciks) > 1 else filer_cik
            # display_names helps identify the issuer company name
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


def fetch_filing_xml(accession_number: str, cik: str, req_per_sec: float = 8.0) -> Optional[str]:
    """
    Fetch the raw XML content of a Form 4 filing.
    Tries the standard accession-number filename first (1 request, fast path).
    Falls back to parsing the filing index page to discover the actual filename
    when the standard name doesn't exist (e.g. 'wk-form4_1234.xml').
    Returns XML string or None if not found.
    """
    acc_no_dashes = accession_number.replace("-", "")
    cik_padded = str(cik).zfill(10)
    base_dir = f"{EDGAR_ARCHIVES}/data/{cik_padded}/{acc_no_dashes}"

    # Fast path: standard filename used by most filers
    _throttle(req_per_sec)
    try:
        resp = requests.get(f"{base_dir}/{accession_number}.xml", headers=HEADERS, timeout=30)
        if resp.status_code == 200 and resp.text.strip().startswith("<"):
            return resp.text
    except requests.RequestException:
        pass

    # Slow path: fetch filing index to discover the actual XML filename
    xml_filename = None
    for index_suffix in [f"{accession_number}-index.htm", f"{accession_number}-index.html"]:
        _throttle(req_per_sec)
        try:
            resp = requests.get(f"{base_dir}/{index_suffix}", headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                matches = re.findall(r'href="[^"]*?/([^"/?]+\.xml)"', resp.text, re.IGNORECASE)
                if matches:
                    xml_filename = matches[0]
                    break
        except requests.RequestException:
            pass

    if xml_filename:
        _throttle(req_per_sec)
        try:
            resp = requests.get(f"{base_dir}/{xml_filename}", headers=HEADERS, timeout=30)
            if resp.status_code == 200 and resp.text.strip().startswith("<"):
                return resp.text
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
