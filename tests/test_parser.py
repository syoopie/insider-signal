import pytest

from src.ingest.parser import classify_role, parse_form4


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Chief Financial Officer", "cfo"),
        ("CFO", "cfo"),
        ("EVP and Treasurer", "cfo"),
        ("Chief Executive Officer", "ceo"),
        ("President and CEO", "ceo"),
        ("Chief Operating Officer", "coo"),
        ("Chairman of the Board", "chairman"),
        ("Director", "director"),
        ("Trustee", "director"),
        ("President", "officer"),
        ("EVP, General Counsel", "officer"),
        ("Secretary", "officer"),
        ("10% Owner", "other"),
        ("", "other"),
    ],
)
def test_classify_role(title, expected):
    assert classify_role(title) == expected


def test_classify_role_specificity_cfo_beats_officer():
    # "Chief Financial Officer" contains "officer" but must resolve to cfo
    assert classify_role("Chief Financial Officer") == "cfo"


MINIMAL_FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0000123456</issuerCik>
    <issuerName>Acme Corp</issuerName>
    <issuerTradingSymbol>acme</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000999888</rptOwnerCik>
      <rptOwnerName>Jane Insider</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-12-05:00</value></transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>12.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_happy_path():
    parsed = parse_form4(MINIMAL_FORM4, {"filed_date": "2026-05-14", "accession_number": "x-1"})
    assert parsed["issuer"] == {"cik": "123456", "ticker": "ACME", "name": "Acme Corp"}
    assert parsed["owner"]["name"] == "Jane Insider"
    assert parsed["owner"]["role_category"] == "cfo"
    assert len(parsed["transactions"]) == 1
    tx = parsed["transactions"][0]
    assert tx["transaction_code"] == "P"
    assert tx["transaction_date"] == "2026-05-12"  # timezone offset stripped
    assert tx["shares"] == 2000.0
    assert tx["price_per_share"] == 12.5
    assert tx["total_value"] == 25_000.0
    assert tx["shares_after"] == 5000.0
    assert tx["is_direct"] is True


def test_debt_reported_on_table_one_is_skipped():
    """
    Notes are reported with the principal amount in both transactionShares and
    transactionPricePerShare, so shares x price is meaningless. MetLife's $10M
    of KYN senior notes was stored as a $100 trillion purchase. Such filings
    report valueOwnedFollowingTransaction instead of shares, which is the tell.
    """
    notes = MINIMAL_FORM4.replace(
        "<transactionShares><value>2000</value></transactionShares>",
        "<transactionShares><value>10000000</value></transactionShares>",
    ).replace(
        "<transactionPricePerShare><value>12.50</value></transactionPricePerShare>",
        "<transactionPricePerShare><value>10000000</value></transactionPricePerShare>",
    ).replace(
        "<sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>",
        "<valueOwnedFollowingTransaction><value>10000000</value></valueOwnedFollowingTransaction>",
    )
    assert parse_form4(notes, {})["transactions"] == []


def test_parse_form4_malformed_returns_empty():
    assert parse_form4("<not-xml", {}) == {}


def test_parse_form4_10b51_footnote_detected():
    xml = MINIMAL_FORM4.replace("</ownershipDocument>", "<footnote>Sale under a Rule 10b5-1 plan</footnote></ownershipDocument>")
    parsed = parse_form4(xml, {})
    assert parsed["transactions"][0]["is_10b51"] is True


def test_aff10b5one_flag_marks_every_transaction():
    """<aff10b5One> is filing-wide; when set the whole filing is plan activity."""
    xml = MINIMAL_FORM4.replace("<nonDerivativeTable>", "<aff10b5One>1</aff10b5One><nonDerivativeTable>")
    assert parse_form4(xml, {})["transactions"][0]["is_10b51"] is True

    clear = MINIMAL_FORM4.replace("<nonDerivativeTable>", "<aff10b5One>0</aff10b5One><nonDerivativeTable>")
    assert parse_form4(clear, {})["transactions"][0]["is_10b51"] is False


def test_10b51_footnote_does_not_leak_across_transactions():
    """
    A filing may pair a 10b5-1 plan sale with an ordinary open-market buy. Only
    the transaction that references the plan footnote is disqualified. Scanning
    the whole document, as this used to, threw away the buy as well.
    """
    plan_sale = """
    <nonDerivativeTransaction>
      <transactionDate><value>2026-05-12</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode><footnoteId id="F1"/></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>20</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>"""
    xml = MINIMAL_FORM4.replace(
        "<nonDerivativeTable>", "<aff10b5One>0</aff10b5One><nonDerivativeTable>" + plan_sale
    ).replace(
        "</ownershipDocument>",
        '<footnotes><footnote id="F1">Effected pursuant to a Rule 10b5-1 trading plan.</footnote></footnotes></ownershipDocument>',
    )
    txs = {t["transaction_code"]: t for t in parse_form4(xml, {})["transactions"]}
    assert txs["S"]["is_10b51"] is True
    assert txs["P"]["is_10b51"] is False
