"""Fetching and parsing one Form 4, shared by bootstrap.py and run_ingest.py."""

from src.ingest.edgar import fetch_cik_ticker_map, fetch_filing_xml
from src.ingest.parser import parse_form4
from src.log import log


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
    if not xml:
        return XML_MISSING
    parsed = parse_form4(xml, filing_meta)
    if not parsed:
        return PARSE_ERROR
    if not parsed.get("transactions"):
        return DERIV_ONLY
    return filing_meta, parsed
