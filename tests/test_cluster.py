from datetime import date

from src.signals.cluster import cluster_from_transactions

AS_OF = date(2026, 5, 20)


def tx(name, days_before_asof=0, value=50_000, price=10.0, shares=5000,
       role="director", is_direct=True, is_10b51=False):
    from datetime import timedelta
    return {
        "insider_name": name,
        "role_category": role,
        "transaction_date": (AS_OF - timedelta(days=days_before_asof)).isoformat(),
        "total_value": value,
        "price_per_share": price,
        "shares": shares,
        "is_direct": is_direct,
        "is_10b51": is_10b51,
    }


def test_fewer_than_three_insiders_is_not_a_cluster():
    r = cluster_from_transactions([tx("A"), tx("B")], AS_OF)
    assert r["is_cluster"] is False
    assert r["insider_count"] == 2


def test_three_distinct_insiders_in_window_is_a_cluster():
    r = cluster_from_transactions([tx("A", 1), tx("B", 3), tx("C", 6)], AS_OF)
    assert r["is_cluster"] is True
    assert r["insider_count"] == 3


def test_purchases_outside_14_day_window_are_excluded():
    r = cluster_from_transactions([tx("A", 1), tx("B", 3), tx("C", 20)], AS_OF)
    assert r["insider_count"] == 2
    assert r["is_cluster"] is False


def test_indirect_and_small_and_10b51_are_excluded():
    txs = [
        tx("A", 1),
        tx("B", 2),
        tx("C", 3, is_direct=False),
        tx("D", 4, value=10_000),
        tx("E", 5, is_10b51=True),
    ]
    r = cluster_from_transactions(txs, AS_OF)
    assert sorted(i["insider_name"] for i in r["insiders"]) == ["A", "B"]


def test_one_row_per_insider_keeps_first_seen():
    # caller passes rows newest-first; the later (older) duplicate is dropped
    txs = [tx("A", 1, value=90_000), tx("A", 10, value=40_000), tx("B", 2), tx("C", 3)]
    r = cluster_from_transactions(txs, AS_OF)
    assert r["insider_count"] == 3
    a = next(i for i in r["insiders"] if i["insider_name"] == "A")
    assert a["total_value"] == 90_000


def test_identical_block_is_removed():
    # 3 buyers, same shares+price+date -> an allocation block, not independent
    txs = [
        tx("A", 2, shares=1000, price=15.0),
        tx("B", 2, shares=1000, price=15.0),
        tx("C", 2, shares=1000, price=15.0),
        tx("D", 4),
    ]
    r = cluster_from_transactions(txs, AS_OF)
    assert [i["insider_name"] for i in r["insiders"]] == ["D"]
    assert r["is_cluster"] is False


def test_same_price_offering_is_removed():
    # 3 buyers, same price+date, different share counts -> secondary offering
    txs = [
        tx("A", 2, shares=1000, price=18.0),
        tx("B", 2, shares=2500, price=18.0),
        tx("C", 2, shares=900, price=18.0),
        tx("D", 4),
        tx("E", 5),
    ]
    r = cluster_from_transactions(txs, AS_OF)
    assert sorted(i["insider_name"] for i in r["insiders"]) == ["D", "E"]


def test_executive_cluster_flag():
    director_only = cluster_from_transactions(
        [tx("A", 1), tx("B", 2), tx("C", 3)], AS_OF)
    assert director_only["executive_cluster"] is False
    with_cfo = cluster_from_transactions(
        [tx("A", 1, role="cfo"), tx("B", 2), tx("C", 3)], AS_OF)
    assert with_cfo["executive_cluster"] is True


def test_tight_cluster_flag():
    loose = cluster_from_transactions([tx("A", 0), tx("B", 6), tx("C", 12)], AS_OF)
    assert loose["tight_cluster"] is False
    tight = cluster_from_transactions([tx("A", 0), tx("B", 2), tx("C", 4)], AS_OF)
    assert tight["tight_cluster"] is True
