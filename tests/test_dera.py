"""
The DERA reader, which is now the only way history enters the archive.

Every failure here is silent. A dropped quarter is a thinner sample, a misread
relationship column is a wrong role on a real insider, and a date parsed as NaT
lands as a null the scorer skips. None of them raise, so they have to be caught
by assertion instead.
"""
from datetime import date

import pandas as pd
import pytest

from src.ingest.dera import quarter_url, quarters_between, to_archive_rows


def _tables(submission_rows, owner_rows, trans_rows, with_10b51=False,
            title="Common Stock"):
    submission = pd.DataFrame(submission_rows, columns=[
        "ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE",
        "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"])
    owners = pd.DataFrame(owner_rows, columns=[
        "ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
        "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
    columns = ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
               "TRANS_PRICEPERSHARE", "SHRS_OWND_FOLWNG_TRANS",
               "VALU_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP"]
    if with_10b51:
        columns.append("AFF10B5ONE")
    transactions = pd.DataFrame(trans_rows, columns=columns)
    transactions.insert(1, "SECURITY_TITLE", title)
    return {
        "SUBMISSION": submission,
        "REPORTINGOWNER": owners,
        "NONDERIV_TRANS": transactions,
    }


SUB = [["0001-22-1", "31-AUG-2022", "29-AUG-2022", "4", "0000910638",
        "3D SYSTEMS CORP", "DDD"]]
OWN = [["0001-22-1", "0001879982", "Nordstrom Phyllis B", "Director", "Director"]]
TRX = [["0001-22-1", "29-AUG-2022", "P", "1000.0", "12.50", "5000.0", "", "D"]]


def test_quarters_span_a_year_boundary():
    assert quarters_between(date(2022, 11, 1), date(2023, 2, 1)) == \
        [(2022, 4), (2023, 1)]


def test_a_range_inside_one_quarter_is_one_quarter():
    assert quarters_between(date(2022, 7, 5), date(2022, 8, 9)) == [(2022, 3)]


def test_ten_years_is_the_quarter_count_it_should_be():
    got = quarters_between(date(2016, 1, 1), date(2024, 9, 2))
    assert len(got) == 35 and got[0] == (2016, 1) and got[-1] == (2024, 3)


def test_the_url_names_the_quarter():
    assert quarter_url(2022, 3).endswith("2022q3_form345.zip")


def test_form_3_and_form_5_are_not_form_4():
    """
    The dataset is Form 3, 4 and 5 together. A Form 3 is an initial statement of
    holdings with no transaction, and counting one as a purchase would invent a
    trade that never happened.
    """
    rows = SUB + [["0002-22-1", "31-AUG-2022", "29-AUG-2022", "3", "0000910638",
                   "3D SYSTEMS CORP", "DDD"],
                  ["0003-22-1", "31-AUG-2022", "29-AUG-2022", "4/A",
                   "0000910638", "3D SYSTEMS CORP", "DDD"]]
    filings, _tx = to_archive_rows(_tables(rows, OWN, TRX), set())
    assert sorted(filings["accession_number"]) == ["0001-22-1", "0003-22-1"]


def test_a_ticker_outside_the_universe_is_dropped_with_its_transactions():
    filings, transactions = to_archive_rows(_tables(SUB, OWN, TRX), {"AAPL"})
    assert filings.empty and transactions.empty


def test_an_empty_universe_keeps_everything():
    filings, _tx = to_archive_rows(_tables(SUB, OWN, TRX), set())
    assert len(filings) == 1


def test_dera_dates_become_iso_dates():
    filings, transactions = to_archive_rows(_tables(SUB, OWN, TRX), set())
    assert filings["filed_date"][0] == date(2022, 8, 31)
    assert filings["period_date"][0] == date(2022, 8, 29)
    assert transactions["transaction_date"][0] == date(2022, 8, 29)


def test_total_value_is_shares_times_price():
    _f, transactions = to_archive_rows(_tables(SUB, OWN, TRX), set())
    assert transactions["total_value"][0] == pytest.approx(12_500.0)


def test_indirect_ownership_is_not_direct():
    indirect = [["0001-22-1", "29-AUG-2022", "P", "1000.0", "12.50", "5000.0", "", "I"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, indirect), set())
    assert bool(transactions["is_direct"][0]) is False


@pytest.mark.parametrize("relationship,expected", [
    ("Director", (True, False, False)),
    ("Officer", (False, True, False)),
    ("TenPercentOwner", (False, False, True)),
    ("Director,Officer", (True, True, False)),
    # 152 rows in 2022Q3 alone run two relationships together with no
    # separator, which is why the flags are substring tests and not a split.
    ("Director,TenPercentOwnerOther", (True, False, True)),
])
def test_the_relationship_set_becomes_three_flags(relationship, expected):
    owners = [["0001-22-1", "0001879982", "A Person", relationship, "Some Title"]]
    _f, transactions = to_archive_rows(_tables(SUB, owners, TRX), set())
    got = (bool(transactions["is_director"][0]),
           bool(transactions["is_officer"][0]),
           bool(transactions["is_ten_percent"][0]))
    assert got == expected


def test_only_the_first_reporting_owner_is_kept():
    """
    Matches `parse_form4`, which records only the first `<reportingOwner>`. A
    joint Form 4 is one decision under several names, and counting it as several
    would inflate every cluster it appears in.
    """
    owners = OWN + [["0001-22-1", "0002", "Second Owner", "Officer", "CFO"]]
    _f, transactions = to_archive_rows(_tables(SUB, owners, TRX), set())
    assert list(transactions["insider_name"]) == ["Nordstrom Phyllis B"]


def test_before_2023_there_is_no_10b5_1_column_and_the_answer_is_unknown():
    """
    The checkbox did not exist until the SEC amended Rule 10b5-1 effective
    February 2023. None is the honest answer for older filings; False would
    assert every pre-2023 trade was opportunistic.
    """
    _f, transactions = to_archive_rows(_tables(SUB, OWN, TRX), set())
    assert transactions["is_10b51"].isna().all()
    assert transactions["is_10b51"].dtype.name == "boolean"


def test_the_10b5_1_column_has_one_type_either_side_of_2023():
    """
    Parquet parts are read back a decade at a time. An all-None object column
    writes as a null-typed field, and concatenating that with a real boolean one
    degrades the whole column to object without complaining.
    """
    _f, before = to_archive_rows(_tables(SUB, OWN, TRX), set())
    after_rows = [["0001-22-1", "29-AUG-2022", "P", "1000.0", "12.50", "5000.0",
                   "", "D", "1"]]
    _f2, after = to_archive_rows(_tables(SUB, OWN, after_rows, with_10b51=True), set())
    assert before["is_10b51"].dtype == after["is_10b51"].dtype


def test_from_2023_the_10b5_1_checkbox_is_read():
    rows = [["0001-22-1", "29-AUG-2022", "P", "1000.0", "12.50", "5000.0", "", "D", "1"],
            ["0001-22-1", "29-AUG-2022", "P", "500.0", "12.50", "5500.0", "", "D", "0"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, rows, with_10b51=True), set())
    assert list(transactions["is_10b51"]) == [True, False]


def test_a_debt_filing_is_not_a_share_purchase():
    """
    Table I can carry notes. The filer puts the principal amount in both the
    share count and the price, so shares times price is a number in the
    billions that means nothing. Those rows report a value owned rather than a
    share count.
    """
    debt = [["0001-22-1", "29-AUG-2022", "P", "250000000.0", "50.0", "",
             "12527214383.0", "D"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, debt), set())
    assert transactions.empty


def test_a_debt_row_that_also_reports_a_share_count_is_still_debt():
    """
    The shape that actually occurs, and the one an earlier filter missed. This
    is a real 2024Q3 MetLife row: notes whose principal sits in both the share
    count and the price, reported alongside a share holding. Twenty-two rows
    like it carried $144 trillion of the archive's $144.5 trillion.
    """
    both = [["0001-22-1", "18-SEP-2024", "P", "9600000.0", "9600000.0",
             "280000.0", "9600000.0", "I"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, both), set())
    assert transactions.empty


def test_a_preferred_share_purchase_is_not_mistaken_for_debt():
    """The same filing's real equity leg, which reports no value owned."""
    preferred = [["0001-22-1", "18-SEP-2024", "P", "280000.0", "25.0",
                  "280000.0", "", "I"]]
    tables = _tables(SUB, OWN, preferred,
                     title="Series X Mandatory Redeemable Preferred Shares")
    _f, transactions = to_archive_rows(tables, set())
    assert transactions["total_value"].tolist() == [7_000_000.0]


def test_notes_are_debt_even_when_the_value_column_is_blank():
    """
    The shape the value column alone misses. MetLife's 2022Q3 filing leaves
    VALU_OWND_FOLWNG_TRANS empty on the very row that needs it, and reports
    3,000,000 notes at $3,000,000 each. That single row is $9 trillion.
    """
    notes = [["0001-22-1", "02-AUG-2022", "P", "3000000.0", "3000000.0", "", "", "I"]]
    tables = _tables(SUB, OWN, notes,
                     title="4.67% Series SS Senior Unsecured Notes due August 2, 2034")
    _f, transactions = to_archive_rows(tables, set())
    assert transactions.empty


@pytest.mark.parametrize("title", [
    "6.6875% Notes due 2028",
    "3.18% Senior Notes, Series N, due December 13, 2024",
    "Subordinated Debenture",
    "8.00% Bonds due 2031",
])
def test_debt_titles_are_recognised(title):
    rows = [["0001-22-1", "02-AUG-2022", "P", "1000.0", "1000.0", "", "", "D"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, rows, title=title), set())
    assert transactions.empty


@pytest.mark.parametrize("title", [
    "Common Stock",
    "Class A Common Stock",
    "Series X Mandatory Redeemable Preferred Shares",
    # The word appears inside another, which a substring test would catch and a
    # word-boundary test must not.
    "Denoted Class B Units",
])
def test_an_equity_title_is_not_debt(title):
    rows = [["0001-22-1", "02-AUG-2022", "P", "100.0", "10.0", "100.0", "", "D"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, rows, title=title), set())
    assert len(transactions) == 1


def test_a_transaction_with_no_matching_filing_is_dropped():
    """The tables are quarter-wide; a filing filtered out must take its rows with it."""
    stray = TRX + [["9999-22-9", "29-AUG-2022", "P", "1.0", "1.0", "1.0", "", "D"]]
    _f, transactions = to_archive_rows(_tables(SUB, OWN, stray), set())
    assert list(transactions["accession_number"]) == ["0001-22-1"]
