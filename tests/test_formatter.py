from datetime import date

from src.signals.formatter import build_evidence, fmt_currency, fmt_pct, format_telegram_message
from src.signals.scorer import TIMING_FIRST, TIMING_SEQUENCED


def test_fmt_currency():
    assert fmt_currency(None) == "N/A"
    assert fmt_currency(2_500_000) == "$2.5M"
    assert fmt_currency(12_000) == "$12K"
    assert fmt_currency(9.5) == "$9.50"


def test_fmt_pct():
    assert fmt_pct(None) == "N/A"
    assert fmt_pct(3.14) == "+3.1%"
    assert fmt_pct(-2) == "-2.0%"


def _scored_tx(name, role, shares, price, tx_date, shares_after=None, *,
               timing=TIMING_FIRST, is_direct=True, pct_below=40.0):
    return {
        "owner": {"name": name, "role_category": role, "role_raw": role.title()},
        "transaction": {
            "shares": shares,
            "price_per_share": price,
            "total_value": shares * price,
            "shares_after": shares_after,
            "transaction_date": tx_date,
            "is_direct": is_direct,
            "pct_below_52wk_high": pct_below,
        },
        "score_result": {"score": 90, "breakdown": {"discount_rank": 90},
                         "facts": {"timing": timing}},
    }


def _evidence(transactions, cluster_info=None, breakdown=None, signal_type="BUY"):
    return build_evidence(
        ticker="ACME", company_name="Acme Corp", score=90, signal_type=signal_type,
        score_breakdown=breakdown if breakdown is not None else {"discount_rank": 90},
        cluster_info=cluster_info or {"is_cluster": False}, transactions=transactions,
        cap_tier="small", filed_date=date(2026, 5, 5), signal_date=date(2026, 5, 3),
    )


def test_build_evidence_aggregates_repeat_buyers_and_keeps_shape():
    ev = _evidence([
        _scored_tx("Jane Doe", "cfo", 1000, 10.0, "2026-05-01", shares_after=6000),
        _scored_tx("Jane Doe", "cfo", 2000, 11.0, "2026-05-03", shares_after=6000,
                   timing=TIMING_SEQUENCED, is_direct=False, pct_below=45.0),
    ])
    assert ev["ticker"] == "ACME"
    assert ev["signal_date"] == "2026-05-03"
    assert ev["filed_date"] == "2026-05-05"
    assert ev["cap_tier"] == "small"
    [ins] = ev["insiders"]
    assert ins["name"] == "Jane Doe"
    assert ins["shares_bought"] == 3000
    assert ins["purchase_count"] == 2
    assert ins["timing"] == TIMING_SEQUENCED
    assert ins["pct_below_52wk_high"] == 45.0
    assert ins["is_direct"] is False
    assert ev["research_basis"] and "52-week high" in ev["research_basis"][0]


def test_build_evidence_appends_cluster_only_buyers():
    cluster_info = {
        "is_cluster": True,
        "insider_count": 3,
        "insiders": [
            {"insider_name": "A. Scored", "role_category": "director",
             "shares": 1000, "price_per_share": 10.0, "total_value": 10000, "transaction_date": "2026-05-01"},
            {"insider_name": "B. Window", "role_category": "officer",
             "shares": 500, "price_per_share": 9.0, "total_value": 4500, "transaction_date": "2026-04-28"},
            {"insider_name": "C. Window", "role_category": "director",
             "shares": 800, "price_per_share": 9.5, "total_value": 7600, "transaction_date": "2026-04-27"},
        ],
    }
    ev = _evidence([_scored_tx("A. Scored", "director", 1000, 10.0, "2026-05-01", shares_after=2000)],
                   cluster_info=cluster_info, signal_type="CLUSTER_BUY")
    assert {i["name"] for i in ev["insiders"]} == {"A. Scored", "B. Window", "C. Window"}
    window_only = next(i for i in ev["insiders"] if i["name"] == "B. Window")
    assert window_only["in_scoring_window"] is False
    assert any("Cluster" in r for r in ev["research_basis"])


def test_format_telegram_message_renders_cluster_header_and_discount():
    ev = {
        "signal_type": "CLUSTER_BUY", "score": 95, "ticker": "ACME", "company_name": "Acme Corp",
        "cluster": {"is_cluster": True, "insider_count": 3}, "cap_tier": "small",
        "insiders": [
            {"name": "Jane", "role_raw": "CFO", "total_value": 100000, "shares_bought": 1000,
             "price": 10.0, "transaction_date": "2026-05-01", "in_scoring_window": True,
             "purchase_count": 1, "pct_below_52wk_high": 61.4},
        ],
        "score_breakdown": {"discount_rank": 95}, "filed_date": "2026-05-05",
    }
    msg = format_telegram_message(ev)
    assert "CLUSTER BUY — $ACME" in msg
    assert "3 insiders" in msg
    assert "61% below 52-wk high" in msg
