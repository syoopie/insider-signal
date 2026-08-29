from datetime import date

from src.signals.formatter import build_evidence, fmt_currency, fmt_pct, format_telegram_message


def test_fmt_currency():
    assert fmt_currency(None) == "N/A"
    assert fmt_currency(2_500_000) == "$2.5M"
    assert fmt_currency(12_000) == "$12K"
    assert fmt_currency(9.5) == "$9.50"


def test_fmt_pct():
    assert fmt_pct(None) == "N/A"
    assert fmt_pct(3.14) == "+3.1%"
    assert fmt_pct(-2) == "-2.0%"


def _scored_tx(name, role, shares, price, tx_date, shares_after=None):
    return {
        "owner": {"name": name, "role_category": role, "role_raw": role.title()},
        "transaction": {
            "shares": shares,
            "price_per_share": price,
            "total_value": shares * price,
            "shares_after": shares_after,
            "transaction_date": tx_date,
        },
        "score_result": {"score": 40, "breakdown": {}},
    }


def test_build_evidence_aggregates_repeat_buyers_and_keeps_shape():
    txs = [
        _scored_tx("Jane Doe", "cfo", 1000, 10.0, "2026-05-01", shares_after=6000),
        _scored_tx("Jane Doe", "cfo", 2000, 11.0, "2026-05-03", shares_after=6000),
    ]
    ev = build_evidence(
        ticker="ACME",
        company_name="Acme Corp",
        score=61,
        signal_type="BUY",
        score_breakdown={"role_cfo": 15, "cap_small": 15},
        cluster_info={"is_cluster": False},
        transactions=txs,
        market_data={"cap_tier": "small", "current_price": 12.0, "price_52wk_low": 11.0},
        filed_date="2026-05-05",
        signal_date=date(2026, 5, 6),
    )
    assert ev["ticker"] == "ACME"
    assert ev["signal_date"] == "2026-05-06"
    assert len(ev["insiders"]) == 1
    ins = ev["insiders"][0]
    assert ins["name"] == "Jane Doe"
    assert ins["shares_bought"] == 3000
    assert ins["purchase_count"] == 2
    # research basis pulled from the factors that fired
    assert any("CFO" in r for r in ev["research_basis"])
    # near-52wk-low derived from market_data
    assert ev["near_52wk_low"] is True


def test_build_evidence_appends_cluster_only_buyers():
    scored = [_scored_tx("A. Scored", "director", 1000, 10.0, "2026-05-01", shares_after=2000)]
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
    ev = build_evidence(
        ticker="ACME", company_name="Acme Corp", score=45, signal_type="CLUSTER_BUY",
        score_breakdown={}, cluster_info=cluster_info, transactions=scored,
        market_data={"cap_tier": "small"}, filed_date="2026-05-02", signal_date=date(2026, 5, 3),
    )
    names = {i["name"] for i in ev["insiders"]}
    assert names == {"A. Scored", "B. Window", "C. Window"}
    window_only = next(i for i in ev["insiders"] if i["name"] == "B. Window")
    assert window_only["in_scoring_window"] is False


def test_format_telegram_message_renders_cluster_header():
    ev = {
        "signal_type": "CLUSTER_BUY", "score": 55, "ticker": "ACME", "company_name": "Acme Corp",
        "cluster": {"is_cluster": True, "insider_count": 3},
        "insiders": [
            {"name": "Jane", "role_raw": "CFO", "total_value": 100000, "shares_bought": 1000,
             "price": 10.0, "transaction_date": "2026-05-01", "in_scoring_window": True, "purchase_count": 1},
        ],
        "score_breakdown": {"role_cfo": 15}, "filed_date": "2026-05-05",
    }
    msg = format_telegram_message(ev)
    assert "CLUSTER BUY — $ACME" in msg
    assert "3 insiders" in msg
