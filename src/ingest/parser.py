"""
Form 4 XML parser. Normalizes raw XML into structured transaction dicts.

Key design decisions:
- Only returns non-derivative transactions (Table I) — derivative transactions
  (options, warrants) add complexity and noise to the signal.
- Role classification uses keyword matching on raw title strings from the filing.
- 10b5-1 detection reads <aff10b5One>, the document-level checkbox the SEC added
  when it amended Rule 10b5-1 (effective February 2023), then narrows to the
  individual transaction via its own footnote references. See _tx_is_10b51.

Only the FIRST <reportingOwner> is recorded. Joint Form 4s report one decision
under several names (a fund and its general partner, say), so collapsing them to
one insider is what cluster counting wants. It does mean the stored
insider_name for such a filing is whichever owner EDGAR listed first.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Optional


# Role classification: keywords → canonical category
# Ordered by specificity (check most specific first)
_ROLE_PATTERNS = [
    ("cfo",      r"chief financial|cfo|chief finance|finance officer|treasurer"),
    ("ceo",      r"chief executive|ceo|chief exec|president and ceo|ceo and president"),
    ("coo",      r"chief operating|coo|chief ops"),
    ("chairman", r"chairman|exec chair"),
    ("director", r"\bdirector\b|\btrustee\b|\bboard member\b"),
    ("officer",  r"officer|president|vice president|vp |svp|evp|general counsel|secretary|controller|managing"),
]


def classify_role(raw_title: str) -> str:
    if not raw_title:
        return "other"
    t = raw_title.lower()
    for category, pattern in _ROLE_PATTERNS:
        if re.search(pattern, t):
            return category
    return "other"


def _text(element, tag: str, default=None):
    """Safe text extraction from XML element."""
    node = element.find(tag)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _float(element, tag: str) -> Optional[float]:
    val = _text(element, tag)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _bool_flag(element, tag: str) -> bool:
    val = _text(element, tag, "0")
    return val in ("1", "true", "True", "yes")


_10B51_RE = re.compile(r"10b5[\s\-]?1", re.IGNORECASE)


def _footnote_texts(root) -> dict:
    """{footnote id: text}. Matches <footnote> at any depth; EDGAR nests them in
    <footnotes> but some filing agents emit them bare."""
    return {fn.get("id"): (fn.text or "") for fn in root.findall(".//footnote")}


def _tx_is_10b51(tx_el, footnotes: dict, doc_flag: Optional[bool]) -> bool:
    """
    Whether this single transaction was made under a pre-arranged 10b5-1 plan.

    <aff10b5One> is filing-wide, so when it is set every transaction in the
    filing qualifies. When it is clear, a transaction still qualifies if one of
    the footnotes *it references* names the rule — a filing can mix a plan sale
    with an ordinary open-market buy, and only the sale should be disqualified.

    doc_flag is None for filings predating the February 2023 element, where the
    only available evidence is the footnote set as a whole.
    """
    if doc_flag:
        return True
    for ref in tx_el.findall(".//footnoteId"):
        if _10B51_RE.search(footnotes.get(ref.get("id"), "")):
            return True
    if doc_flag is None:
        return any(_10B51_RE.search(t) for t in footnotes.values())
    return False


def _clean_date(raw: Optional[str]) -> Optional[str]:
    """Strip timezone offsets from date strings: '2026-05-12-05:00' → '2026-05-12'."""
    if not raw:
        return raw
    # Match YYYY-MM-DD at the start; drop anything after (timezone offset, time, etc.)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw.strip())
    return m.group(1) if m else raw


def parse_form4(xml_content: str, filing_metadata: dict) -> dict:
    """
    Parse a Form 4 XML string into a structured dict with:
      - issuer: {cik, ticker, name}
      - owner: {cik, name, role_raw, role_category, is_director, is_officer, is_ten_percent}
      - transactions: list of non-derivative transaction dicts
      - filed_date, period_date

    Returns empty dict if XML is malformed or not a Form 4.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return {}

    # --- Issuer ---
    issuer_el = root.find(".//issuer")
    issuer = {}
    if issuer_el is not None:
        issuer = {
            "cik": _text(issuer_el, "issuerCik", "").lstrip("0"),
            "ticker": (_text(issuer_el, "issuerTradingSymbol") or "").upper(),
            "name": _text(issuer_el, "issuerName", ""),
        }

    # --- Reporting Owner ---
    owner_el = root.find(".//reportingOwner")
    owner = {}
    if owner_el is not None:
        role_el = owner_el.find("reportingOwnerRelationship")
        raw_title = ""
        is_director = False
        is_officer = False
        is_ten_pct = False
        if role_el is not None:
            is_director = _bool_flag(role_el, "isDirector")
            is_officer = _bool_flag(role_el, "isOfficer")
            is_ten_pct = _bool_flag(role_el, "isTenPercentOwner")
            raw_title = _text(role_el, "officerTitle") or ""
            if not raw_title and is_director:
                raw_title = "Director"

        owner = {
            "cik": _text(owner_el.find("reportingOwnerId") or ET.Element("x"), "rptOwnerCik", "").lstrip("0"),
            "name": _text(owner_el.find("reportingOwnerId") or ET.Element("x"), "rptOwnerName", ""),
            "role_raw": raw_title,
            "role_category": classify_role(raw_title),
            "is_director": is_director,
            "is_officer": is_officer,
            "is_ten_percent": is_ten_pct,
        }

    # --- Non-Derivative Transactions (Table I — what we score) ---
    aff_raw = _text(root, ".//aff10b5One")
    doc_10b51 = None if aff_raw is None else aff_raw in ("1", "true", "True", "yes")
    footnotes = _footnote_texts(root)

    transactions = []
    for tx_el in root.findall(".//nonDerivativeTransaction"):
        tx_date_str = _clean_date(_text(tx_el, ".//transactionDate/value") or _text(tx_el, "transactionDate"))
        tx_code = _text(tx_el, ".//transactionCoding/transactionCode") or _text(tx_el, "transactionCode")
        is_10b51 = _tx_is_10b51(tx_el, footnotes, doc_10b51)

        # Table I can carry debt as well as stock. A filer reporting notes puts
        # the principal amount in BOTH transactionShares and
        # transactionPricePerShare, so shares x price is nonsense: MetLife's
        # $10M of KYN senior notes multiplied out to $100 trillion. Such a
        # filing reports holdings as valueOwnedFollowingTransaction instead of
        # sharesOwnedFollowingTransaction, and that element choice is the SEC's
        # own signal that the figure is a dollar amount, not a share count.
        post_el = tx_el.find(".//postTransactionAmounts")
        if (post_el is not None
                and post_el.find("valueOwnedFollowingTransaction") is not None
                and post_el.find("sharesOwnedFollowingTransaction") is None):
            continue

        shares_el = tx_el.find(".//transactionAmounts/transactionShares/value")
        price_el = tx_el.find(".//transactionAmounts/transactionPricePerShare/value")
        shares_after_el = tx_el.find(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value")

        shares = None
        if shares_el is not None and shares_el.text:
            try:
                shares = float(shares_el.text)
            except ValueError:
                pass

        price = None
        if price_el is not None and price_el.text:
            try:
                price = float(price_el.text)
            except ValueError:
                pass

        shares_after = None
        if shares_after_el is not None and shares_after_el.text:
            try:
                shares_after = float(shares_after_el.text)
            except ValueError:
                pass

        total_value = None
        if shares is not None and price is not None:
            total_value = shares * price

        direct_indirect = _text(tx_el, ".//ownershipNature/directOrIndirectOwnership/value", "D")
        is_direct = direct_indirect == "D"

        transactions.append({
            "transaction_date": tx_date_str,
            "transaction_code": tx_code or "",
            "shares": shares,
            "price_per_share": price,
            "total_value": total_value,
            "shares_after": shares_after,
            "is_10b51": is_10b51,
            "is_direct": is_direct,
        })

    return {
        "issuer": issuer,
        "owner": owner,
        "transactions": transactions,
        "filed_date": _clean_date(filing_metadata.get("filed_date", "")),
        "period_date": _clean_date(filing_metadata.get("period_date", "")),
        "accession_number": filing_metadata.get("accession_number", ""),
    }
