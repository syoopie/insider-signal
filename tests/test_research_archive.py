"""
The DuckDB translation of the purchase rollup.

`src/research/archive.py` runs `PURCHASE_ROLLUP_SQL` unchanged against parquet.
The risk is not that the query fails, it is that it succeeds and means something
slightly different, because the archive has no serial filing id and the two
engines break ties their own way. Both behaviours the rollup exists for are
pinned here: broker fills of one decision are totalled, and a joint filing's
repeated rows are not.
"""
from datetime import date

import pandas as pd
import pytest

from src.research.archive import connect, purchases


def _archive(tmp_path, filings, transactions):
    for kind, rows in (("filings", filings), ("transactions", transactions)):
        directory = tmp_path / kind
        directory.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(directory / "part-2024q1.parquet", index=False)
    return tmp_path


FILING = {"accession_number": "0001-24-1", "cik": "910638", "ticker": "DDD",
          "company_name": "3D SYSTEMS", "filed_date": date(2024, 3, 4),
          "period_date": date(2024, 3, 1)}


def _fill(shares, price, accession="0001-24-1", name="A BUYER", direct=True):
    return {"accession_number": accession, "insider_name": name,
            "insider_cik": "1879982", "insider_role": "CFO",
            "role_category": "cfo", "is_director": False, "is_officer": True,
            "is_ten_percent": False, "security_title": "Common Stock",
            "transaction_date": date(2024, 3, 1), "transaction_code": "P",
            "shares": shares, "price_per_share": price,
            "total_value": shares * price, "shares_after": 10_000.0,
            "is_10b51": None, "is_direct": direct}


def test_broker_fills_of_one_decision_are_totalled(tmp_path):
    """
    The bug the rollup was extracted to fix. Keeping one arbitrary fill hid
    $4.1B of purchase value across the stored history.
    """
    archive = _archive(tmp_path, [FILING],
                       [_fill(100.0, 10.0), _fill(200.0, 11.0), _fill(300.0, 12.0)])
    got = purchases(connect(archive))
    assert len(got) == 1
    assert got["shares"][0] == 600.0
    assert got["total_value"][0] == pytest.approx(100 * 10 + 200 * 11 + 300 * 12)


def test_the_rolled_up_price_is_value_weighted(tmp_path):
    archive = _archive(tmp_path, [FILING], [_fill(100.0, 10.0), _fill(300.0, 20.0)])
    got = purchases(connect(archive))
    assert got["price_per_share"][0] == pytest.approx(7000.0 / 400.0)


def test_a_joint_filings_repeated_rows_are_not_multiplied(tmp_path):
    """
    Alyeska's six affiliated funds filed one purchase as six identical Table I
    lines. Only the first reporting owner is kept, so all six land under one
    name and totalling them would sextuple a real purchase. Identical shares
    and price inside one filing is that pattern; genuine fills differ.
    """
    archive = _archive(tmp_path, [FILING], [_fill(135135.0, 7.4)] * 6)
    got = purchases(connect(archive))
    assert got["shares"][0] == 135135.0


def test_direct_and_indirect_stay_separate(tmp_path):
    """Different holdings, not tranches of one order."""
    archive = _archive(tmp_path, [FILING],
                       [_fill(100.0, 10.0), _fill(50.0, 10.0, direct=False)])
    got = purchases(connect(archive)).sort_values("is_direct")
    assert got["shares"].tolist() == [50.0, 100.0]


def test_an_amendment_replaces_rather_than_adds(tmp_path):
    """
    A 4/A restates the original under a new accession. Summing both would
    double the purchase, so the newest filing wins outright.
    """
    amendment = dict(FILING, accession_number="0001-24-2",
                     filed_date=date(2024, 3, 6))
    archive = _archive(tmp_path, [FILING, amendment],
                       [_fill(100.0, 10.0),
                        _fill(140.0, 10.0, accession="0001-24-2")])
    got = purchases(connect(archive))
    assert len(got) == 1 and got["shares"][0] == 140.0


def test_columns_the_archive_cannot_supply_come_back_null(tmp_path):
    """
    Not a gap to be filled with a default. `discount_score` returns None without
    pct_below_52wk_high, and a caller substituting the median would place an
    unrankable purchase in WATCH on no evidence.
    """
    got = purchases(connect(_archive(tmp_path, [FILING], [_fill(100.0, 10.0)])))
    for column in ("is_routine", "pct_below_52wk_high", "cap_tier"):
        assert got[column].isna().all()


def test_only_purchases_come_back(tmp_path):
    sale = dict(_fill(100.0, 10.0), transaction_code="S")
    archive = _archive(tmp_path, [FILING], [_fill(100.0, 10.0), sale])
    got = purchases(connect(archive))
    assert got["transaction_code"].tolist() == ["P"]
