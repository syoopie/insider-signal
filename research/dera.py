"""
Form 3/4/5 history from the SEC's own quarterly datasets.

The archive used to be built by fetching filings one at a time. For anything
older than about a year that costs three requests each, because the submissions
API has aged the filing out and `fetch_filing_xml` falls back to scraping the
index page for the XML link. Three requests times a thousand filings a week is
what put a 9 req/sec budget over EDGAR's limit and earned a 429 twenty-two
minutes into the first real run.

DERA publishes the same filings already parsed, one zip per quarter, about 10MB
each. Ten years is roughly forty requests and 400MB against the several hundred
thousand requests the per-filing path needs, and it is SEC's own parse of the
same XML rather than a second one of ours.

Checked against EDGAR's daily index over 2022-09-01 to 09-07: both report 2,891
distinct Form 4 and 4/A accessions, so this is the complete record and not a
sample of it.

`is_10b51` is decided the way `parser._tx_is_10b51` decides it, from the same
two pieces of evidence. `AFF10B5ONE` on SUBMISSION is the `<aff10b5One>`
checkbox, filing-wide, and it exists only from 2023q1 because the SEC amended
Rule 10b5-1 effective February 2023. FOOTNOTES.tsv plus the twelve `_FN`
columns on NONDERIV_TRANS are DERA's flattening of the footnote references
`parse_form4` reads off each transaction, and they go back as far as the
datasets do. So the checkbox being absent is not the end of the answer, and the
years before it are not blank.

Publication lags the quarter by a month or so. That is irrelevant here: the
database retains 48 months and this exists to reach further back than that.
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

from src.ingest.edgar import HEADERS, _throttle
from src.ingest.parser import _10B51_RE, classify_role

DERA_BASE = ("https://www.sec.gov/files/structureddata/data"
             "/insider-transactions-data-sets")

# The four tables the archive needs. DERA ships seven; signatures and the
# derivative tables are not read.
TABLES = ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS", "FOOTNOTES")

# DERA flattens `tx_el.findall(".//footnoteId")` into one column per field of
# the transaction that can carry a reference. Their union is the set that
# `_tx_is_10b51` walks, so all twelve are read or none of them is trusted.
FOOTNOTE_ID_COLUMNS = (
    "SECURITY_TITLE_FN", "TRANS_DATE_FN", "DEEMED_EXECUTION_DATE_FN",
    "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS_FN", "TRANS_SHARES_FN",
    "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD_FN",
    "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS_FN",
    "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP_FN",
)

FORM4_TYPES = ("4", "4/A")


def quarter_url(year: int, quarter: int) -> str:
    return f"{DERA_BASE}/{year}q{quarter}_form345.zip"


def quarters_between(start: date, end: date) -> list:
    out, year, quarter = [], start.year, (start.month - 1) // 3 + 1
    while (year, quarter) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return out


def download_quarter(year: int, quarter: int, dest: Path,
                     req_per_sec: float = 8.0) -> Optional[Path]:
    """
    The quarter's zip on disk, downloaded if it is not already there.

    Returns None for a quarter DERA has not published yet, which is the current
    one and sometimes the one before it. That is not an error.
    """
    path = dest / f"{year}q{quarter}_form345.zip"
    if path.exists() and path.stat().st_size > 0:
        return path
    _throttle(req_per_sec)
    resp = requests.get(quarter_url(year, quarter), headers=HEADERS, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    dest.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


def read_quarter(zip_path: Path) -> dict:
    """
    The tables the archive reads, as string frames.

    A table the quarter does not ship is left out of the dict rather than
    faked, and `to_archive_rows` answers unknown for whatever it cannot see.
    """
    with zipfile.ZipFile(zip_path) as archive:
        shipped = set(archive.namelist())
        return {
            name: pd.read_csv(archive.open(f"{name}.tsv"), sep="\t", dtype=str,
                              keep_default_na=False, on_bad_lines="skip")
            for name in TABLES if f"{name}.tsv" in shipped
        }


def _iso(series: pd.Series) -> pd.Series:
    """DERA writes 31-AUG-2022. Everything downstream reads 2022-08-31."""
    return pd.to_datetime(series, format="%d-%b-%Y", errors="coerce").dt.date


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


# A title naming a debt instrument. Table I can carry notes, and a filer
# reporting them puts the principal amount in both the share count and the
# price, so shares times price is meaningless: one MetLife row reads 3,000,000
# shares at $3,000,000 each, or $9 trillion.
#
# Two tests, because neither alone is enough. `parse_form4` keys off the holding
# being denominated in value rather than shares, and that is the primary signal;
# but MetLife's 2022Q3 rows leave that column blank on the very line that needs
# it while naming "4.67% Series SS Senior Unsecured Notes" in the title. The
# title is what a reader would use and it costs one more column to keep.
DEBT_TITLE = r"\b(?:notes?|debentures?|bonds?)\b"


def is_debt(trans: pd.DataFrame) -> pd.Series:
    """Rows whose share count is really a principal amount."""
    by_value = (trans["VALU_OWND_FOLWNG_TRANS"].str.strip() != "") \
        if "VALU_OWND_FOLWNG_TRANS" in trans.columns \
        else pd.Series(False, index=trans.index)
    by_title = trans["SECURITY_TITLE"].str.contains(DEBT_TITLE, case=False,
                                                    regex=True, na=False)
    return by_value | by_title


def _flag(series: pd.Series, token: str) -> pd.Series:
    """
    One relationship out of the comma-joined set DERA writes.

    Substring rather than a split on commas, because the set is not always well
    formed: `Director,TenPercentOwnerOther` appears 152 times in 2022Q3 alone,
    with two relationships run together and no separator between them.
    """
    return series.fillna("").str.contains(token, case=False, regex=False)


def _checkbox_by_filing(filings: pd.DataFrame) -> pd.Series:
    """
    `<aff10b5One>` per accession: True, False, or NA where the element is absent.

    An empty cell is an absent element, not a clear box. The checkbox postdates
    February 2023 and 63,649 of 2023q1's filings were made before it existed, so
    they carry a blank rather than a zero. `_tx_is_10b51` calls that case
    doc_flag=None and lets the footnotes answer instead; reading it as False
    would silence that branch on every filing older than the rule change.
    """
    raw = filings.drop_duplicates("ACCESSION_NUMBER") \
                 .set_index("ACCESSION_NUMBER")["AFF10B5ONE"]
    return raw.str.strip().str.lower().map(
        {"1": True, "true": True, "y": True,
         "0": False, "false": False, "n": False}).astype("boolean")


def _plan_footnotes(footnotes: pd.DataFrame) -> tuple[set, set]:
    """
    Footnotes naming Rule 10b5-1, as {(accession, id)} and the accessions holding one.

    The pattern is imported from the parser rather than retyped: it is the one
    definition of what counts as naming the rule, and a second copy of it here
    would be a second answer to the same question.
    """
    hit = footnotes[footnotes["FOOTNOTE_TXT"].str.contains(_10B51_RE, na=False)]
    accessions = hit["ACCESSION_NUMBER"]
    return set(zip(accessions, hit["FOOTNOTE_ID"].str.strip())), set(accessions)


def _references_a_plan(trans: pd.DataFrame, plan_ids: set,
                       plan_filings: set) -> np.ndarray:
    """
    Rows one of whose own footnote references names the rule.

    Only rows inside a filing that has such a footnote can qualify, so the
    per-row work runs over that handful rather than the quarter. Cells hold
    comma-joined ids (`F2, F1`), unordered, and every id in every cached quarter
    matches `F<n>`.
    """
    if not plan_ids:
        return np.zeros(len(trans), dtype=bool)
    candidates = trans[trans["ACCESSION_NUMBER"].isin(plan_filings)]
    cells = candidates[list(FOOTNOTE_ID_COLUMNS)].agg(",".join, axis=1)
    hit = [
        index
        for index, accession, cell in zip(candidates.index,
                                          candidates["ACCESSION_NUMBER"], cells)
        if any((accession, ref.strip()) in plan_ids for ref in cell.split(","))
    ]
    return trans.index.isin(hit)


def _ten_b5_one(trans: pd.DataFrame, filings: pd.DataFrame,
                footnotes: Optional[pd.DataFrame]) -> pd.arrays.BooleanArray:
    """
    `parser._tx_is_10b51`'s rule, evaluated over a quarter of DERA rows.

    The parser's four branches collapse into one expression because three of
    them answer True and the fourth is what is left. The filing-wide footnote
    scan is guarded on the checkbox being absent, which is the only asymmetry:
    with a checkbox present and clear, a plan footnote attached to some other
    transaction in the filing does not disqualify this one.

    Without FOOTNOTES.tsv or the `_FN` columns, only a set checkbox can decide a
    row. A clear one cannot, because the branch that would overrule it is the
    one that cannot be evaluated, so those rows stay unknown.
    """
    checked = _checkbox_by_filing(filings) if "AFF10B5ONE" in filings.columns \
        else pd.Series(dtype="boolean")
    doc = trans["ACCESSION_NUMBER"].map(checked).astype("boolean")
    doc_set = doc.fillna(False).to_numpy(dtype=bool)

    readable = footnotes is not None and \
        all(column in trans.columns for column in FOOTNOTE_ID_COLUMNS)
    if not readable:
        return pd.array(np.where(doc_set, True, pd.NA), dtype="boolean")

    plan_ids, plan_filings = _plan_footnotes(footnotes)
    own = _references_a_plan(trans, plan_ids, plan_filings)
    anywhere = trans["ACCESSION_NUMBER"].isin(plan_filings).to_numpy()
    return pd.array(doc_set | own | (doc.isna().to_numpy() & anywhere),
                    dtype="boolean")


def to_archive_rows(tables: dict, universe: set):
    """
    One quarter of DERA tables as the archive's filing and transaction frames.

    Only the first reporting owner is kept, matching `parse_form4`, which
    records only the first `<reportingOwner>`. A joint Form 4 reports one
    decision under several names and collapsing them is what cluster counting
    wants; the cost is that the stored name is whichever DERA listed first.
    """
    submissions = tables["SUBMISSION"]
    filings = submissions[submissions["DOCUMENT_TYPE"].isin(FORM4_TYPES)].copy()
    filings["ticker"] = filings["ISSUERTRADINGSYMBOL"].str.strip().str.upper()
    if universe:
        filings = filings[filings["ticker"].isin(universe)]

    filing_rows = pd.DataFrame({
        "accession_number": filings["ACCESSION_NUMBER"],
        "cik": filings["ISSUERCIK"].str.lstrip("0"),
        "ticker": filings["ticker"],
        "company_name": filings["ISSUERNAME"],
        "filed_date": _iso(filings["FILING_DATE"]),
        "period_date": _iso(filings["PERIOD_OF_REPORT"]),
    })

    kept = set(filing_rows["accession_number"])
    raw_owners = tables["REPORTINGOWNER"]
    raw_owners = raw_owners[raw_owners["ACCESSION_NUMBER"].isin(kept)] \
        .drop_duplicates("ACCESSION_NUMBER", keep="first")
    relationship = raw_owners["RPTOWNER_RELATIONSHIP"]
    owners = pd.DataFrame({
        "accession_number": raw_owners["ACCESSION_NUMBER"],
        "insider_name": raw_owners["RPTOWNERNAME"],
        "insider_cik": raw_owners["RPTOWNERCIK"].str.lstrip("0"),
        "insider_role": raw_owners["RPTOWNER_TITLE"],
        "is_director": _flag(relationship, "Director"),
        "is_officer": _flag(relationship, "Officer"),
        "is_ten_percent": _flag(relationship, "TenPercentOwner"),
    })
    owners["role_category"] = owners["insider_role"].map(
        lambda title: classify_role(title or ""))

    trans = tables["NONDERIV_TRANS"]
    trans = trans[trans["ACCESSION_NUMBER"].isin(kept)].copy()
    trans = trans[~is_debt(trans)]

    ten_b5_one = _ten_b5_one(trans, filings, tables.get("FOOTNOTES"))

    shares = _numeric(trans["TRANS_SHARES"])
    price = _numeric(trans["TRANS_PRICEPERSHARE"])
    transaction_rows = pd.DataFrame({
        "accession_number": trans["ACCESSION_NUMBER"],
        # Kept so the debt rule above can be audited against the rows it let
        # through, rather than only against the ones it caught.
        "security_title": trans["SECURITY_TITLE"],
        "transaction_date": _iso(trans["TRANS_DATE"]),
        "transaction_code": trans["TRANS_CODE"].str.strip(),
        "shares": shares,
        "price_per_share": price,
        "total_value": shares * price,
        "shares_after": _numeric(trans["SHRS_OWND_FOLWNG_TRANS"]),
        "is_direct": trans["DIRECT_INDIRECT_OWNERSHIP"].str.strip().str.upper() == "D",
        # Nullable boolean, not plain bool, so a quarter that can decide every
        # row and one that cannot decide any write the same parquet type. Left
        # as object, an all-None column lands as a null-typed field and reading
        # a decade of parts back together silently degrades the whole column.
        "is_10b51": ten_b5_one,
    })
    transaction_rows = transaction_rows.merge(owners, on="accession_number",
                                              how="left")
    return filing_rows.reset_index(drop=True), transaction_rows.reset_index(drop=True)
