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


# ── price context ───────────────────────────────────────────────────────────

def test_price_context_is_filled_from_the_panel(tmp_path):
    """
    The archive rollup leaves the price columns NULL because they are computed at
    ingest. This fills them by calling `context_from_series`, the same function
    the ingest path calls, so there is one definition of the 52-week high rather
    than two. Measured against what ingest actually stored, 8,907 of 8,909
    overlapping purchases agree to 0.01pp.
    """
    import numpy as np

    from src.market.panel import PanelSeries
    from src.research.archive import with_price_context

    # Flat at 50, with two spikes. The 200 sits far outside the trailing
    # 52 weeks and the 80 sits well inside it, so a window that reached back
    # over all available history would answer 200 and this answers 80.
    days = pd.date_range("2023-01-02", periods=400, freq="B")
    close = np.full(400, 50.0)
    close[10] = 200.0
    close[300] = 80.0
    series = PanelSeries(symbol="DDD", dates=days.to_numpy(dtype="datetime64[D]"),
                         close=close, adj_close=close,
                         volume=np.full(400, 1e6))

    archive = _archive(tmp_path, [FILING], [_fill(100.0, 10.0)])
    frame = purchases(connect(archive))
    frame["transaction_date"] = [days[350].date()]
    got = with_price_context(frame, {"DDD": series})

    assert got["price_context_bars"][0] >= 200
    assert got["px_close_at_tx"][0] == pytest.approx(50.0)
    assert got["px_52wk_high"][0] == pytest.approx(80.0)
    assert got["pct_below_52wk_high"][0] == pytest.approx(37.5)


def test_a_ticker_the_panel_does_not_cover_stays_null(tmp_path):
    """
    None rather than a partial answer. An unrankable purchase scores 0 and is
    never alerted, which is the conservative failure; a substituted median would
    place it in WATCH on no evidence.
    """
    from src.research.archive import with_price_context

    frame = purchases(connect(_archive(tmp_path, [FILING], [_fill(100.0, 10.0)])))
    got = with_price_context(frame, {})
    assert got["pct_below_52wk_high"].isna().all()
    assert got["px_52wk_high"].isna().all()


def test_too_little_history_before_the_date_stays_null(tmp_path):
    """
    A 52-week high needs a year behind it. Over 40 bars it is the high of the
    last two months wearing the wrong name.
    """
    import numpy as np

    from src.market.panel import PanelSeries
    from src.research.archive import with_price_context

    days = pd.date_range("2024-02-01", periods=40, freq="B")
    series = PanelSeries(symbol="DDD", dates=days.to_numpy(dtype="datetime64[D]"),
                         close=np.linspace(100.0, 90.0, 40),
                         adj_close=np.linspace(100.0, 90.0, 40),
                         volume=np.full(40, 1e6))

    frame = purchases(connect(_archive(tmp_path, [FILING], [_fill(100.0, 10.0)])))
    frame["transaction_date"] = [days[-1].date()]
    got = with_price_context(frame, {"DDD": series})
    assert got["pct_below_52wk_high"].isna().all()
    assert got["price_context_bars"][0] < 200
