"""
A ticker EDGAR accepted is not necessarily a ticker anything can look up.

Filers type the issuer ticker by hand. Five companies in the database carry
values no price API resolves — '(CALX)', 'N O G', 'NYSE/TRN', 'BFA, BFB',
'WLY, WLYB' — so those companies have no market cap, no price context, and their
purchases cannot be labelled at all.
"""
import pytest

from src.ingest.common import _clean_ticker


@pytest.mark.parametrize("raw,expected", [
    ("AAPL", "AAPL"),
    ("  aapl  ", "AAPL"),
    ("(CALX)", "CALX"),
    ("N O G", "NOG"),
    ("NYSE/TRN", "TRN"),
    ("NASDAQ:SVC", "SVC"),
    ("BRK.B", "BRK.B"),
    ("BF-A", "BF-A"),
])
def test_unambiguous_noise_is_stripped(raw, expected):
    assert _clean_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["BFA, BFB", "WLY, WLYB"])
def test_two_tickers_in_one_field_are_refused_not_guessed(raw):
    """
    Brown-Forman files both share classes. Taking the first would file its
    insider purchases under BFA, an unrelated ETF. A missing ticker costs one
    company's signals; a wrong one corrupts another company's.
    """
    assert _clean_ticker(raw) is None


@pytest.mark.parametrize("raw", ["", "   ", "NONE", "N/A", "NULL", "na"])
def test_sentinels_are_not_tickers(raw):
    assert _clean_ticker(raw) is None


@pytest.mark.parametrize("raw", ["TOOLONGTICKER", "AB CD, EF", "###"])
def test_anything_still_unparseable_is_refused(raw):
    assert _clean_ticker(raw) is None


def test_none_and_empty_are_handled():
    assert _clean_ticker(None) is None
    assert _clean_ticker("") is None
