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

What it does not carry is `is_10b51` before 2023. The checkbox did not exist
until the SEC amended Rule 10b5-1 effective February 2023, so `AFF10B5ONE` is a
column in the 2023 quarters onward and absent from the earlier ones. Older rows
get None, which is the honest answer and the same one `is_routine` gives when
the history cannot decide.

Publication lags the quarter by a month or so. That is irrelevant here: the
database retains 48 months and this exists to reach further back than that.
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from src.ingest.edgar import HEADERS, _throttle
from src.ingest.parser import classify_role

DERA_BASE = ("https://www.sec.gov/files/structureddata/data"
             "/insider-transactions-data-sets")

# The three tables the archive needs. DERA ships seven; footnotes, signatures
# and the derivative tables are not read.
TABLES = ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS")

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
    """The three tables the archive reads, as string frames."""
    with zipfile.ZipFile(zip_path) as archive:
        return {
            name: pd.read_csv(archive.open(f"{name}.tsv"), sep="\t", dtype=str,
                              keep_default_na=False, on_bad_lines="skip")
            for name in TABLES
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

    shares = _numeric(trans["TRANS_SHARES"])
    price = _numeric(trans["TRANS_PRICEPERSHARE"])
    ten_b5_one = trans["AFF10B5ONE"] if "AFF10B5ONE" in trans.columns else None
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
        # Nullable boolean, not plain bool, so the quarters before the checkbox
        # existed and the ones after it write the same parquet type. Left as
        # object, an all-None column lands as a null-typed field and reading a
        # decade of parts back together silently degrades the whole column.
        "is_10b51": pd.array(
            ten_b5_one.str.strip().str.lower().isin(["1", "true", "y"])
            if ten_b5_one is not None else [None] * len(trans),
            dtype="boolean"),
    })
    transaction_rows = transaction_rows.merge(owners, on="accession_number",
                                              how="left")
    return filing_rows.reset_index(drop=True), transaction_rows.reset_index(drop=True)
