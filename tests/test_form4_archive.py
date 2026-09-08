"""
The two pieces of the archive that can be wrong without anything complaining.

A duplicated accession does not raise; it inflates every count downstream by a
factor nobody notices. A window arithmetic bug does not raise either; it leaves
a gap in the middle of ten years of history that only shows up as a month with
no signals.
"""
from datetime import date

from scripts.build_form4_archive import _windows, to_fetch


def _meta(accession: str, cik: str) -> dict:
    return {"accession_number": accession, "cik_raw": cik}


CIK_MAP = {"0000000001": "AAA", "0000000002": "BBB", "0000000009": "ZZZ"}
UNIVERSE = {"AAA", "BBB"}


def test_a_joint_filing_indexed_once_per_owner_is_fetched_once():
    """
    The bug the first pilot found. EDGAR lists one accession under each
    reporting owner, so the index yields it several times, and the archive
    stored six transactions where the database stored three.
    """
    index = [_meta("0001-26-1", "1"), _meta("0001-26-1", "2"),
             _meta("0001-26-1", "1"), _meta("0002-26-9", "2")]
    got = to_fetch(index, CIK_MAP, UNIVERSE)
    assert [meta["accession_number"] for meta, _t in got] == \
        ["0001-26-1", "0002-26-9"]


def test_the_first_record_for_an_accession_decides_its_ticker():
    index = [_meta("0001-26-1", "1"), _meta("0001-26-1", "2")]
    assert [t for _m, t in to_fetch(index, CIK_MAP, UNIVERSE)] == ["AAA"]


def test_a_filing_outside_the_universe_is_dropped():
    index = [_meta("0003-26-1", "9")]
    assert to_fetch(index, CIK_MAP, UNIVERSE) == []


def test_dropping_an_out_of_universe_filing_does_not_claim_its_accession():
    """
    Order matters here. If the universe check ran after the accession was
    claimed, a filing listed first under an unknown filer and second under a
    known one would be lost entirely.
    """
    index = [_meta("0004-26-1", "9"), _meta("0004-26-1", "1")]
    assert [t for _m, t in to_fetch(index, CIK_MAP, UNIVERSE)] == ["AAA"]


def test_a_record_with_no_accession_is_skipped():
    assert to_fetch([{"cik_raw": "1"}], CIK_MAP, UNIVERSE) == []


def test_an_empty_universe_keeps_everything():
    index = [_meta("0005-26-9", "9")]
    assert len(to_fetch(index, CIK_MAP, set())) == 1


# ── windows ─────────────────────────────────────────────────────────────────

def test_windows_cover_the_range_with_no_gap_and_no_overlap():
    got = _windows(date(2024, 1, 1), date(2024, 1, 20), 7)
    assert got[0][0] == date(2024, 1, 1)
    assert got[-1][1] == date(2024, 1, 20)
    for (_s1, e1), (s2, _e2) in zip(got, got[1:]):
        assert (s2 - e1).days == 1


def test_the_last_window_is_clipped_to_the_end_date():
    got = _windows(date(2024, 1, 1), date(2024, 1, 10), 7)
    assert got == [(date(2024, 1, 1), date(2024, 1, 7)),
                   (date(2024, 1, 8), date(2024, 1, 10))]


def test_a_single_day_range_is_one_window():
    assert _windows(date(2024, 1, 1), date(2024, 1, 1), 7) == \
        [(date(2024, 1, 1), date(2024, 1, 1))]


def test_ten_years_of_weekly_windows_is_the_count_it_should_be():
    got = _windows(date(2016, 1, 1), date(2026, 1, 1), 7)
    assert len(got) == 522   # 3,654 inclusive days is exactly 522 weeks
    assert got[0][0] == date(2016, 1, 1) and got[-1][1] == date(2026, 1, 1)
